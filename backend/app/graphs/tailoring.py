"""简历定制图。一次运行只针对一个岗位。

    load_materials ─▶ precheck ─▶ retrieve ─▶ propose ─▶ validate ─▶ wording ─▶ review ─▶ save
                         │                       ▲           │                     ┆
                         ▼                       └─ 不合格 ──┤                     ┆ 中断：等用户
                        END                    （最多重试 2 次）                    ┆ 确认接受哪些
                                                             ▼
                                                            END（仍不合格，或不需要修改）

validate 是程序核对：行号、数字和技术词有没有出处。wording 再让模型逐条对照原文核对措辞
（把「参与」写成「独立承接」这类程序查不出来的），发现的问题标在那条修改上，不退回重写。
review 节点调用 interrupt() 把修改方案交给用户，图在这里停下，State 存进 Checkpoint。
用户提交答复后，图从 review 继续。停下期间服务重启也没关系。

基础简历只读。最终简历 = 基础简历 + 用户接受的修改，存为一份新的成果。
"""

import asyncio
import hashlib
import logging
import operator
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db import documents as document_store
from app.db.models import ArtifactKind, DocumentKind, Job
from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE, announce, job_result
from app.llm import privacy
from app.llm.evidence import has_placeholder
from app.llm.factcheck import Item, check_wording
from app.llm.parsing import InvalidOutput
from app.llm.tailoring import (
    RULES_FINGERPRINT,
    SYSTEM_PROMPT,
    Edit,
    TailoringPlan,
    apply_edits,
    build_messages,
    check_plan,
    drop_unchanged,
    jd_queries,
    original_text,
    parse_plan,
    require_placeholder_confirmation,
    unverified_facts,
)
from app.llm.tokens import estimate_tokens
from app.rag.knowledge import KnowledgeBase

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_materials": "读取材料",
    "precheck": "前置检查",
    "retrieve": "检索证据",
    "propose": "生成修改方案",
    "validate": "校验方案",
    "wording": "核对措辞",
    "review": "等你确认",
    "save": "保存简历",
}
MAX_EVIDENCE = 10


class TailoringInput(TypedDict):
    job_id: int
    force: bool


class TailoringState(TailoringInput, total=False):
    job: dict | None  # 岗位快照
    names: dict  # 公司名和职位名，用在成果标题上
    resume: dict | None  # {id, content_md, content_hash}
    other_hashes: list[str]  # 补充资料和 STAR 经历当前版本的指纹
    evaluation_plan: str  # 评估报告里的「定制计划」，没有评估过就是空
    input_hash: str
    evidence: list[dict]  # 检索到的证据：{id, source, text, matched}
    attempts: int
    raw: str
    feedback: str
    plan: dict  # 校验通过的 TailoringPlan
    concerns: list[list[str]]  # 措辞核对的提醒，和 plan.edits 一一对应
    accepted: list[int]  # 用户接受的修改编号，从 1 开始
    overrides: dict[str, str]  # 用户自己改过的新内容：修改编号（字符串）→ 用户写的文字
    results: Annotated[list[dict], operator.add]


def build_proposal(state: TailoringState) -> dict:
    """交给用户确认的内容：逐条修改（带原文、依据、证据）和全部接受后的预览。"""
    plan = TailoringPlan.model_validate(state["plan"])
    resume_md = state["resume"]["content_md"]
    evidence = {item["id"]: item for item in state["evidence"]}
    texts = {evidence_id: item["text"] for evidence_id, item in evidence.items()}
    return {
        "job_id": state["job_id"],
        "summary": plan.summary,
        "edits": [
            {
                "number": number,
                **edit.model_dump(exclude={"evidence_ids"}),
                "original": original_text(resume_md, edit),
                "evidence": [
                    {key: evidence[evidence_id][key] for key in ("id", "source", "text")}
                    for evidence_id in edit.evidence_ids
                ],
                # 材料里找不到出处的数字和技术词。只有标为「需要你确认」的修改才可能有。
                "unverified": unverified_facts(edit, resume_md, texts),
                # 措辞核对的提醒：新内容里哪几个字超出了原文，例如把「参与」写成「独立承接」。
                "concerns": concerns,
            }
            for number, (edit, concerns) in enumerate(
                zip(plan.edits, state["concerns"], strict=True), start=1
            )
        ],
        "gaps": plan.gaps,
        "questions": plan.questions,
        "preview_md": apply_edits(resume_md, plan.edits),
    }


def chosen_edits(plan: TailoringPlan, accepted: list[int], overrides: dict[str, str]) -> list[Edit]:
    """用户接受的修改。用户改过新内容的，用他写的文字（不再核对材料，是他自己写的）。"""
    edits = []
    for number in sorted(accepted):
        edit = plan.edits[number - 1]
        if str(number) in overrides:
            edit = edit.model_copy(update={"new_text": overrides[str(number)]})
        edits.append(edit)
    return edits


def check_choice(accepted: list[int], overrides: dict[int, str], kinds: dict[int, str]) -> dict:
    """工作台和 MCP 共用：用户只能修改接受了的替换或插入项。"""
    unknown = sorted((set(accepted) | set(overrides)) - kinds.keys())
    if unknown:
        raise ValueError(f"方案里没有编号为 {'、'.join(map(str, unknown))} 的修改")
    for number, text in overrides.items():
        if number not in accepted:
            raise ValueError(f"修改 {number} 没有接受，不用改它的内容")
        if kinds[number] not in ("replace", "insert"):
            raise ValueError(f"修改 {number} 没有新内容可改")
        if not text.strip():
            raise ValueError(f"修改 {number} 改成了空的；不想要这条就不要勾选它")
    return {
        "accepted": sorted(set(accepted)),
        "overrides": {str(number): text for number, text in overrides.items()},
    }


def preview_choice(state: TailoringState, accepted: list[int], overrides: dict[int, str]) -> str:
    kinds = {number: edit["kind"] for number, edit in enumerate(state["plan"]["edits"], start=1)}
    return preview_accepted(state, **check_choice(accepted, overrides, kinds))


def preview_accepted(
    state: TailoringState, accepted: list[int], overrides: dict[str, str] | None = None
) -> str:
    """只应用这几条修改（按 1 起的编号）后的简历。确认前给用户预览，和最终生成用同一套规则。"""
    plan = TailoringPlan.model_validate(state["plan"])
    return apply_edits(state["resume"]["content_md"], chosen_edits(plan, accepted, overrides or {}))


@dataclass
class TailoringNodes:
    engine: Engine
    model: BaseChatModel | None
    knowledge: KnowledgeBase
    token_budget: int

    async def load_materials(self, state: TailoringState) -> dict:
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
            output = evaluation.data["output"] if evaluation else {}
            return {
                "job": job.model_dump(),
                "names": {
                    "company": job.company.strip() or output.get("company") or "未知公司",
                    "title": job.title.strip() or output.get("title") or "未知职位",
                },
                "resume": resume
                and {
                    "id": resume.id,
                    "content_md": resume.content_md,
                    "content_hash": resume.content_hash,
                },
                "other_hashes": [document.content_hash if document else "" for document in others],
                "evaluation_plan": output.get("e_plan", ""),
            }

    async def precheck(self, state: TailoringState) -> dict:
        announce(state["job_id"], "precheck")
        job, resume = state["job"], state["resume"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not job["jd_text"].strip():
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有完整 JD，请先补全 JD 再定制简历"
            )
        if resume is None:
            return job_result(state["job_id"], "blocked", "还没有保存基础简历，请先保存简历再定制")

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
            state["evaluation_plan"],  # 重新评估后定制计划变了，定制也要重做
        ]
        input_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        if not state["force"]:
            with Session(self.engine) as session:
                existing = artifact_store.find_by_input(
                    session, ArtifactKind.tailored_resume, state["job_id"], input_hash
                )
            if existing:
                return job_result(
                    state["job_id"],
                    "reused",
                    "JD 和材料都没有变化，直接使用已有的定制简历",
                    existing.id,
                )
        return {"input_hash": input_hash}

    async def retrieve(self, state: TailoringState) -> dict:
        """RAG 的检索一步：把 JD 的每条要求当作查询，到补充资料和 STAR 经历里找证据。

        基础简历整份都会给模型，不需要再检索。
        """
        announce(state["job_id"], "retrieve")
        queries = jd_queries(state["job"]["jd_text"])
        # Embedding 和 Rerank 是本机的计算密集任务，放到线程里跑，不卡住其他请求。
        passages = await asyncio.to_thread(
            self.knowledge.search,
            queries,
            kinds=[DocumentKind.supplement, DocumentKind.star],
            limit=MAX_EVIDENCE,
        )
        logger.info(
            "定制岗位 %s：%s 条 JD 要求，检索到 %s 个证据片段",
            state["job_id"],
            len(queries),
            len(passages),
        )
        return {
            "evidence": [
                {
                    "id": f"E{number}",
                    "source": passage.source,
                    "text": passage.text,
                    "matched": list(passage.matched),
                }
                for number, passage in enumerate(passages, start=1)
            ]
        }

    async def propose(self, state: TailoringState) -> dict:
        announce(state["job_id"], "propose")
        evidence_block = "\n\n".join(
            f"[{item['id']}] 出处：{item['source']}\n"
            f"对应的 JD 要求：{'；'.join(item['matched'])}\n{item['text']}"
            for item in state["evidence"]
        )
        messages = list(
            build_messages(
                Job(**state["job"]),
                resume_md=state["resume"]["content_md"],
                evidence_block=evidence_block,
                evaluation_plan=state["evaluation_plan"],
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
            "定制岗位 %s：第 %s 次调用模型 input_tokens=%s output_tokens=%s",
            state["job_id"],
            attempt,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        return {"raw": str(reply.text), "attempts": attempt}

    async def validate(self, state: TailoringState) -> dict:
        announce(state["job_id"], "validate")
        resume_md = state["resume"]["content_md"]

        def mask(text: str) -> str:
            return privacy.mask_for_model(self.model, text)

        try:
            plan = drop_unchanged(parse_plan(state["raw"]), resume_md)
            plan = require_placeholder_confirmation(plan)
            check_plan(
                plan, resume_md, {item["id"]: item["text"] for item in state["evidence"]}, mask=mask
            )
        except InvalidOutput as error:
            feedback = mask(str(error))
            logger.warning("定制岗位 %s：修改方案不合格：%s", state["job_id"], feedback)
            if state["attempts"] > MAX_RETRIES:
                return job_result(
                    state["job_id"],
                    "failed",
                    f"模型给出的修改方案连续 {state['attempts']} 次不合格：{feedback}",
                )
            return {"feedback": feedback}
        if not plan.edits:
            return job_result(
                state["job_id"],
                "no_changes",
                f"模型认为这份简历不需要为这个岗位修改：{plan.summary}",
            )
        return {"plan": plan.model_dump(), "feedback": ""}

    async def wording(self, state: TailoringState) -> dict:
        """让模型逐条对照原文，找出新内容里材料不支持的说法。发现的问题不退回重写，
        标在那条修改上，由用户在确认时决定。"""
        announce(state["job_id"], "wording")
        plan = TailoringPlan.model_validate(state["plan"])
        resume_md = state["resume"]["content_md"]
        evidence = {item["id"]: item["text"] for item in state["evidence"]}
        # 已经标为「需要你确认」的修改不用再核对：它本来就交给用户判断。
        written = [
            index
            for index, edit in enumerate(plan.edits)
            if edit.kind in ("replace", "insert") and edit.fact_check != "needs_confirmation"
        ]
        items = [
            Item(
                basis="\n".join(
                    [
                        original_text(resume_md, plan.edits[index]),
                        *[evidence[evidence_id] for evidence_id in plan.edits[index].evidence_ids],
                    ]
                ),
                text=plan.edits[index].new_text,
            )
            for index in written
        ]
        found = await check_wording(
            self.model, material=resume_md, items=items, label=f"定制岗位 {state['job_id']}"
        )
        concerns = [
            ["新内容含隐私占位符。请对照原文修改，或拒绝这条以保留原文。"]
            if has_placeholder(edit.new_text)
            else []
            for edit in plan.edits
        ]
        for index, cautions in zip(written, found, strict=True):
            concerns[index] = cautions
        return {"concerns": concerns}

    async def review(self, state: TailoringState) -> dict:
        """人工确认。interrupt() 让图停在这里；用户答复后，这个节点从头再执行一遍，
        这时 interrupt() 直接返回用户的答复。所以节点里不能有副作用。"""
        announce(state["job_id"], "review")
        answer = interrupt(build_proposal(state))
        return {
            "accepted": sorted(set(answer["accepted"])),
            "overrides": answer.get("overrides", {}),
        }

    async def save(self, state: TailoringState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        if not state["accepted"]:
            return job_result(state["job_id"], "declined", "没有接受任何修改，未生成定制简历")

        plan = TailoringPlan.model_validate(state["plan"])
        overrides = state.get("overrides", {})
        edits = chosen_edits(plan, state["accepted"], overrides)
        names = state["names"]
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.tailored_resume,
                job_id=state["job_id"],
                run_id=config["configurable"]["thread_id"],
                input_hash=state["input_hash"],
                title=f"定制简历：{names['company']} — {names['title']}",
                content_md=apply_edits(state["resume"]["content_md"], edits),
                data={
                    "proposal": build_proposal(state),
                    "accepted": state["accepted"],
                    "overrides": overrides,
                    "inputs": {"resume_id": state["resume"]["id"]},
                },
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


def after_precheck(state: TailoringState) -> str:
    return END if state.get("results") else "retrieve"


def after_validate(state: TailoringState) -> str:
    if state.get("results"):
        return END
    return "propose" if state.get("feedback") else "wording"


def build_tailoring_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    knowledge: KnowledgeBase,
    token_budget: int,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """组装定制图。只读取 State 或只提交用户答复（不调用模型）时，model 可以传 None。"""
    nodes = TailoringNodes(engine, model, knowledge, token_budget)

    graph = StateGraph(TailoringState, input_schema=TailoringInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load_materials")
    graph.add_edge("load_materials", "precheck")
    graph.add_conditional_edges("precheck", after_precheck, ["retrieve", END])
    graph.add_edge("retrieve", "propose")
    graph.add_edge("propose", "validate")
    graph.add_conditional_edges("validate", after_validate, ["propose", "wording", END])
    graph.add_edge("wording", "review")
    graph.add_edge("review", "save")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
