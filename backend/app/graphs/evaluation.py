"""岗位评估图。

一次运行是一个 LangGraph 线程，可以包含一个或多个岗位：

    运行图   START ──分发──▶ evaluate_job（每个岗位一份，并行）──▶ END

    单岗子图 load_materials ─▶ precheck ─▶ call_model ─▶ validate ─▶ save ─▶ END
                                  │            ▲            │  └▶ recheck ─▶ save
                                  ▼            └── 不合格 ──┤（最多重试 2 次）
                                 END                        ▼
                                                           END（仍不合格）

总分等于结论分数线（3.5、4.0、4.5）或低于该线 0.1 分时走 recheck：同样的输入再评两次，
每个维度取三次的中间值。由用户的 Agent 代答时不复评：同一个 Agent 再答一遍不是独立的样本。

每个节点跑完，LangGraph 把 State 存进 Checkpoint。运行中断后从断点继续：
已完成的岗位不重跑，没完成的岗位从它停下的节点接着跑。
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
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db import documents as document_store
from app.db import jobs as job_store
from app.db.models import ArtifactKind, Document, DocumentKind, Job
from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE, announce, job_result
from app.llm import privacy
from app.llm.evaluation import (
    RECHECKS,
    RULES_FINGERPRINT,
    SYSTEM_PROMPT,
    EvaluationOutput,
    InvalidOutput,
    build_messages,
    check_blocker_quotes,
    compute_scorecard,
    merge_by_median,
    near_boundary,
    parse_output,
    render_report,
    report_title,
)
from app.llm.evidence import quote_sources
from app.llm.tokens import estimate_tokens, trim_to_tokens

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_materials": "读取材料",
    "precheck": "前置检查",
    "call_model": "调用模型",
    "validate": "校验输出",
    "recheck": "复评（总分在分数线附近）",
    "save": "保存报告",
}


# ---------- State ----------


class JobInput(TypedDict):
    job_id: int
    conditions: str
    force: bool


class JobState(JobInput, total=False):
    """单岗子图的 State：一个岗位从读材料到保存报告之间的全部中间结果。"""

    job: dict | None  # 岗位快照。评估全程用同一份 JD，不受中途修改影响
    resume: dict | None  # {id, content_md, content_hash}
    supplement: dict | None
    supplement_truncated: bool
    star: dict | None  # STAR 经历，选填
    star_truncated: bool
    input_hash: str
    attempts: int  # 已调用模型的次数
    raw: str  # 模型最近一次的原始输出
    feedback: str  # 最近一次校验不合格的原因；合格后清空
    output: dict  # 校验通过的 EvaluationOutput
    recheck_totals: list[float]  # 多评了几次时，每一次各自的总分；没有复评就是空
    results: Annotated[list[dict], operator.add]


class JobOutput(TypedDict):
    results: Annotated[list[dict], operator.add]


class RunState(TypedDict, total=False):
    job_ids: list[int]
    conditions: str
    force: bool
    # 每个岗位的结论。多个岗位并行写入，用 operator.add 合并成一个列表。
    results: Annotated[list[dict], operator.add]


def _snapshot(document: Document | None) -> dict | None:
    if document is None:
        return None
    return {
        "id": document.id,
        "content_md": document.content_md,
        "content_hash": document.content_hash,
    }


# ---------- Node ----------


@dataclass
class EvaluationNodes:
    engine: Engine
    model: BaseChatModel | None
    token_budget: int

    async def load_materials(self, state: JobState) -> dict:
        announce(state["job_id"], "load_materials")
        with Session(self.engine) as session:
            job = session.get(Job, state["job_id"])
            return {
                "job": job.model_dump() if job and not job.deleted_at else None,
                "resume": _snapshot(document_store.get_current(session, DocumentKind.resume)),
                "supplement": _snapshot(
                    document_store.get_current(session, DocumentKind.supplement)
                ),
                "star": _snapshot(document_store.get_current(session, DocumentKind.star)),
            }

    async def precheck(self, state: JobState) -> dict:
        """能不能评、要不要评：材料齐不齐、输入超不超预算、是不是已经评过。"""
        announce(state["job_id"], "precheck")
        job, resume, supplement = state["job"], state["resume"], state["supplement"]
        star = state["star"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not job["jd_text"].strip():
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有完整 JD，请先补全 JD 再评估"
            )
        if resume is None:
            return job_result(state["job_id"], "blocked", "还没有保存基础简历，请先保存简历再评估")

        required = SYSTEM_PROMPT + job["jd_text"] + resume["content_md"] + state["conditions"]
        required_tokens = estimate_tokens(required)
        if required_tokens > self.token_budget:
            return job_result(
                state["job_id"],
                "blocked",
                f"JD 和简历约 {required_tokens} Token，超过输入预算 {self.token_budget}。"
                "请精简后再评估，或调大 QIANYUE_LLM_INPUT_TOKEN_BUDGET",
            )

        parts = [
            RULES_FINGERPRINT,
            job["jd_hash"],
            resume["content_hash"],
            supplement["content_hash"] if supplement else "",
            state["conditions"].strip(),
            star["content_hash"] if star else "",
        ]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.evaluation, state["job_id"], input_hash
                )
            if existing:
                return job_result(
                    state["job_id"],
                    "reused",
                    "JD、你的材料和条件都没有变化，直接使用已有报告",
                    existing.id,
                )

        # 补充资料和 STAR 经历是选填的：放不下时裁掉超出的部分，而不是拒绝评估。
        # 先放补充资料（里面有求职方向和期望，几个维度靠它才能评），剩下的给 STAR 经历。
        update = {"input_hash": input_hash, "supplement_truncated": False, "star_truncated": False}
        remaining = self.token_budget - required_tokens
        for key, document in (("supplement", supplement), ("star", star)):
            if not document:
                continue
            trimmed = trim_to_tokens(document["content_md"], remaining)
            remaining -= estimate_tokens(trimmed)
            if trimmed != document["content_md"]:
                update |= {key: document | {"content_md": trimmed}, f"{key}_truncated": True}
        return update

    async def call_model(self, state: JobState) -> dict:
        announce(state["job_id"], "call_model")
        messages = list(
            build_messages(
                Job(**state["job"]),
                resume=state["resume"]["content_md"],
                supplement=(state["supplement"] or {}).get("content_md", ""),
                star=(state["star"] or {}).get("content_md", ""),
                conditions=state["conditions"],
            )
        )
        if state.get("feedback"):
            # 重试：把上次的输出和不合格原因一起交还给模型。
            messages += [
                AIMessage(state["raw"]),
                HumanMessage(RETRY_TEMPLATE.format(problem=state["feedback"])),
            ]

        attempt = state.get("attempts", 0) + 1
        reply = await self.model.ainvoke(messages)
        usage = reply.usage_metadata or {}
        logger.info(
            "评估岗位 %s：第 %s 次调用模型 input_tokens=%s（其中命中缓存 %s）output_tokens=%s",
            state["job_id"],
            attempt,
            usage.get("input_tokens"),
            (usage.get("input_token_details") or {}).get("cache_read", "未知"),
            usage.get("output_tokens"),
        )
        return {"raw": str(reply.text), "attempts": attempt}

    async def validate(self, state: JobState) -> dict:
        announce(state["job_id"], "validate")
        try:
            output = parse_output(state["raw"])
            sources = quote_sources(
                {
                    "JD": state["job"]["jd_text"],
                    "基础简历": state["resume"]["content_md"],
                    "补充资料": (state["supplement"] or {}).get("content_md", ""),
                    "STAR 经历": (state["star"] or {}).get("content_md", ""),
                },
                lambda text: privacy.mask_for_model(self.model, text),
            )
            output = check_blocker_quotes(output, sources)
            compute_scorecard(output)
        except InvalidOutput as error:
            logger.warning("评估岗位 %s：模型输出不合格：%s", state["job_id"], error)
            if state["attempts"] > MAX_RETRIES:
                return job_result(
                    state["job_id"], "failed", f"模型输出连续 {state['attempts']} 次不合格：{error}"
                )
            return {"feedback": str(error)}
        return {"output": output.model_dump(), "feedback": ""}

    async def recheck(self, state: JobState) -> dict:
        """总分在结论的分数线附近时，同样的输入再评几次，每个维度取中间值。

        同一份输入重评，总分可能略有波动。平时不影响结论，
        落在分数线上时结论会在两档之间变，所以只在这种时候多评。
        """
        announce(state["job_id"], "recheck")
        first = EvaluationOutput.model_validate(state["output"])
        messages = list(
            build_messages(
                Job(**state["job"]),
                resume=state["resume"]["content_md"],
                supplement=(state["supplement"] or {}).get("content_md", ""),
                star=(state["star"] or {}).get("content_md", ""),
                conditions=state["conditions"],
            )
        )
        replies = await asyncio.gather(
            *[self.model.ainvoke(messages) for _ in range(RECHECKS)], return_exceptions=True
        )
        outputs = [first]
        for reply in replies:
            # 复评只是让结论更稳：某一次出错（网络、限流）或不合格就不算它，不为它重试，
            # 第一次已经合格的评估照常保存
            if isinstance(reply, Exception):
                logger.warning("评估岗位 %s：复评调用模型出错：%r", state["job_id"], reply)
                continue
            try:
                again = parse_output(str(reply.text))
                compute_scorecard(again)
            except InvalidOutput as error:
                logger.warning("评估岗位 %s：复评的输出不合格：%s", state["job_id"], error)
                continue
            outputs.append(again)
        totals = [compute_scorecard(output).total for output in outputs]
        logger.info("评估岗位 %s：总分在分数线附近，共评 %s 次", state["job_id"], len(outputs))
        if len(outputs) == 1:
            return {"recheck_totals": []}
        return {"output": merge_by_median(outputs).model_dump(), "recheck_totals": totals}

    async def save(self, state: JobState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        job = Job(**state["job"])
        output = EvaluationOutput.model_validate(state["output"])
        scorecard = compute_scorecard(output)
        data = {
            "output": state["output"],
            "scorecard": scorecard.model_dump(mode="json"),
            "recheck_totals": state.get("recheck_totals", []),
            "inputs": {
                "resume_id": state["resume"]["id"],
                "supplement_id": state["supplement"]["id"] if state["supplement"] else None,
                "supplement_truncated": state["supplement_truncated"],
                "star_id": state["star"]["id"] if state["star"] else None,
                "star_truncated": state["star_truncated"],
                "conditions": state["conditions"],
            },
        }
        with Session(self.engine) as session:
            # 岗位的公司名、职位名还空着时，用评估从 JD 里读出来的补上；已经填了的不动。
            found = {"company": output.company.strip(), "title": output.title.strip()}
            missing = {
                key: value
                for key, value in found.items()
                if value and not getattr(job, key).strip()
            }
            if missing:
                job_store.update_job(session, state["job_id"], missing)
            # 岗位的处理进度按有没有报告算，保存报告不改应聘进展
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.evaluation,
                job_id=state["job_id"],
                run_id=config["configurable"]["thread_id"],  # 运行编号就是线程编号
                input_hash=state["input_hash"],
                title=report_title(job, output),
                content_md=render_report(
                    job,
                    output,
                    scorecard,
                    today=date.today().isoformat(),
                    recheck_totals=state.get("recheck_totals"),
                ),
                data=data,
                score=scorecard.total,
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


# ---------- Edge ----------


def dispatch(state: RunState) -> list[Send]:
    """把每个岗位各发给一份单岗子图。"""
    return [
        Send(
            "evaluate_job",
            {"job_id": job_id, "conditions": state["conditions"], "force": state["force"]},
        )
        for job_id in state["job_ids"]
    ]


def after_precheck(state: JobState) -> str:
    return END if state.get("results") else "call_model"


def build_evaluation_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    token_budget: int,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """组装评估图。只读取已保存的 State（不执行节点）时，model 可以传 None。"""
    nodes = EvaluationNodes(engine, model, token_budget)
    recheck = getattr(model, "independent_samples", True)

    def after_validate(state: JobState) -> str:
        if state.get("results"):
            return END
        if state.get("feedback"):
            return "call_model"
        total = compute_scorecard(EvaluationOutput.model_validate(state["output"])).total
        return "recheck" if recheck and near_boundary(total) else "save"

    job_graph = StateGraph(JobState, input_schema=JobInput, output_schema=JobOutput)
    job_graph.add_node("load_materials", nodes.load_materials)
    job_graph.add_node("precheck", nodes.precheck)
    job_graph.add_node("call_model", nodes.call_model)
    job_graph.add_node("validate", nodes.validate)
    job_graph.add_node("recheck", nodes.recheck)
    job_graph.add_node("save", nodes.save)
    job_graph.add_edge(START, "load_materials")
    job_graph.add_edge("load_materials", "precheck")
    job_graph.add_conditional_edges("precheck", after_precheck, ["call_model", END])
    job_graph.add_edge("call_model", "validate")
    job_graph.add_conditional_edges(
        "validate", after_validate, ["call_model", "recheck", "save", END]
    )
    job_graph.add_edge("recheck", "save")
    job_graph.add_edge("save", END)

    run_graph = StateGraph(RunState)
    run_graph.add_node("evaluate_job", job_graph.compile())
    run_graph.add_conditional_edges(START, dispatch, ["evaluate_job"])
    run_graph.add_edge("evaluate_job", END)
    return run_graph.compile(checkpointer=checkpointer)
