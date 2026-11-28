"""能力画像图。一次运行针对几个岗位，产出一份成果（不属于某一个岗位）。

load ─▶ precheck ─▶ analyze ─▶ save ─▶ END
           │           │
           ▼           ▼
          END         END（结论始终不合格）
"""

import hashlib
import logging
import operator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.db import artifacts as artifact_store
from app.db import documents as document_store
from app.db.models import DOCUMENT_KIND_NAMES, ArtifactKind, DocumentKind, Job
from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE, announce, job_result
from app.llm import privacy
from app.llm.evidence import quote_sources
from app.llm.parsing import InvalidOutput
from app.llm.profile import (
    MAX_JOBS,
    MIN_JOBS,
    RULES_FINGERPRINT,
    ProfileOutput,
    build_messages,
    check_profile,
    parse_profile,
    render_profile,
)

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load": "读取岗位和材料",
    "precheck": "前置检查",
    "analyze": "归纳能力",
    "save": "保存画像",
}


class ProfileInput(TypedDict):
    job_ids: list[int]
    force: bool


class ProfileState(ProfileInput, total=False):
    jobs: list[dict]  # 有 JD、没删除的岗位：{id, company, title, jd_text, jd_hash}
    materials: dict[str, str]
    input_hash: str
    output: dict
    results: Annotated[list[dict], operator.add]


@dataclass
class ProfileNodes:
    engine: Engine
    model: BaseChatModel | None

    async def load(self, state: ProfileState) -> dict:
        announce(state["job_ids"][0], "load")
        with Session(self.engine) as session:
            rows = session.exec(
                select(Job).where(col(Job.id).in_(state["job_ids"]), Job.deleted_at == None)  # noqa: E711
            ).all()
            materials = {}
            for kind in (DocumentKind.resume, DocumentKind.supplement, DocumentKind.star):
                current = document_store.get_current(session, kind)
                materials[DOCUMENT_KIND_NAMES[kind]] = current.content_md if current else ""
        jobs = [
            {
                "id": job.id,
                "company": job.company,
                "title": job.title,
                "jd_text": job.jd_text,
                "jd_hash": job.jd_hash or "",
            }
            for job in sorted(rows, key=lambda job: job.id)
            if job.has_jd
        ]
        return {"jobs": jobs, "materials": materials}

    async def precheck(self, state: ProfileState) -> dict:
        announce(state["job_ids"][0], "precheck")
        jobs = state["jobs"]
        if len(jobs) < MIN_JOBS:
            return job_result(
                None,
                "blocked",
                f"至少要 {MIN_JOBS} 个有 JD 的岗位才能汇总，现在只有 {len(jobs)} 个",
            )
        if len(jobs) > MAX_JOBS:
            return job_result(
                None, "blocked", f"一次最多汇总 {MAX_JOBS} 个岗位，选了 {len(jobs)} 个"
            )
        parts = [RULES_FINGERPRINT, *(job["jd_hash"] for job in jobs), *state["materials"].values()]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.profile, None, input_hash
                )
            if existing:
                return job_result(
                    None, "reused", "这几个岗位和你的材料都没有变化，直接用已有的画像", existing.id
                )
        return {"input_hash": input_hash}

    async def analyze(self, state: ProfileState) -> dict:
        announce(state["job_ids"][0], "analyze")
        messages = build_messages(state["jobs"], state["materials"])
        materials = quote_sources(
            state["materials"],
            lambda text: privacy.mask_for_model(self.model, text),
        )
        ids = [job["id"] for job in state["jobs"]]
        for attempt in range(1, MAX_RETRIES + 2):
            reply = await self.model.ainvoke(messages)
            usage = reply.usage_metadata or {}
            logger.info(
                "能力画像：%s 个岗位，第 %s 次调用模型 input_tokens=%s output_tokens=%s",
                len(ids),
                attempt,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
            try:
                output = check_profile(parse_profile(str(reply.text)), ids, materials)
            except InvalidOutput as error:
                logger.warning("能力画像的输出不合格（第 %s 次）：%s", attempt, error)
                messages = [
                    *messages,
                    AIMessage(str(reply.text)),
                    HumanMessage(RETRY_TEMPLATE.format(problem=error)),
                ]
                continue
            return {"output": output.model_dump()}
        return job_result(None, "failed", f"模型输出连续 {MAX_RETRIES + 1} 次不合格")

    async def save(self, state: ProfileState, config: RunnableConfig) -> dict:
        announce(state["job_ids"][0], "save")
        output = ProfileOutput.model_validate(state["output"])
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.profile,
                job_id=None,
                run_id=config["configurable"]["thread_id"],
                input_hash=state["input_hash"],
                title=f"能力画像：{len(state['jobs'])} 个岗位",
                content_md=render_profile(output, state["jobs"], today=date.today().isoformat()),
                data={"output": state["output"], "job_ids": [job["id"] for job in state["jobs"]]},
            )
            return job_result(None, "done", artifact_id=artifact.id)


def _stop_or(next_node: str):
    return lambda state: END if state.get("results") else next_node


def build_profile_graph(
    *, engine: Engine, model: BaseChatModel | None, checkpointer: BaseCheckpointSaver
) -> CompiledStateGraph:
    nodes = ProfileNodes(engine, model)
    graph = StateGraph(ProfileState, input_schema=ProfileInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load")
    graph.add_edge("load", "precheck")
    graph.add_conditional_edges("precheck", _stop_or("analyze"), ["analyze", END])
    graph.add_conditional_edges("analyze", _stop_or("save"), ["save", END])
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
