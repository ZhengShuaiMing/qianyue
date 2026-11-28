"""面试准备图。一次运行只针对一个岗位。

    load_materials ─▶ precheck ─▶ retrieve ─▶ experiences ─▶ generate ─▶ validate ─▶ save
                         │                                      ▲            │
                         ▼                                      └─ 不合格 ───┤
                        END                               （最多重试 2 次）    ▼
                                                                    END（仍不合格）

这是固定流程加检索，不是 ReAct：本地的材料（JD、简历、STAR 经历、已有的评估和公司研究）
直接读；experiences 节点联网找这家公司的面经，也是程序固定搜两次，不用模型决定去查什么。
搜不到面经照常出题。
"""

import asyncio
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
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db import documents as document_store
from app.db.models import ArtifactKind, DocumentKind, Job
from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE, announce, job_result
from app.llm import privacy
from app.llm.interview_prep import (
    REPORT_TOKENS,
    RULES_FINGERPRINT,
    SYSTEM_PROMPT,
    PrepOutput,
    build_messages,
    check_prep,
    finalize,
    parse_prep,
    render_prep_report,
)
from app.llm.parsing import InvalidOutput
from app.llm.tailoring import jd_queries
from app.llm.tokens import estimate_tokens, trim_to_tokens
from app.rag.knowledge import KnowledgeBase
from app.tools.experiences import find_experiences
from app.tools.web import WebClient

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_materials": "读取材料",
    "precheck": "前置检查",
    "retrieve": "检索经历",
    "experiences": "联网查面经",
    "generate": "预测问题并准备回答",
    "validate": "核对内容",
    "save": "保存准备材料",
}
MAX_EVIDENCE = 10


class PrepInput(TypedDict):
    job_id: int
    conditions: str  # 候选人这次的要求：第几轮、面试官是谁、哪天面
    force: bool


class PrepState(PrepInput, total=False):
    job: dict | None
    names: dict
    resume: dict | None  # {content_md, content_hash}
    other_hashes: list[str]  # 补充资料和 STAR 经历当前版本的指纹
    evaluation: str  # 岗位评估报告正文，没有就是空
    research: str  # 公司研究报告正文，没有就是空
    input_hash: str
    evidence: list[dict]  # 检索到的证据：{id, source, text}
    experiences: list[dict]  # 联网搜到的面经：{id, url, title, text, opened}，没有就是空
    attempts: int
    raw: str
    feedback: str
    output: dict
    results: Annotated[list[dict], operator.add]


@dataclass
class PrepNodes:
    engine: Engine
    model: BaseChatModel | None
    knowledge: KnowledgeBase
    token_budget: int
    web: WebClient

    async def load_materials(self, state: PrepState) -> dict:
        announce(state["job_id"], "load_materials")
        with Session(self.engine) as session:
            job = session.get(Job, state["job_id"])
            if job is None or job.deleted_at:
                return {"job": None, "resume": None}
            resume = document_store.get_current(session, DocumentKind.resume)
            others = [
                document_store.get_current(session, kind)
                for kind in (DocumentKind.supplement, DocumentKind.star)
            ]
            evaluation = artifact_store.latest_evaluation(session, job.id)
            research = artifact_store.latest_artifact(
                session, ArtifactKind.company_research, job.id
            )
            output = evaluation.data["output"] if evaluation else {}
            return {
                "job": job.model_dump(),
                "names": {
                    "company": job.company.strip() or output.get("company") or "未知公司",
                    "title": job.title.strip() or output.get("title") or "未知职位",
                },
                "resume": resume
                and {"content_md": resume.content_md, "content_hash": resume.content_hash},
                "other_hashes": [document.content_hash if document else "" for document in others],
                "evaluation": evaluation.content_md if evaluation else "",
                "research": research.content_md if research else "",
            }

    async def precheck(self, state: PrepState) -> dict:
        announce(state["job_id"], "precheck")
        job, resume = state["job"], state["resume"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not job["jd_text"].strip():
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有完整 JD，请先补全 JD 再准备面试"
            )
        if resume is None:
            return job_result(
                state["job_id"], "blocked", "还没有保存基础简历，回答要点需要引用你的真实经历"
            )

        tokens = estimate_tokens(SYSTEM_PROMPT + job["jd_text"] + resume["content_md"])
        if tokens > self.token_budget:
            return job_result(
                state["job_id"],
                "blocked",
                f"JD 和简历约 {tokens} Token，超过输入预算 {self.token_budget}。"
                "请精简后再试，或调大 QIANYUE_LLM_INPUT_TOKEN_BUDGET",
            )

        parts = [
            RULES_FINGERPRINT,
            job["jd_hash"],
            resume["content_hash"],
            *state["other_hashes"],
            state["evaluation"],
            state["research"],
            state["conditions"],
        ]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.interview_prep, state["job_id"], input_hash
                )
            if existing:
                return job_result(
                    state["job_id"],
                    "reused",
                    "JD、材料和已有报告都没有变化，直接使用已有的面试准备",
                    existing.id,
                )
        return {"input_hash": input_hash}

    async def retrieve(self, state: PrepState) -> dict:
        """把 JD 的每条要求当作查询，到补充资料和 STAR 经历里找能用来回答的经历。"""
        announce(state["job_id"], "retrieve")
        queries = jd_queries(state["job"]["jd_text"])
        passages = await asyncio.to_thread(
            self.knowledge.search,
            queries,
            kinds=[DocumentKind.supplement, DocumentKind.star],
            limit=MAX_EVIDENCE,
        )
        logger.info(
            "面试准备岗位 %s：%s 条 JD 要求，检索到 %s 个证据片段",
            state["job_id"],
            len(queries),
            len(passages),
        )
        return {
            "evidence": [
                {"id": f"E{number}", "source": passage.source, "text": passage.text}
                for number, passage in enumerate(passages, start=1)
            ]
        }

    async def experiences(self, state: PrepState) -> dict:
        """联网找这家公司的面经。搜不到、读不到都照常往下走。"""
        announce(state["job_id"], "experiences")
        names = state["names"]
        if names["company"] == "未知公司":
            return {"experiences": []}
        writer = get_stream_writer()

        def on_use(tool: str, detail: str) -> None:
            writer({"job_id": state["job_id"], "tool": tool, "detail": detail})

        found = await find_experiences(self.web, names["company"], names["title"], on_use)
        return {"experiences": found}

    async def generate(self, state: PrepState) -> dict:
        announce(state["job_id"], "generate")
        experiences_block = "\n\n".join(
            f"[{item['id']}] {item['title']}"
            f"{'' if item['opened'] else '（只读到搜索摘要）'}\n{item['text']}"
            for item in state["experiences"]
        )
        evidence_block = "\n\n".join(
            f"[{item['id']}] 出处：{item['source']}\n{item['text']}" for item in state["evidence"]
        )
        messages = list(
            build_messages(
                company=state["names"]["company"],
                title=state["names"]["title"],
                jd=state["job"]["jd_text"],
                resume=state["resume"]["content_md"],
                evidence_block=evidence_block,
                evaluation=state["evaluation"],
                research=state["research"],
                request=state["conditions"],
                today=date.today().isoformat(),
                experiences_block=experiences_block,
            )
        )
        if state.get("feedback"):
            messages += [
                AIMessage(state["raw"]),
                HumanMessage(RETRY_TEMPLATE.format(problem=state["feedback"])),
            ]

        attempt = state.get("attempts", 0) + 1
        reply = await self.model.ainvoke(messages)
        usage = reply.usage_metadata or {}
        logger.info(
            "面试准备岗位 %s：第 %s 次调用模型 input_tokens=%s output_tokens=%s",
            state["job_id"],
            attempt,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        return {"raw": str(reply.text), "attempts": attempt}

    async def validate(self, state: PrepState) -> dict:
        announce(state["job_id"], "validate")
        job, resume = state["job"], state["resume"]

        def mask(text: str) -> str:
            return privacy.mask_for_model(self.model, text)

        allowed = "\n".join(
            [
                state["names"]["company"],
                state["names"]["title"],
                job["jd_text"],
                resume["content_md"],
                *[item["text"] for item in state["evidence"]],
                trim_to_tokens(state["evaluation"].strip(), REPORT_TOKENS),
                trim_to_tokens(state["research"].strip(), REPORT_TOKENS),
                state["conditions"],
            ]
        )
        try:
            output = check_prep(
                parse_prep(state["raw"]),
                jd=job["jd_text"],
                resume=resume["content_md"],
                evidence={item["id"]: item["text"] for item in state["evidence"]},
                allowed=mask(allowed),
                experiences={item["id"]: item["text"] for item in state["experiences"]},
                mask=mask,
            )
        except InvalidOutput as error:
            feedback = mask(str(error))
            logger.warning("面试准备岗位 %s：输出不合格：%s", state["job_id"], feedback)
            if state["attempts"] > MAX_RETRIES:
                return job_result(
                    state["job_id"],
                    "failed",
                    f"模型给出的面试准备连续 {state['attempts']} 次不合格：{feedback}",
                )
            return {"feedback": feedback}
        return {"output": finalize(output).model_dump(), "feedback": ""}

    async def save(self, state: PrepState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        output = PrepOutput.model_validate(state["output"])
        names = state["names"]
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.interview_prep,
                job_id=state["job_id"],
                run_id=config["configurable"]["thread_id"],
                input_hash=state["input_hash"],
                title=f"面试准备：{names['company']} — {names['title']}",
                content_md=render_prep_report(
                    output,
                    company=names["company"],
                    title=names["title"],
                    today=date.today().isoformat(),
                    sources={item["id"]: item["source"] for item in state["evidence"]},
                    experiences=state["experiences"],
                ),
                data={
                    "output": state["output"],
                    "evidence": state["evidence"],
                    "experiences": state["experiences"],
                },
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


def after_precheck(state: PrepState) -> str:
    return END if state.get("results") else "retrieve"


def after_validate(state: PrepState) -> str:
    if state.get("results"):
        return END
    return "generate" if state.get("feedback") else "save"


def build_interview_prep_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    knowledge: KnowledgeBase,
    token_budget: int,
    checkpointer: BaseCheckpointSaver,
    web: WebClient,
) -> CompiledStateGraph:
    nodes = PrepNodes(engine, model, knowledge, token_budget, web)

    graph = StateGraph(PrepState, input_schema=PrepInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load_materials")
    graph.add_edge("load_materials", "precheck")
    graph.add_conditional_edges("precheck", after_precheck, ["retrieve", END])
    graph.add_edge("retrieve", "experiences")
    graph.add_edge("experiences", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", after_validate, ["generate", "save", END])
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
