"""全局检索：在你的材料、岗位、沟通记录和全部成果里按意思和字面一起找。

和材料检索（/api/knowledge/search）分开：那边只查你的材料，是定制简历、面试准备找证据用的。
"""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.api.chat import conversation_items
from app.api.deps import KnowledgeDep, RunManagerDep, SessionDep
from app.db.models import SearchRecord, now_iso

router = APIRouter(prefix="/search", tags=["全局检索"])

SearchType = Literal["material", "job", "communication", "artifact", "chat"]


class SearchHit(BaseModel):
    type: SearchType = Field(
        description="material 材料，job 岗位，communication 沟通记录，artifact 成果，chat 助手对话"
    )
    kind: str = Field(description="材料种类、成果种类，或沟通里谁说的；岗位是 jd")
    title: str = Field(description="出自哪里，例如「岗位评估 · 星河智能 — Agent 工程师」")
    heading: str = Field(description="片段所在的标题，没有是空字符串")
    text: str
    date: str
    link: str = Field(description="前端打开它的地址")
    job_id: int | None
    highlights: list[tuple[int, int]] = Field(description="text 里和查询字面相同的位置")
    literal: bool = Field(description="标题或正文里有和查询字面相同的词；false 表示只按意思找到")


@router.get("", response_model=list[SearchHit])
async def search(
    request: Request,
    knowledge: KnowledgeDep,
    manager: RunManagerDep,
    session: SessionDep,
    q: Annotated[str, Query(description="要找什么，一句话或几个词")],
    type: Annotated[list[SearchType] | None, Query(description="只在这几类里找")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """检索前先和数据库、助手对话对齐。第一次会把全部内容建好索引，内容多时要一两分钟。"""
    if not q.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "q 不能为空")
    chats = await conversation_items(request, manager, session)
    hits = await asyncio.to_thread(
        knowledge.workspace.search, q.strip(), types=type, limit=limit, chats=chats
    )
    _remember(session, q.strip(), type[0] if type and len(type) == 1 else None, len(hits))
    return hits


HISTORY_LIMIT = 50


class SearchRecordRead(BaseModel):
    id: int
    query: str
    type: SearchType | None = Field(description="只在这一类里找；null 是全部")
    hits: int = Field(description="上次检索找到几条")
    created_at: str


def _remember(session, query: str, kind: str | None, hits: int) -> None:
    """记一条检索历史：同一个查询和类别只留一条。"""
    same_type = SearchRecord.type == kind if kind else col(SearchRecord.type).is_(None)
    record = session.exec(
        select(SearchRecord).where(SearchRecord.query == query, same_type)
    ).first()
    record = record or SearchRecord(query=query, type=kind)
    record.hits, record.created_at = hits, now_iso()
    session.add(record)
    session.commit()


@router.get("/history", response_model=list[SearchRecordRead])
def search_history(session: SessionDep):
    """最近的检索，最新的在前，最多 50 条。"""
    statement = select(SearchRecord).order_by(col(SearchRecord.created_at).desc())
    return session.exec(statement.limit(HISTORY_LIMIT)).all()


@router.delete("/history/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_search_record(record_id: int, session: SessionDep):
    record = session.get(SearchRecord, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条检索记录不存在")
    session.delete(record)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_search_history(session: SessionDep):
    for record in session.exec(select(SearchRecord)).all():
        session.delete(record)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
