"""全局检索：在你的材料、岗位、沟通记录和全部成果里一起找，给检索页和助手用。

和材料检索（app/rag/knowledge.py）分开存：定制简历、面试准备、评估找证据只用你的材料。
岗位 JD 和 AI 写的报告混进证据，模型可能把 JD 的要求、报告的推断当成你的经历。

向量库里的片段是从数据库派生的：每次检索前按内容指纹对齐，新的补上、改过的重建、删掉的去掉。
检索时两路一起找再合并：按意思（向量）和按字面（关键词）。「Kafka」这类词字面更准，
「做 RAG 的那家公司」这类说法按意思更好。合并后用重排模型排一次先后。
"""

import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from uuid import uuid4

from qdrant_client import QdrantClient, models
from sqlmodel import Session, col, select

from app.db.models import (
    ARTIFACT_KIND_NAMES,
    DOCUMENT_KIND_NAMES,
    Artifact,
    Communication,
    Document,
    DocumentKind,
    Job,
)
from app.rag.chunking import split_markdown

logger = logging.getLogger(__name__)

COLLECTION = "workspace"
TYPE_NAMES = {
    "material": "材料",
    "job": "岗位",
    "communication": "沟通记录",
    "artifact": "成果",
    "chat": "助手对话",
}
CHAT = "chat:"  # 助手对话的记录编号前缀。对话在 Checkpoint 里，由检索接口读出来交给这里
SPEAKERS = {"recruiter": "招聘方消息", "me": "我的消息", "transcript": "聊天记录"}
RRF_K = 60  # 和材料检索同一个常数，见 app/rag/knowledge.py
CANDIDATES = 30  # 每一路各取前几名
RERANK = 16  # 合并后取前几名交给重排模型。重排在本机跑，这一步最慢
PER_ITEM = 2  # 同一条记录（一个岗位、一份成果）最多出几个片段
_ASCII = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.\-]*")
_CJK = re.compile(r"[一-鿿]+")


@dataclass(frozen=True)
class Item:
    """要入库的一条记录。"""

    key: str  # 例如 job:3、artifact:12
    type: str  # material、job、communication、artifact
    kind: str  # 材料种类、成果种类、沟通里谁说的；岗位是 jd
    title: str
    date: str
    link: str  # 前端的地址
    text: str
    job_id: int | None = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.text}".encode()).hexdigest()


@dataclass(frozen=True)
class Hit:
    """找到的一个片段。highlights 是 text 里和查询字面相同的位置（开始，结束），给前端加亮。"""

    type: str
    kind: str
    title: str
    heading: str
    text: str
    date: str
    link: str
    job_id: int | None
    highlights: tuple[tuple[int, int], ...]
    literal: bool  # 标题或正文里有和查询字面相同的词；没有就是只按意思找到的


def _job_name(job: Job) -> str:
    return f"{job.company or '未填公司'} — {job.title or '未填职位'}"


def collect_items(session: Session) -> list[Item]:
    """数据库里现在要能搜到的全部记录。删掉的岗位，连同它的沟通记录和成果都不算。"""
    items = []
    for document in session.exec(select(Document).where(col(Document.is_current).is_(True))):
        kind = DocumentKind(document.kind)
        items.append(
            Item(
                key=f"material:{kind}",
                type="material",
                kind=kind,
                title=DOCUMENT_KIND_NAMES[kind],
                date=document.created_at,
                link="/materials" if kind == DocumentKind.resume else f"/materials?kind={kind}",
                text=document.content_md,
            )
        )
    jobs = {job.id: job for job in session.exec(select(Job).where(col(Job.deleted_at).is_(None)))}
    for job in jobs.values():
        facts = " · ".join(part for part in (job.city, job.salary) if part)
        items.append(
            Item(
                key=f"job:{job.id}",
                type="job",
                kind="jd",
                title=_job_name(job),
                date=job.created_at,
                link=f"/jobs/{job.id}",
                text="\n".join(part for part in (facts, job.jd_text.strip()) if part),
                job_id=job.id,
            )
        )
    for record in session.exec(select(Communication)):
        job = jobs.get(record.job_id)
        if job is None:
            continue
        items.append(
            Item(
                key=f"communication:{record.id}",
                type="communication",
                kind=record.speaker,
                title=f"{_job_name(job)} · {SPEAKERS.get(record.speaker, record.speaker)}",
                date=record.created_at,
                link=f"/jobs/{job.id}?tab=communication",
                text=record.text,
                job_id=job.id,
            )
        )
    for artifact in session.exec(select(Artifact).where(col(Artifact.deleted_at).is_(None))):
        if artifact.job_id is not None and artifact.job_id not in jobs:
            continue
        name = ARTIFACT_KIND_NAMES.get(artifact.kind, artifact.kind)
        about = _job_name(jobs[artifact.job_id]) if artifact.job_id else artifact.title
        items.append(
            Item(
                key=f"artifact:{artifact.id}",
                type="artifact",
                kind=artifact.kind,
                title=f"{name} · {about}" if about else name,
                date=artifact.updated_at,
                link=f"/artifacts/{artifact.id}",
                text=artifact.content_md,
                job_id=artifact.job_id,
            )
        )
    return [item for item in items if item.text.strip() or item.type == "job"]


def query_terms(query: str) -> list[str]:
    """查询里按字面找的词：英文和数字整词（不分大小写），中文每相邻两个字一组；单个字不算。"""
    terms = [word.lower() for word in _ASCII.findall(query) if len(word) >= 2]
    for run in _CJK.findall(query):
        terms += [run[i : i + 2] for i in range(len(run) - 1)]
    return list(dict.fromkeys(terms))


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKS = re.compile(r"\*\*|__|`")
_LINE_START = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?)", re.MULTILINE)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)


def plain_text(text: str) -> str:
    """摘要按纯文字显示：链接只留文字，加粗、代码、标题和引用的记号去掉，列表换成圆点。
    原文仍按 Markdown 切块、算向量，这里只处理给人看（和给助手看）的那段。"""
    text = _LINK.sub(r"\1", text)
    text = _MARKS.sub("", text)
    text = _LINE_START.sub("", text)
    return _BULLET.sub(r"\1· ", text)


def highlights(text: str, terms: list[str]) -> tuple[tuple[int, int], ...]:
    """text 里所有和 terms 字面相同的位置，相邻、重叠的合成一段。"""
    lowered = text.lower()
    spans = []
    for term in terms:
        start = lowered.find(term)
        while start >= 0:
            spans.append((start, start + len(term)))
            start = lowered.find(term, start + 1)
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


class WorkspaceIndex:
    """knowledge：材料检索的知识库。向量库的连接、锁、Embedding 和重排模型都和它共用。"""

    def __init__(self, knowledge) -> None:
        self.knowledge = knowledge
        self._chunks: dict[str, dict] = {}  # 点编号 → 片段，按字面找时用
        self._loaded = False

    def _client(self) -> QdrantClient:
        client = self.knowledge.client()
        if not client.collection_exists(COLLECTION):
            size = len(self.knowledge.embeddings.embed_query("向量维度"))
            client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
            )
            self._chunks, self._loaded = {}, False
        return client

    def _load(self, client: QdrantClient) -> None:
        self._chunks = {}
        offset = None
        while True:
            points, offset = client.scroll(
                COLLECTION, limit=512, offset=offset, with_payload=True, with_vectors=False
            )
            self._chunks.update({str(point.id): point.payload for point in points})
            if offset is None:
                break
        self._loaded = True

    def forget(self, key: str) -> None:
        """立刻去掉一条记录的全部片段（删除对话时用）。助手和 MCP 的检索不重新对齐对话，
        不在这里删掉的话，删了的对话还能搜到，点开是 404。"""
        with self.knowledge.lock:
            client = self._client()
            client.delete(
                COLLECTION,
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="key", match=models.MatchValue(value=key))]
                ),
            )
            self._chunks = {pid: c for pid, c in self._chunks.items() if c["key"] != key}

    def _sync(self, client: QdrantClient, chats: list[Item] | None) -> int:
        """和数据库对齐，返回新入库的片段数。chats 是助手对话；不传时对话部分保持原样。"""
        with Session(self.knowledge.engine) as session:
            wanted = {item.key: item for item in collect_items(session)}
        wanted |= {item.key: item for item in chats or []}

        def managed(key: str) -> bool:
            return chats is not None or not key.startswith(CHAT)

        if not self._loaded:
            self._load(client)
        indexed = {chunk["key"]: chunk["fingerprint"] for chunk in self._chunks.values()}
        # 删掉了的、内容改过的：旧片段都去掉，改过的下面重新入库
        stale = {
            key
            for key, fp in indexed.items()
            if managed(key) and (key not in wanted or wanted[key].fingerprint != fp)
        }
        if stale:
            client.delete(
                COLLECTION,
                points_selector=models.Filter(
                    must=[
                        models.FieldCondition(key="key", match=models.MatchAny(any=sorted(stale)))
                    ]
                ),
            )
            self._chunks = {pid: c for pid, c in self._chunks.items() if c["key"] not in stale}
        fresh = [item for key, item in wanted.items() if key not in indexed or key in stale]
        payloads = []
        for item in fresh:
            pieces = split_markdown(item.text) or [None]
            for piece in pieces:
                payloads.append(
                    {
                        "key": item.key,
                        "fingerprint": item.fingerprint,
                        "type": item.type,
                        "kind": item.kind,
                        "title": item.title,
                        "heading": piece.heading if piece else "",
                        "text": piece.text if piece else "",
                        "date": item.date,
                        "link": item.link,
                        "job_id": item.job_id,
                    }
                )
        if payloads:
            vectors = self.knowledge.embeddings.embed_documents(
                ["\n".join(p for p in (c["title"], c["heading"], c["text"]) if p) for c in payloads]
            )
            points = [
                models.PointStruct(id=str(uuid4()), vector=vector, payload=payload)
                for vector, payload in zip(vectors, payloads, strict=True)
            ]
            client.upsert(COLLECTION, points=points)
            self._chunks.update({str(point.id): point.payload for point in points})
            logger.info("全局检索：%s 条记录更新，%s 个片段入库", len(fresh), len(payloads))
        return len(payloads)

    def search(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        limit: int = 20,
        chats: list[Item] | None = None,
    ) -> list[Hit]:
        """按意思和按字面各找一份，RRF 合并，再重排。同一条记录最多出 PER_ITEM 个片段。
        chats 是助手对话，传了就一起对齐；不传时用上次入库的对话。"""
        terms = query_terms(query)
        with self.knowledge.lock:
            client = self._client()
            self._sync(client, chats)
            where = (
                models.Filter(
                    must=[models.FieldCondition(key="type", match=models.MatchAny(any=types))]
                )
                if types
                else None
            )
            vector = self.knowledge.embeddings.embed_query(query)
            found = client.query_points(
                COLLECTION, query=vector, query_filter=where, limit=CANDIDATES, with_payload=True
            ).points
            by_meaning = [str(point.id) for point in found]
            by_words = self._by_words(terms, types)
            fused: dict[str, float] = defaultdict(float)
            for ranking in (by_meaning, by_words):
                for rank, pid in enumerate(ranking, start=1):
                    fused[pid] += 1 / (RRF_K + rank)
            candidates = sorted(fused, key=lambda pid: -fused[pid])[:RERANK]
            chunks = [self._chunks[pid] for pid in candidates if pid in self._chunks]
            if not chunks:
                return []
            scores = self.knowledge.reranker.score(
                query, [f"{c['title']}\n{c['heading']}\n{c['text']}" for c in chunks]
            )
        # 查询里有英文词（多半是技术名词，如 Kafka）时，含有全部这些词的片段排在前面
        words = [term for term in terms if term.isascii()]

        def order(pair):
            chunk, score = pair
            text = f"{chunk['title']}\n{chunk['text']}".lower()
            return (not all(word in text for word in words), -score)

        hits, per_item = [], defaultdict(int)
        for chunk, _ in sorted(zip(chunks, scores, strict=True), key=order):
            if per_item[chunk["key"]] >= PER_ITEM:
                continue
            per_item[chunk["key"]] += 1
            hits.append(
                Hit(
                    type=chunk["type"],
                    kind=chunk["kind"],
                    title=chunk["title"],
                    heading=chunk["heading"],
                    text=plain_text(chunk["text"]),
                    date=chunk["date"],
                    link=chunk["link"],
                    job_id=chunk["job_id"],
                    highlights=highlights(plain_text(chunk["text"]), terms),
                    literal=any(
                        term in f"{chunk['title']}\n{chunk['heading']}\n{chunk['text']}".lower()
                        for term in terms
                    ),
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _by_words(self, terms: list[str], types: list[str] | None) -> list[str]:
        """按字面：每个片段数一数查询里的词出现了几个（英文词算 2，中文两字一组算 1）。"""
        if not terms:
            return []
        scored = []
        for pid, chunk in self._chunks.items():
            if types and chunk["type"] not in types:
                continue
            haystack = f"{chunk['title']}\n{chunk['heading']}\n{chunk['text']}".lower()
            score = sum((2 if term.isascii() else 1) for term in terms if term in haystack)
            if score:
                scored.append((score, chunk["date"], pid))
        scored.sort(reverse=True)
        return [pid for _, _, pid in scored[:CANDIDATES]]
