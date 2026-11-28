"""知识库：把用户材料存进向量库，按意思检索。

向量库是从 documents 表派生出来的数据，不是另一份需要维护的状态：
每次检索前先对齐（sync），库里只保留各类材料当前版本的片段。删掉向量目录也没关系，会自动重建。
"""

import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from langchain_core.documents import Document as VectorDocument
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.db.models import DOCUMENT_KIND_NAMES, Document, DocumentKind
from app.rag.chunking import split_markdown
from app.rag.rerank import Reranker
from app.rag.workspace import WorkspaceIndex

logger = logging.getLogger(__name__)

COLLECTION = "materials"
# RRF（Reciprocal Rank Fusion，倒数排名融合）的常数。多个查询的结果按名次合并：
# 一个片段在某个查询里排第 r 名，就得 1 / (RRF_K + r) 分，各查询的得分相加。
# 只看名次不看分数，因为重排模型的分数高低不稳定（相关的片段有时只有 0.002），名次却很准。
RRF_K = 60


@dataclass(frozen=True)
class Passage:
    """检索到的一个片段，以及它的出处。"""

    document_id: int
    kind: DocumentKind
    heading: str
    text: str
    score: float  # recall 返回余弦相似度；search 返回重排模型给的相关度（0–1），只适合比较先后
    matched: tuple[str, ...] = ()  # search：这个片段是为哪些查询找到的

    @property
    def source(self) -> str:
        name = DOCUMENT_KIND_NAMES[self.kind]
        return f"{name} > {self.heading}" if self.heading else name


class KnowledgeBase:
    def __init__(
        self, engine: Engine, embeddings: Embeddings, reranker: Reranker, vectors_dir: Path
    ) -> None:
        self.engine = engine
        self.embeddings = embeddings
        self.reranker = reranker
        self.vectors_dir = vectors_dir
        self._client: QdrantClient | None = None
        self.lock = threading.Lock()  # 本地模式的 Qdrant 一次只能有一个操作
        # 全局检索（材料、岗位、沟通、成果）另存一个集合，和这里共用连接和锁
        self.workspace = WorkspaceIndex(self)

    def client(self) -> QdrantClient:
        """本地模式：不用另起服务，数据就是 vectors_dir 下的文件。"""
        if self._client is None:
            self._client = QdrantClient(path=str(self.vectors_dir))
        return self._client

    def _store(self) -> QdrantVectorStore:
        client = self.client()
        if not client.collection_exists(COLLECTION):
            size = len(self.embeddings.embed_query("向量维度"))
            self._client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
            )
        return QdrantVectorStore(
            client=self._client, collection_name=COLLECTION, embedding=self.embeddings
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self.workspace = WorkspaceIndex(self)

    def _sync(self, store: QdrantVectorStore) -> None:
        """让向量库和各类材料的当前版本对齐：缺的补上，过期的删掉。"""
        with Session(self.engine) as session:
            current = session.exec(select(Document).where(col(Document.is_current).is_(True))).all()
        wanted = {document.id: document for document in current}

        indexed: set[int] = set()
        offset = None
        while True:
            points, offset = store.client.scroll(
                COLLECTION,
                limit=256,
                offset=offset,
                with_payload=["metadata.document_id"],
                with_vectors=False,
            )
            indexed.update(point.payload["metadata"]["document_id"] for point in points)
            if offset is None:
                break

        stale = indexed - wanted.keys()
        if stale:
            store.client.delete(COLLECTION, points_selector=_where(document_ids=stale))
        for document_id in wanted.keys() - indexed:
            document = wanted[document_id]
            chunks = [
                VectorDocument(
                    # 标题一起参与向量化：「项目经历 > 客服助手」本身就是重要的语义。
                    page_content=f"{chunk.heading}\n{chunk.text}" if chunk.heading else chunk.text,
                    metadata={
                        "document_id": document.id,
                        "kind": document.kind,
                        "heading": chunk.heading,
                        "text": chunk.text,
                    },
                )
                for chunk in split_markdown(document.content_md)
            ]
            if chunks:
                store.add_documents(chunks)
            logger.info(
                "知识库：%s 第 %s 版入库，%s 个片段", document.kind, document.id, len(chunks)
            )

    @staticmethod
    def _recall(
        store: QdrantVectorStore, query: str, kinds: list[DocumentKind] | None, limit: int
    ) -> list[Passage]:
        """召回：找出向量最接近查询的片段。分数是余弦相似度，只用来排序。"""
        found = store.similarity_search_with_score(
            query, k=limit, filter=_where(kinds=kinds) if kinds else None
        )
        return [
            Passage(
                document_id=document.metadata["document_id"],
                kind=DocumentKind(document.metadata["kind"]),
                heading=document.metadata["heading"],
                text=document.metadata["text"],
                score=score,
            )
            for document, score in found
        ]

    def search(
        self,
        queries: list[str],
        *,
        kinds: list[DocumentKind] | None = None,
        per_query: int = 6,
        keep: int = 2,
        limit: int = 12,
    ) -> list[Passage]:
        """检索：每个查询各自召回 per_query 个候选，重排后留下前 keep 名，再用 RRF 合并。

        返回的片段按合并后的名次排列，每个片段记着它是为哪些查询找到的。
        材料不多时几乎都会返回，材料多了才靠名次取舍。
        """
        found: dict[tuple, Passage] = {}
        fused: dict[tuple, float] = {}
        with self.lock:
            store = self._store()
            self._sync(store)
            for query in queries:
                candidates = self._recall(store, query, kinds, per_query)
                if not candidates:
                    continue
                scores = self.reranker.score(
                    query, [f"{passage.heading}\n{passage.text}" for passage in candidates]
                )
                ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
                for rank, (passage, score) in enumerate(ranked[:keep], start=1):
                    key = (passage.document_id, passage.heading, passage.text)
                    known = found.get(key)
                    fused[key] = fused.get(key, 0.0) + 1 / (RRF_K + rank)
                    found[key] = replace(
                        passage,
                        score=max(score, known.score) if known else score,
                        matched=(*(known.matched if known else ()), query),
                    )
        best = sorted(found, key=lambda key: fused[key], reverse=True)[:limit]
        return [found[key] for key in best]


def _where(*, document_ids: set[int] | None = None, kinds: list[DocumentKind] | None = None):
    conditions = []
    if document_ids is not None:
        conditions.append(
            models.FieldCondition(
                key="metadata.document_id", match=models.MatchAny(any=sorted(document_ids))
            )
        )
    if kinds is not None:
        conditions.append(
            models.FieldCondition(
                key="metadata.kind", match=models.MatchAny(any=[str(kind) for kind in kinds])
            )
        )
    return models.Filter(must=conditions)
