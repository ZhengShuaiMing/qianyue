"""识别截图图：没有模型 Key 时，添加岗位、添加沟通带的截图由用户的 Agent 代读。

read ─▶ END

有 Key 时截图接口直接读完就存（api/screenshots.py），不经过这里。没 Key 时接口先把截图存进
data/uploads/，发起这个运行；读图那一步由 AgentChatModel 交给 Agent，读出的内容按同样的格式
校验，再用和接口相同的方式存成岗位或沟通记录。
"""

import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine
from sqlmodel import Session

from app.core import uploads
from app.db import communications as communication_store
from app.db import jobs as job_store
from app.db.models import Communication, Job, Speaker
from app.graphs.common import announce, job_result
from app.llm.parsing import InvalidOutput
from app.llm.screenshot import ChatFromImage, JobFromImage, chat_as_record, read_screenshots

NODE_LABELS = {"read": "识别截图"}

NO_JOB = "没有从截图中识别出岗位内容，请换一张包含岗位信息的截图"
NO_CHAT = "没有从截图中识别出聊天消息，请换一张聊天记录截图"


class ScreenshotInput(TypedDict):
    upload: str  # data/uploads/ 下的编号
    target: Literal["job", "communication"]
    job_ids: list[int]  # 添加沟通时是那个岗位；添加岗位时为空
    # 用户在截图下面打的字和填的项：添加岗位是 jd_text、url、company、title、city、salary，
    # 添加沟通是 text、speaker
    fields: dict


class ScreenshotState(ScreenshotInput, total=False):
    results: Annotated[list[dict], operator.add]


def save_job(session: Session, read: JobFromImage, fields: dict) -> tuple[Job, bool]:
    """截图读出的岗位和用户填的合在一起存下：用户填了的项以用户的为准，打的字接在 JD 后面。
    什么都没读出来时抛 ValueError。返回 (岗位, 是否新建)。"""
    jd = "\n\n".join(
        part for part in (read.jd_text.strip(), (fields.get("jd_text") or "").strip()) if part
    )
    if not (jd or read.company.strip() or read.title.strip()):
        raise ValueError(NO_JOB)

    def pick(name: str) -> str:
        return (fields.get(name) or "").strip() or getattr(read, name).strip()

    return job_store.create_or_get_job(
        session,
        url=(fields.get("url") or "").strip() or None,
        jd_text=jd,
        company=pick("company"),
        title=pick("title"),
        city=pick("city") or None,
        salary=pick("salary") or None,
    )


def save_communication(
    session: Session, job_id: int, read: ChatFromImage, fields: dict
) -> tuple[Communication, bool]:
    """聊天截图读出的消息加上用户打的字存成一条沟通记录。没读出消息时抛 ValueError。"""
    if not any(line.text.strip() for line in read.messages):
        raise ValueError(NO_CHAT)
    speaker, text = chat_as_record(
        read, fields.get("text") or "", fields.get("speaker") or Speaker.transcript
    )
    return communication_store.add_communication(session, job_id, Speaker(speaker), text)


@dataclass
class ScreenshotNodes:
    engine: Engine
    model: BaseChatModel | None
    data_dir: Path

    async def read(self, state: ScreenshotState) -> dict:
        job_id = state["job_ids"][0] if state["job_ids"] else None
        announce(job_id, "read")
        try:
            images = uploads.data_urls(self.data_dir, state["upload"])
        except uploads.UploadMissing as error:
            return job_result(job_id, "blocked", str(error))
        try:
            read = await read_screenshots(self.model, state["target"], images)
        except InvalidOutput as error:
            return job_result(job_id, "failed", str(error))
        with Session(self.engine) as session:
            try:
                if state["target"] == "job":
                    job, created = save_job(session, read, state["fields"])
                    reason = "已保存为新岗位" if created else "这个岗位之前存过，没有重复保存"
                    return job_result(job.id, "done", reason)
                _, created = save_communication(session, job_id, read, state["fields"])
            except ValueError as error:
                return job_result(job_id, "blocked", str(error))
        reason = "已保存沟通记录" if created else "同样的沟通记录之前存过，没有重复保存"
        return job_result(job_id, "done", reason)


def build_screenshot_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    checkpointer: BaseCheckpointSaver | None,
    data_dir: Path,
) -> CompiledStateGraph:
    nodes = ScreenshotNodes(engine, model, data_dir)
    graph = StateGraph(ScreenshotState, input_schema=ScreenshotInput)
    graph.add_node("read", nodes.read)
    graph.add_edge(START, "read")
    graph.add_edge("read", END)
    return graph.compile(checkpointer=checkpointer)
