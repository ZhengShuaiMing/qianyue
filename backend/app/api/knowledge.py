from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import KnowledgeDep
from app.db.models import DocumentKind

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class PassageRead(BaseModel):
    source: str
    kind: DocumentKind
    heading: str
    text: str
    score: float


@router.get("/search", response_model=list[PassageRead])
def search_knowledge(
    knowledge: KnowledgeDep,
    q: Annotated[str, Query(description="要找的内容，例如一条 JD 要求")],
    kind: Annotated[list[DocumentKind] | None, Query(description="只在这些材料里找")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
):
    """在你的材料（简历、补充资料、STAR 经历）里按意思检索相关片段，按相关度从高到低返回。

    第一次调用会下载并加载 Embedding 和 Rerank 模型（约 1.2GB），需要等一会儿。
    """
    if not q.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "q 不能为空")
    passages = knowledge.search([q.strip()], kinds=kind, per_query=limit, keep=limit, limit=limit)
    return [
        PassageRead(
            source=passage.source,
            kind=passage.kind,
            heading=passage.heading,
            text=passage.text,
            score=round(passage.score, 3),
        )
        for passage in passages
    ]
