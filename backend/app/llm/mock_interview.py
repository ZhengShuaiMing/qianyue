"""模拟面试：面试官每一轮的消息、复盘的结构、程序核对、复盘报告。

分工：面试官怎么问写在 backend/prompts/mock_interview.md，怎么复盘写在 mock_review.md。
这里核对复盘的三件事：每个回答过的轮次都评了、引用的「原话」真的是用户说过的、
「更好的回答」里没有简历和用户回答之外的数字和技术词。
中文措辞（例如替用户说「都是我独立完成的」）程序查不出来，由 app/llm/factcheck.py 再核对一次，
发现的问题写在那一轮的 cautions 里。
"""

import re
from collections.abc import Callable
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, StringConstraints

from app.core.config import BACKEND_DIR
from app.llm.evidence import local_quote, prefixed_source
from app.llm.facts import unsupported
from app.llm.parsing import InvalidOutput, compact, parse_json_as

PROMPTS = BACKEND_DIR / "prompts"
INTERVIEWER_PROMPT = (PROMPTS / "mock_interview.md").read_text(encoding="utf-8").strip()
REVIEW_PROMPT = (PROMPTS / "mock_review.md").read_text(encoding="utf-8").strip()

MAX_TURNS = 8  # 面试官一共说几轮，最后一轮是「你有什么想问我的吗」
MAX_ANSWER_CHARS = 4000


def interview_answer(text: str) -> dict:
    if not text.strip():
        raise ValueError("回答不能为空。想跳过这一题可以直接说「跳过」")
    if len(text) > MAX_ANSWER_CHARS:
        raise ValueError(f"回答太长了，请控制在 {MAX_ANSWER_CHARS} 字以内")
    return {"answer": text.strip()}


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RATING_NAMES = {"strong": "回答得好", "ok": "基本可以", "weak": "需要重点准备"}


# ---------- 面试官 ----------

CONTEXT_TEMPLATE = """<岗位信息>
公司：{company}
职位：{title}
</岗位信息>

<JD>
{jd}
</JD>

<基础简历>
{resume}
</基础简历>

<备选问题>
{prepared}
</备选问题>

<候选人这次的要求>
{request}
</候选人这次的要求>

面试现在开始。{turn_note}"""

ANSWER_TEMPLATE = """<候选人的回答>
{answer}
</候选人的回答>

{turn_note}"""


def _turn_note(turn: int) -> str:
    note = f"现在是第 {turn} 轮，一共 {MAX_TURNS} 轮。"
    return note + ("这是最后一轮。" if turn == MAX_TURNS else "") + "请说面试官这一轮的话。"


def build_interviewer_messages(
    *,
    company: str,
    title: str,
    jd: str,
    resume: str,
    prepared: list[str],
    request: str,
    turns: list[dict],
) -> list[BaseMessage]:
    """面试官这一轮看到的对话。turns 是此前已经问答完的轮次：{question, answer}。"""
    context = CONTEXT_TEMPLATE.format(
        company=company,
        title=title,
        jd=jd.strip(),
        resume=resume.strip(),
        prepared="\n".join(f"- {item}" for item in prepared) or "（没有）",
        request=request.strip() or "（没有特别要求）",
        turn_note=_turn_note(1) if not turns else "",
    )
    messages: list[BaseMessage] = [SystemMessage(INTERVIEWER_PROMPT), HumanMessage(context)]
    for number, turn in enumerate(turns, start=1):
        messages.append(AIMessage(turn["question"]))
        note = _turn_note(number + 1) if number == len(turns) else ""
        messages.append(HumanMessage(ANSWER_TEMPLATE.format(answer=turn["answer"], turn_note=note)))
    return messages


# ---------- 复盘 ----------


class Good(BaseModel):
    quote: Text
    comment: Text


class Problem(BaseModel):
    quote: str = ""
    issue: Text
    fix: Text


class TurnReview(BaseModel):
    turn: int
    rating: Literal["strong", "ok", "weak"]
    good: list[Good] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    better_answer: Text
    # 措辞核对的提醒：「更好的回答」里哪几个字在简历和候选人的回答里没有依据。由程序填写。
    cautions: list[str] = Field(default_factory=list)


class Priority(BaseModel):
    action: Text
    reason: Text


class ReviewOutput(BaseModel):
    summary: Text
    strengths: list[Text] = Field(default_factory=list)
    turns: list[TurnReview]
    priorities: list[Priority] = Field(min_length=1)


def parse_review(text: str) -> ReviewOutput:
    return parse_json_as(text, ReviewOutput)


_PLACEHOLDER = re.compile(r"【[^】]*】")


def answered(turns: list[dict]) -> dict[int, dict]:
    """回答过的轮次：轮次编号 → {question, answer}。"""
    return {
        number: turn for number, turn in enumerate(turns, start=1) if turn.get("answer") is not None
    }


def check_review(
    output: ReviewOutput, *, turns: list[dict], allowed: str, mask: Callable[[str], str] = str
) -> ReviewOutput:
    """核对复盘。turns 是整场问答；allowed 是「更好的回答」可以取材的全部文字。"""
    real = answered(turns)
    problems = []

    reviewed = [item.turn for item in output.turns]
    extra = sorted(set(reviewed) - set(real))
    missing = sorted(set(real) - set(reviewed))
    repeated = sorted({turn for turn in reviewed if reviewed.count(turn) > 1})
    if extra:
        problems.append(
            f"评了候选人没有回答的轮次：{'、'.join(map(str, extra))}。只评实际回答过的轮次"
        )
    if missing:
        problems.append(f"这些轮次没有评：{'、'.join(map(str, missing))}")
    if repeated:
        problems.append(f"这些轮次评了不止一次：{'、'.join(map(str, repeated))}")

    checked = []
    for item in output.turns:
        if item.turn not in real:
            continue
        source = prefixed_source(
            f"第 {item.turn} 轮回答", real[item.turn]["answer"].strip(), "候选人：", mask
        )
        answer = compact(source.visible)
        quotes = [good.quote for good in item.good] + [bad.quote for bad in item.problems]
        for quote in quotes:
            if quote.strip() and compact(quote) not in answer:
                problems.append(
                    f"第 {item.turn} 轮引用的「{mask(quote)[:30]}」不是候选人这一轮的原话。"
                    "quote 必须逐字照抄，不能改写或拼接；指的是漏讲的内容时 quote 给空字符串"
                )
        unknown = unsupported(_PLACEHOLDER.sub("", item.better_answer), allowed)
        if unknown:
            problems.append(
                f"第 {item.turn} 轮的 better_answer 里出现了简历和候选人回答中没有的数字或技术词："
                f"{'、'.join(unknown)}。只能用已有的事实，缺的用【】留出位置，步骤数和时长用中文写"
            )
        checked.append(
            item.model_copy(
                update={
                    "good": [
                        good.model_copy(update={"quote": local_quote(good.quote, [source])})
                        for good in item.good
                    ],
                    "problems": [
                        bad.model_copy(update={"quote": local_quote(bad.quote, [source])})
                        for bad in item.problems
                    ],
                }
            )
        )

    if problems:
        raise InvalidOutput("；".join(problems))
    return output.model_copy(update={"turns": checked})


def _quote_block(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def render_review_report(
    output: ReviewOutput, *, company: str, title: str, today: str, turns: list[dict]
) -> str:
    """把复盘排成给人看的 Markdown。每一轮带上问题和用户的完整回答，方便对照。"""
    real = answered(turns)
    lines = [
        f"# 模拟面试复盘：{company} — {title}",
        "",
        f"**日期:** {today}",
        f"**回答了:** {len(real)} 轮",
        f"**总评:** {output.summary}",
    ]
    if output.strengths:
        lines += ["", "## 做得好的", "", *[f"- {item}" for item in output.strengths]]
    lines += ["", "## 面试前最该改的", ""]
    lines += [
        f"{index}. **{item.action}** {item.reason}"
        for index, item in enumerate(output.priorities, start=1)
    ]

    lines += ["", "## 逐轮复盘"]
    for item in sorted(output.turns, key=lambda review: review.turn):
        turn = real[item.turn]
        lines += [
            "",
            f"### 第 {item.turn} 轮：{RATING_NAMES[item.rating]}",
            "",
            f"**面试官:** {' '.join(turn['question'].split())}",
            "",
            "**你的回答:**",
            "",
            *_quote_block(turn["answer"]),
        ]
        if item.good:
            lines += ["", "**好的地方**", ""]
            lines += [f"- 「{good.quote}」{good.comment}" for good in item.good]
        if item.problems:
            lines += ["", "**问题**", ""]
            for bad in item.problems:
                quoted = f"「{bad.quote.strip()}」" if bad.quote.strip() else ""
                lines.append(f"- {quoted}{bad.issue} 改法：{bad.fix}")
        lines += ["", "**更好的回答**", "", item.better_answer]
        if item.cautions:
            lines += ["", "**用之前请核实**（下面这些说法在你的简历和回答里没有依据）", ""]
            lines += [f"- {caution}" for caution in item.cautions]

    lines += [
        "",
        "---",
        "",
        "「更好的回答」只用了你简历里和你在这场面试里说过的内容，【】里的部分需要你自己补充。",
    ]
    return "\n".join(lines) + "\n"


REVIEW_TEMPLATE = """请复盘下面这场模拟面试。

<岗位信息>
公司：{company}
职位：{title}
</岗位信息>

<JD>
{jd}
</JD>

<基础简历>
{resume}
</基础简历>

<问答记录>
{transcript}
</问答记录>"""


def format_transcript(turns: list[dict]) -> str:
    return "\n\n".join(
        f"[第 {number} 轮]\n面试官：{turn['question'].strip()}\n候选人：{turn['answer'].strip()}"
        for number, turn in answered(turns).items()
    )


def build_review_messages(
    *, company: str, title: str, jd: str, resume: str, turns: list[dict]
) -> list[BaseMessage]:
    human = REVIEW_TEMPLATE.format(
        company=company,
        title=title,
        jd=jd.strip(),
        resume=resume.strip(),
        transcript=format_transcript(turns),
    )
    return [SystemMessage(REVIEW_PROMPT), HumanMessage(human)]
