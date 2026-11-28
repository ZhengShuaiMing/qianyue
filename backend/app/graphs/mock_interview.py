"""模拟面试图。一次运行是一场面试，只针对一个岗位。

    load_context ─▶ precheck ─▶ ask ─▶ listen ─┬─▶ ask（下一轮）
                       │         ▲      ┆      │
                       ▼         └──────┆──────┘
                      END               ┆ 中断：等用户回答
                                        │
                           （问满轮数，或用户提前结束）
                                        ▼
                                     review ─▶ validate ─▶ wording ─▶ save ─▶ END
                                        ▲          │
                                        └─ 不合格 ─┘（最多重试 2 次）

ask 节点让模型以面试官身份说一轮话，逐字推给前端。listen 节点调用 interrupt() 停下来等回答，
整场问答存在 Checkpoint 里，所以断线、关页面、服务重启之后都能接着面。
一题都没答就结束时，不调用模型，也不生成复盘。
"""

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
from langgraph.types import interrupt
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import artifacts as artifact_store
from app.db import documents as document_store
from app.db.models import ArtifactKind, DocumentKind, Job
from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE, announce, job_result
from app.llm import privacy
from app.llm.factcheck import Item, check_wording
from app.llm.mock_interview import (
    INTERVIEWER_PROMPT,
    MAX_TURNS,
    ReviewOutput,
    answered,
    build_interviewer_messages,
    build_review_messages,
    check_review,
    parse_review,
    render_review_report,
)
from app.llm.parsing import InvalidOutput
from app.llm.tokens import estimate_tokens

logger = logging.getLogger(__name__)

NODE_LABELS = {
    "load_context": "读取材料",
    "precheck": "前置检查",
    "ask": "面试官提问",
    "listen": "等你回答",
    "review": "复盘",
    "validate": "核对复盘",
    "wording": "核对措辞",
    "save": "保存复盘",
}


class MockInput(TypedDict):
    job_id: int
    conditions: str  # 候选人这次的要求：面试官身份、轮次、想重点练什么
    force: bool


class MockState(MockInput, total=False):
    job: dict | None
    names: dict
    resume: str | None
    prepared: list[str]  # 面试准备里预测的问题，没有就是空
    turns: list[dict]  # 整场问答：{question, answer}。还没回答的那一轮 answer 是 None
    ended: bool  # 用户提前结束
    attempts: int
    raw: str
    feedback: str
    review: dict
    results: Annotated[list[dict], operator.add]


@dataclass
class MockNodes:
    engine: Engine
    model: BaseChatModel | None
    token_budget: int

    async def load_context(self, state: MockState) -> dict:
        announce(state["job_id"], "load_context")
        with Session(self.engine) as session:
            job = session.get(Job, state["job_id"])
            if job is None or job.deleted_at:
                return {"job": None, "resume": None}
            resume = document_store.get_current(session, DocumentKind.resume)
            evaluation = artifact_store.latest_evaluation(session, job.id)
            prep = artifact_store.latest_artifact(session, ArtifactKind.interview_prep, job.id)
            output = evaluation.data["output"] if evaluation else {}
            questions = prep.data["output"]["questions"] if prep else []
            return {
                "job": job.model_dump(),
                "names": {
                    "company": job.company.strip() or output.get("company") or "未知公司",
                    "title": job.title.strip() or output.get("title") or "未知职位",
                },
                "resume": resume and resume.content_md,
                "prepared": [question["question"] for question in questions],
                "turns": [],
            }

    async def precheck(self, state: MockState) -> dict:
        announce(state["job_id"], "precheck")
        job, resume = state["job"], state["resume"]
        if job is None:
            return job_result(state["job_id"], "blocked", "岗位不存在或已删除")
        if not job["jd_text"].strip():
            return job_result(
                state["job_id"], "blocked", "这个岗位还没有完整 JD，请先补全 JD 再模拟面试"
            )
        if resume is None:
            return job_result(
                state["job_id"], "blocked", "还没有保存基础简历，面试官需要根据你的简历提问"
            )
        tokens = estimate_tokens(INTERVIEWER_PROMPT + job["jd_text"] + resume)
        if tokens > self.token_budget:
            return job_result(
                state["job_id"],
                "blocked",
                f"JD 和简历约 {tokens} Token，超过输入预算 {self.token_budget}。"
                "请精简后再试，或调大 QIANYUE_LLM_INPUT_TOKEN_BUDGET",
            )
        return {}

    async def ask(self, state: MockState) -> dict:
        """面试官说一轮话，边生成边推给前端。"""
        announce(state["job_id"], "ask")
        turns = state["turns"]
        messages = build_interviewer_messages(
            company=state["names"]["company"],
            title=state["names"]["title"],
            jd=state["job"]["jd_text"],
            resume=state["resume"],
            prepared=state["prepared"],
            request=state["conditions"],
            turns=turns,
        )
        writer = get_stream_writer()
        total = None
        async for chunk in self.model.astream(messages):
            total = chunk if total is None else total + chunk
            if chunk.text:
                writer({"job_id": state["job_id"], "token": str(chunk.text)})
        question = str(total.text).strip() if total is not None else ""
        usage = (total.usage_metadata if total is not None else None) or {}
        logger.info(
            "模拟面试岗位 %s：第 %s 轮提问 %s 字 input_tokens=%s output_tokens=%s",
            state["job_id"],
            len(turns) + 1,
            len(question),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        if not question:
            raise RuntimeError("模型没有给出这一轮的问题，可以恢复这次运行重试")
        return {"turns": [*turns, {"question": question, "answer": None}]}

    async def listen(self, state: MockState) -> dict:
        """等用户回答。interrupt() 让图停在这里；用户答复后，这个节点从头再执行一遍，
        这时 interrupt() 直接返回答复。"""
        announce(state["job_id"], "listen")
        turns = state["turns"]
        reply = interrupt(
            {
                "job_id": state["job_id"],
                "turn": len(turns),
                "total": MAX_TURNS,
                "question": turns[-1]["question"],
                "transcript": turns[:-1],
            }
        )
        if reply.get("end"):
            return {"ended": True}
        return {"turns": [*turns[:-1], {**turns[-1], "answer": reply["answer"]}]}

    async def review(self, state: MockState) -> dict:
        announce(state["job_id"], "review")
        messages = build_review_messages(
            company=state["names"]["company"],
            title=state["names"]["title"],
            jd=state["job"]["jd_text"],
            resume=state["resume"],
            turns=state["turns"],
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
            "模拟面试岗位 %s：第 %s 次复盘 input_tokens=%s output_tokens=%s",
            state["job_id"],
            attempt,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        return {"raw": str(reply.text), "attempts": attempt}

    async def validate(self, state: MockState) -> dict:
        announce(state["job_id"], "validate")
        turns = state["turns"]
        review_turns = answered(turns).values()

        def mask(text: str) -> str:
            return privacy.mask_for_model(self.model, text)

        allowed = "\n".join(
            [
                state["names"]["company"],
                state["names"]["title"],
                state["job"]["jd_text"],
                state["resume"],
                *[turn["question"] for turn in review_turns],
                *[turn["answer"] for turn in review_turns],
            ]
        )
        try:
            output = check_review(
                parse_review(state["raw"]), turns=turns, allowed=mask(allowed), mask=mask
            )
        except InvalidOutput as error:
            feedback = mask(str(error))
            logger.warning("模拟面试岗位 %s：复盘不合格：%s", state["job_id"], feedback)
            if state["attempts"] > MAX_RETRIES:
                return job_result(
                    state["job_id"],
                    "failed",
                    f"模型给出的复盘连续 {state['attempts']} 次不合格：{feedback}",
                )
            return {"feedback": feedback}
        # cautions 由程序填写，模型自己写的不算。
        cleared = [turn.model_copy(update={"cautions": []}) for turn in output.turns]
        output = output.model_copy(update={"turns": cleared})
        return {"review": output.model_dump(), "feedback": ""}

    async def wording(self, state: MockState) -> dict:
        """让模型核对每一轮「更好的回答」的措辞：有没有替用户说了简历和回答里没有的话。
        发现的问题写在那一轮上，不退回重写。"""
        announce(state["job_id"], "wording")
        review = ReviewOutput.model_validate(state["review"])
        turns = state["turns"]
        items = [
            Item(
                basis=f"面试官：{turns[item.turn - 1]['question']}\n"
                f"候选人：{turns[item.turn - 1]['answer']}",
                text=item.better_answer,
            )
            for item in review.turns
        ]
        found = await check_wording(
            self.model,
            material=state["resume"],
            items=items,
            label=f"模拟面试岗位 {state['job_id']}",
        )
        checked = [
            item.model_copy(update={"cautions": cautions})
            for item, cautions in zip(review.turns, found, strict=True)
        ]
        return {"review": review.model_copy(update={"turns": checked}).model_dump()}

    async def save(self, state: MockState, config: RunnableConfig) -> dict:
        announce(state["job_id"], "save")
        names = state["names"]
        run_id = config["configurable"]["thread_id"]
        with Session(self.engine) as session:
            artifact = artifact_store.save_artifact(
                session,
                kind=ArtifactKind.mock_interview,
                job_id=state["job_id"],
                run_id=run_id,
                # 每场面试都是新的，不和别的场次去重；同一场重复保存时覆盖自己。
                input_hash=f"mock:{run_id}",
                title=f"模拟面试复盘：{names['company']} — {names['title']}",
                content_md=render_review_report(
                    ReviewOutput.model_validate(state["review"]),
                    company=names["company"],
                    title=names["title"],
                    today=date.today().isoformat(),
                    turns=state["turns"],
                ),
                data={"review": state["review"], "turns": state["turns"]},
            )
            return job_result(state["job_id"], "done", artifact_id=artifact.id)


def after_precheck(state: MockState) -> str:
    return END if state.get("results") else "ask"


def after_listen(state: MockState) -> str:
    turns = state["turns"]
    if not state.get("ended") and len(turns) < MAX_TURNS:
        return "ask"
    return "review" if answered(turns) else "finish_empty"


async def finish_empty(state: MockState) -> dict:
    return job_result(state["job_id"], "empty", "一题都没有回答，没有生成复盘")


def after_validate(state: MockState) -> str:
    if state.get("results"):
        return END
    return "review" if state.get("feedback") else "wording"


def build_mock_interview_graph(
    *,
    engine: Engine,
    model: BaseChatModel | None,
    token_budget: int,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    nodes = MockNodes(engine, model, token_budget)

    graph = StateGraph(MockState, input_schema=MockInput)
    for name in NODE_LABELS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_node("finish_empty", finish_empty)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "precheck")
    graph.add_conditional_edges("precheck", after_precheck, ["ask", END])
    graph.add_edge("ask", "listen")
    graph.add_conditional_edges("listen", after_listen, ["ask", "review", "finish_empty"])
    graph.add_edge("finish_empty", END)
    graph.add_edge("review", "validate")
    graph.add_conditional_edges("validate", after_validate, ["review", "wording", END])
    graph.add_edge("wording", "save")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
