"""公司研究图。一次运行只针对一个岗位。

    load_context ─▶ precheck ─▶ research ─▶ save ─▶ END
                       │           │
                       ▼           ▼
                      END         END（结论始终不合格）

research 节点里是一个 ReAct Agent：它自己决定搜什么、打开哪个网页、什么时候停。
图只管两头：前面读岗位、做前置检查，后面核对来源并保存报告。
"""

import hashlib
import logging
import operator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db.models import ArtifactKind, Job
from app.graphs.agent_loop import AgentGaveUp, run_agent
from app.graphs.common import announce, job_result
from app.llm.research import (
    RULES_FINGERPRINT,
    SYSTEM_PROMPT,
    ResearchOutput,
    build_user_message,
    check_research,
    finalize,
    parse_research,
    render_research_report,
)
from app.tools.research import SearchUnavailable, build_research_tools
from app.tools.sources import SourceLog
from app.tools.web import WebClient

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_context": "读取岗位",
    "precheck": "前置检查",
    "research": "联网研究",
    "save": "保存报告",
}


class ResearchInput(TypedDict):
    job_id: int
    conditions: str  # 候选人这次特别想了解的
    force: bool


class ResearchState(ResearchInput, total=False):
    job: dict | None
    company: str
    input_hash: str
    output: dict  # 通过核对的 ResearchOutput
    sources: list[dict]  # 这次运行见过的全部来源
    results: Annotated[list[dict], operator.add]


@dataclass
class ResearchNodes:
    engine: Engine
    model: BaseChatModel | None
    web: WebClient

    async def load_context(self, state: ResearchState) -> dict:
        announce(state["job_id"], "load_context")
        with Session(self.engine) as session:
            job = session.get(Job, state["job_id"])
            if job is None or job.deleted_at:
                return {"job": None, "company": ""}
            company = job.company.strip()
            if not company:  # 岗位没填公司名时，用评估时从 JD 里识别出的公司名
                evaluation = artifact_store.latest_evaluation(session, job.id)
                company = (evaluation.data["output"].get("company") or "") if evaluation else ""
            return {"job": job.model_dump(), "company": company}

    async def precheck(self, state: ResearchState) -> dict:
        announce(state["job_id"], "precheck")
        job = state["job"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not state["company"]:
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有公司名，请先给岗位填上公司名再研究"
            )

        parts = [RULES_FINGERPRINT, state["company"], job["jd_hash"] or "", state["conditions"]]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.company_research, state["job_id"], input_hash
                )
            if existing:
                return job_result(
                    state["job_id"],
                    "reused",
                    "这家公司已经研究过，直接使用已有报告。要重新联网查，请求里加 force",
                    existing.id,
                )
        return {"input_hash": input_hash}

    async def research(self, state: ResearchState) -> dict:
        announce(state["job_id"], "research")
        job = state["job"]
        log = SourceLog()
        writer = get_stream_writer()

        def on_use(tool: str, detail: str) -> None:
            writer({"job_id": state["job_id"], "tool": tool, "detail": detail})

        def parse(raw: str) -> ResearchOutput:
            output = parse_research(raw)
            check_research(output, log.dump())
            return output

        try:
            output, stats = await run_agent(
                model=self.model,
                tools=build_research_tools(self.web, log, on_use),
                system_prompt=SYSTEM_PROMPT,
                user_message=build_user_message(
                    company=state["company"],
                    title=job["title"],
                    city=job["city"] or "",
                    salary=job["salary"] or "",
                    jd=job["jd_text"],
                    focus=state["conditions"],
                    today=date.today().isoformat(),
                ),
                parse=parse,
            )
        except AgentGaveUp as error:
            return job_result(state["job_id"], "failed", f"研究结论{error}")
        except SearchUnavailable as error:
            return job_result(
                state["job_id"],
                "failed",
                f"联网搜索两次都失败了，一条结果都没拿到，所以没有写报告。原因：{error}",
            )
        logger.info(
            "研究岗位 %s：模型调用 %s 次，搜索 %s 次，读取网页 %s 个，"
            "input_tokens=%s output_tokens=%s",
            state["job_id"],
            stats.model_calls,
            log.searches,
            log.pages,
            stats.input_tokens,
            stats.output_tokens,
        )
        return {"output": output.model_dump(), "sources": log.dump()}

    async def save(self, state: ResearchState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        output = ResearchOutput.model_validate(state["output"])
        result = finalize(output, state["sources"])
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.company_research,
                job_id=state["job_id"],
                run_id=config["configurable"]["thread_id"],
                input_hash=state["input_hash"],
                title=f"公司研究：{output.company_name}",
                content_md=render_research_report(
                    output, result, state["sources"], today=date.today().isoformat()
                ),
                data={
                    "output": state["output"],
                    "result": result.model_dump(),
                    "sources": state["sources"],
                },
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


def _stop_or(next_node: str):
    return lambda state: END if state.get("results") else next_node


def build_research_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    web: WebClient,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    nodes = ResearchNodes(engine, model, web)

    graph = StateGraph(ResearchState, input_schema=ResearchInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "precheck")
    graph.add_conditional_edges("precheck", _stop_or("research"), ["research", END])
    graph.add_conditional_edges("research", _stop_or("save"), ["save", END])
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
