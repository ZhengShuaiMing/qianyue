"""围绕一个岗位的只读工具：检索用户的材料、读取这个岗位已有的报告。"""

import asyncio
from collections.abc import Callable
from typing import Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db.models import ArtifactKind
from app.rag.knowledge import KnowledgeBase

REPORT_CHARS = 6000
REPORT_NAMES = {"evaluation": "岗位评估", "company_research": "公司研究"}


def build_job_tools(
    engine: Engine, knowledge: KnowledgeBase, job_id: int, on_use: Callable[[str, str], None]
) -> list[BaseTool]:
    @tool
    async def search_materials(query: str) -> str:
        """在候选人的材料（基础简历、补充资料、STAR 经历）里按意思检索相关片段。
        需要引用候选人的真实经历、项目或数字时先用它查，不要凭印象写。"""
        on_use("search_materials", query)
        passages = await asyncio.to_thread(knowledge.search, [query], per_query=6, keep=4, limit=4)
        if not passages:
            return "材料里没有找到相关内容。"
        return "\n\n".join(f"出处：{passage.source}\n{passage.text}" for passage in passages)

    @tool
    def read_report(kind: Literal["evaluation", "company_research"]) -> str:
        """读取这个岗位已有的报告。evaluation 是岗位评估（匹配度、缺口、薪资分析），
        company_research 是公司研究（业务、风险信号、值得问的问题）。"""
        on_use("read_report", REPORT_NAMES[kind])
        with Session(engine) as session:
            artifact = artifact_store.latest_artifact(session, ArtifactKind(kind), job_id)
        if artifact is None:
            return f"这个岗位还没有{REPORT_NAMES[kind]}报告。"
        text = artifact.content_md[:REPORT_CHARS]
        suffix = (
            "\n（报告过长，后面的部分已省略）" if len(artifact.content_md) > REPORT_CHARS else ""
        )
        return text + suffix

    return [search_materials, read_report]
