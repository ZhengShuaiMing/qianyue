"""沟通建议图。一次运行只针对一个岗位。

    load_context ─▶ precheck ─▶ advise ─▶ save ─▶ END
                       │           │
                       ▼           ▼
                      END         END（结论始终不合格）

advise 节点里是一个 ReAct Agent：它按需检索候选人的材料、读取这个岗位的评估和公司研究，
然后分析对话并起草消息。草稿只保存为成果，不会被发送；岗位阶段只给建议，不会被修改。
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
from app.db import communications as communication_store
from app.db import documents as document_store
from app.db.models import ArtifactKind, DocumentKind, Job, Speaker
from app.graphs.agent_loop import AgentGaveUp, run_agent
from app.graphs.common import announce, job_result
from app.llm import privacy
from app.llm.communication import (
    RULES_FINGERPRINT,
    SYSTEM_PROMPT,
    AdviceOutput,
    build_user_message,
    chat_record_prefix,
    check_advice,
    finalize,
    format_chat,
    parse_advice,
    render_advice_report,
)
from app.llm.evidence import prefixed_source
from app.llm.factcheck import Item, check_wording
from app.llm.parsing import InvalidOutput
from app.rag.knowledge import KnowledgeBase
from app.tools.job import build_job_tools

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_context": "读取材料",
    "precheck": "前置检查",
    "advise": "分析并起草",
    "wording": "核对措辞",
    "save": "保存建议",
}


class AdviceInput(TypedDict):
    job_id: int
    conditions: str  # 候选人这次的要求：渠道、语气、目的
    force: bool


class AdviceState(AdviceInput, total=False):
    job: dict | None
    names: dict
    resume: dict | None
    records: list[dict]  # 沟通记录：{speaker, text}
    reference: str  # 草稿可以取材的其他文字：补充资料、STAR 经历、这个岗位的报告
    input_hash: str
    output: dict
    results: Annotated[list[dict], operator.add]


@dataclass
class AdviceNodes:
    engine: Engine
    model: BaseChatModel | None
    knowledge: KnowledgeBase

    async def load_context(self, state: AdviceState) -> dict:
        announce(state["job_id"], "load_context")
        with Session(self.engine) as session:
            job = session.get(Job, state["job_id"])
            if job is None or job.deleted_at:
                return {"job": None, "resume": None, "records": []}
            resume = document_store.get_current(session, DocumentKind.resume)
            others = [
                document_store.get_current(session, kind)
                for kind in (DocumentKind.supplement, DocumentKind.star)
            ]
            # 只取最近的一份评估和一份公司研究：旧报告里的数字已经过时，不该被当成有出处
            reports = [
                report
                for kind in (ArtifactKind.evaluation, ArtifactKind.company_research)
                if (report := artifact_store.latest_artifact(session, kind, job.id))
            ]
            evaluation = artifact_store.latest_evaluation(session, job.id)
            output = evaluation.data["output"] if evaluation else {}
            return {
                "job": job.model_dump(),
                "names": {
                    "company": job.company.strip() or output.get("company") or "未知公司",
                    "title": job.title.strip() or output.get("title") or "未知职位",
                },
                "resume": resume
                and {"content_md": resume.content_md, "content_hash": resume.content_hash},
                "records": [
                    {"speaker": record.speaker, "text": record.text}
                    for record in communication_store.list_communications(session, job.id)
                ],
                "reference": "\n".join(
                    [document.content_md for document in others if document]
                    + [report.content_md for report in reports]
                ),
            }

    async def precheck(self, state: AdviceState) -> dict:
        announce(state["job_id"], "precheck")
        job, resume = state["job"], state["resume"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not job["jd_text"].strip():
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有完整 JD，请先补全 JD 再要沟通建议"
            )
        if resume is None:
            return job_result(
                state["job_id"], "blocked", "还没有保存基础简历，草稿需要引用你的真实经历"
            )

        parts = [
            RULES_FINGERPRINT,
            job["jd_hash"],
            resume["content_hash"],
            format_chat(state["records"]),
            state["conditions"],
            state["reference"],  # 补充资料、STAR 经历和报告变了，草稿可以取材的内容也就变了
        ]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.communication_advice, state["job_id"], input_hash
                )
            if existing:
                return job_result(
                    state["job_id"],
                    "reused",
                    "沟通记录、JD 和简历都没有变化，直接使用已有建议。要换一批草稿，请求里加 force",
                    existing.id,
                )
        return {"input_hash": input_hash}

    async def advise(self, state: AdviceState) -> dict:
        announce(state["job_id"], "advise")
        job, resume = state["job"], state["resume"]
        chat = format_chat(state["records"])

        def mask(text: str) -> str:
            return privacy.mask_for_model(self.model, text)

        sources = [
            prefixed_source(
                f"沟通记录 #{number}", record["text"], chat_record_prefix(number, speaker), mask
            )
            for number, record in enumerate(state["records"], start=1)
            if (speaker := Speaker(record["speaker"])) != Speaker.me
        ]
        writer = get_stream_writer()

        def on_use(tool: str, detail: str) -> None:
            writer({"job_id": state["job_id"], "tool": tool, "detail": detail})

        allowed = mask(
            "\n".join(
                [
                    job["company"],
                    job["title"],
                    job["city"] or "",
                    job["salary"] or "",
                    job["jd_text"],
                    resume["content_md"],
                    chat,
                    state["conditions"],
                    state["reference"],
                ]
            )
        )

        def parse(raw: str) -> AdviceOutput:
            try:
                return check_advice(
                    parse_advice(raw), chat=chat, allowed=allowed, sources=sources, mask=mask
                )
            except InvalidOutput as error:
                raise InvalidOutput(mask(str(error))) from None

        try:
            output, stats = await run_agent(
                model=self.model,
                tools=build_job_tools(self.engine, self.knowledge, state["job_id"], on_use),
                system_prompt=SYSTEM_PROMPT,
                user_message=build_user_message(
                    company=state["names"]["company"],
                    title=state["names"]["title"],
                    city=job["city"] or "",
                    salary=job["salary"] or "",
                    job_stage=job["stage"],
                    jd=job["jd_text"],
                    resume=resume["content_md"],
                    chat=chat,
                    request=state["conditions"],
                    today=date.today().isoformat(),
                ),
                parse=parse,
            )
        except AgentGaveUp as error:
            return job_result(state["job_id"], "failed", f"沟通建议{error}")
        logger.info(
            "沟通建议岗位 %s：沟通记录 %s 条，模型调用 %s 次，工具调用 %s 次，"
            "input_tokens=%s output_tokens=%s",
            state["job_id"],
            len(state["records"]),
            stats.model_calls,
            stats.tool_calls,
            stats.input_tokens,
            stats.output_tokens,
        )
        final = finalize(output, has_chat=bool(state["records"]), current_job_stage=job["stage"])
        return {"output": final.model_dump()}

    async def wording(self, state: AdviceState) -> dict:
        """让模型核对每条草稿的措辞：有没有替用户说了材料里没有的经历，或者替用户做了承诺。
        发现的问题写在那条草稿上，不退回重写。"""
        announce(state["job_id"], "wording")
        output = AdviceOutput.model_validate(state["output"])
        job = state["job"]
        said = "\n".join(
            part for part in (format_chat(state["records"]), state["conditions"]) if part
        )
        material = "\n\n".join(
            [
                f"公司：{state['names']['company']}\n职位：{state['names']['title']}\n"
                f"城市：{job['city'] or '未填写'}\n薪资：{job['salary'] or '未填写'}",
                f"JD：\n{job['jd_text'].strip()}",
                f"基础简历：\n{state['resume']['content_md'].strip()}",
                f"其他材料：\n{state['reference'].strip() or '（没有）'}",
            ]
        )
        found = await check_wording(
            self.model,
            material=material,
            items=[Item(basis=said, text=draft.text) for draft in output.drafts],
            label=f"沟通建议岗位 {state['job_id']}",
        )
        drafts = [
            draft.model_copy(update={"cautions": cautions})
            for draft, cautions in zip(output.drafts, found, strict=True)
        ]
        return {"output": output.model_copy(update={"drafts": drafts}).model_dump()}

    async def save(self, state: AdviceState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        output = AdviceOutput.model_validate(state["output"])
        names = state["names"]
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.communication_advice,
                job_id=state["job_id"],
                run_id=config["configurable"]["thread_id"],
                input_hash=state["input_hash"],
                title=f"沟通建议：{names['company']} — {names['title']}",
                content_md=render_advice_report(
                    output,
                    company=names["company"],
                    title=names["title"],
                    today=date.today().isoformat(),
                ),
                data={"output": state["output"], "records": len(state["records"])},
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


def _stop_or(next_node: str):
    return lambda state: END if state.get("results") else next_node


def build_communication_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    knowledge: KnowledgeBase,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    nodes = AdviceNodes(engine, model, knowledge)

    graph = StateGraph(AdviceState, input_schema=AdviceInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "precheck")
    graph.add_conditional_edges("precheck", _stop_or("advise"), ["advise", END])
    graph.add_conditional_edges("advise", _stop_or("wording"), ["wording", END])
    graph.add_edge("wording", "save")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
