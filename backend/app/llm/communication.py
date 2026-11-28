"""沟通建议：结论的结构、程序核对、报告正文。

分工：沟通规则写在 backend/prompts/communication.md，Agent 负责看懂对话和起草；
这里核对三件事：它引用的「对方原话」是不是真的在聊天记录里、草稿里有没有材料之外的
数字和技术词、草稿里有没有手机号这类不该出现的信息。岗位阶段只给建议，程序不会去改。
"""

import hashlib
import re
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.core.config import BACKEND_DIR
from app.db.models import STAGE_NAMES as STAGE_NAMES_OF_JOB
from app.db.models import JobStage, Speaker
from app.llm.evidence import QuoteSource, local_quote
from app.llm.facts import unsupported
from app.llm.parsing import InvalidOutput, compact, parse_json_as

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "communication.md").read_text(encoding="utf-8").strip()
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]

FIRST_CONTACT_CHARS = 200  # 规则要求 150 字以内，程序留一点余量
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

STAGE_NAMES = {
    "first_contact": "首次接触",
    "qualification": "对方在了解你的情况",
    "role_clarification": "双方在确认岗位内容",
    "interview_scheduling": "在约面试",
    "compensation": "在谈薪资或 Offer",
    "follow_up": "等待回复或需要跟进",
    "closed": "已结束",
    "unclear": "看不出来",
}
SPEAKER_NAMES = {Speaker.recruiter: "招聘方", Speaker.me: "我", Speaker.transcript: "整段聊天"}
CONFIDENCE_NAMES = {"high": "高", "medium": "中", "low": "低"}

Stage = Literal[
    "first_contact",
    "qualification",
    "role_clarification",
    "interview_scheduling",
    "compensation",
    "follow_up",
    "closed",
    "unclear",
]
SuggestedJobStage = Literal[
    "applied", "assessment", "interviewed", "offer", "rejected", "withdrawn"
]


class RecruiterIntent(BaseModel):
    summary: Text
    evidence: list[str]
    confidence: Literal["high", "medium", "low"]


class NextAction(BaseModel):
    action: Text
    reason: Text


class Draft(BaseModel):
    label: Text
    text: Text
    based_on: Text
    # 措辞核对的提醒：草稿里哪几个字在简历和沟通记录里没有依据。由程序填写。
    cautions: list[str] = Field(default_factory=list)


class AdviceOutput(BaseModel):
    stage: Stage
    recruiter_intent: RecruiterIntent
    open_questions: list[str]
    to_confirm: list[str]
    risks: list[str]
    next_action: NextAction
    drafts: list[Draft]
    avoid: list[str]
    suggested_job_stage: SuggestedJobStage | None
    stage_reason: str = ""


def parse_advice(text: str) -> AdviceOutput:
    """Output Parser：把 Agent 最后的回答变成 AdviceOutput，允许外面包一层 ```json。"""
    return parse_json_as(text, AdviceOutput)


_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_NUMBER = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PLACEHOLDER = re.compile(r"【[^】]*】")
# 约时间时出现的数字（3 点、14:30、5 号、2 天后）是提议，不是关于候选人的事实，不核对。
_TIME = re.compile(
    r"\d{1,2}\s*[:：]\s*\d{2}|\d{4}\s*年|\d+\s*(?:点半?|月|日|号|天|个?工作日|小时|分钟|周)"
)


# 招聘沟通里的通用说法，不是需要出处的技术词。
COMMON_TERMS = "JD HR HRBP Offer OK BOSS HC App PM"


def check_advice(
    output: AdviceOutput,
    *,
    chat: str,
    allowed: str,
    sources: list[QuoteSource] | None = None,
    mask: Callable[[str], str] = str,
) -> AdviceOutput:
    """核对沟通建议。chat 是全部聊天记录的文字；allowed 是草稿可以取材的全部文字。"""
    has_chat = bool(chat.strip())
    problems = []

    located = []
    if has_chat:
        if sources is None:
            sources = [QuoteSource("沟通记录", chat, chat)]
        for quote in output.recruiter_intent.evidence:
            excerpt = compact(quote)
            matches = [
                source for source in sources if excerpt and excerpt in compact(source.visible)
            ]
            if not matches:
                problems.append(
                    f"recruiter_intent.evidence 里的「{mask(quote)[:30]}」不是聊天记录里的原话。"
                    "只能逐字引用对方说过的话，没有就给空数组并把 summary 写成「无法确认」"
                )
            else:
                located.append(local_quote(quote, matches))

    expected = (0, 3) if has_chat and output.stage == "closed" else (2, 3)
    if not expected[0] <= len(output.drafts) <= expected[1]:
        problems.append(f"草稿有 {len(output.drafts)} 条，应该是 {expected[0]}–{expected[1]} 条")

    for number, draft in enumerate(output.drafts, start=1):
        text = _PLACEHOLDER.sub("", draft.text)
        if _PHONE.search(text) or _ID_NUMBER.search(text):
            problems.append(f"第 {number} 条草稿里有手机号或身份证号。草稿里不写联系方式和证件号")
            continue
        if not has_chat and len(draft.text) > FIRST_CONTACT_CHARS:
            problems.append(
                f"第 {number} 条草稿有 {len(draft.text)} 字。第一次打招呼的消息控制在 150 字以内"
            )
        extra = unsupported(_TIME.sub("", text), allowed + "\n" + COMMON_TERMS)
        if extra:
            problems.append(
                f"第 {number} 条草稿里出现了材料和对话中没有的数字或技术词：{'、'.join(extra)}。"
                "只能使用简历、工具查到的材料、JD 和对话里已有的；需要候选人自己填的用【】留出位置"
            )

    if problems:
        raise InvalidOutput("；".join(problems))
    return output.model_copy(
        update={
            "recruiter_intent": output.recruiter_intent.model_copy(update={"evidence": located})
        }
    )


def finalize(output: AdviceOutput, *, has_chat: bool, current_job_stage: str) -> AdviceOutput:
    """程序说了算的部分：没有对话时不谈对方意图；岗位阶段的建议和现状一样时不提。"""
    changes: dict = {}
    if not has_chat:
        changes |= {
            "stage": "first_contact",
            "recruiter_intent": RecruiterIntent(
                summary="还没有对话", evidence=[], confidence="low"
            ),
            "open_questions": [],
            "suggested_job_stage": None,
            "stage_reason": "",
        }
    elif output.suggested_job_stage == current_job_stage:
        changes |= {"suggested_job_stage": None, "stage_reason": ""}
    return output.model_copy(update=changes)


def format_chat(records: list[dict]) -> str:
    """把沟通记录排成给模型看的文字。"""
    return "\n\n".join(
        chat_record_prefix(number, record["speaker"]) + record["text"]
        for number, record in enumerate(records, start=1)
    )


def chat_record_prefix(number: int, speaker: Speaker) -> str:
    separator = "\n" if speaker == Speaker.transcript else ""
    return f"[{number}] {SPEAKER_NAMES[speaker]}：{separator}"


def _section(title: str, items: list[str]) -> list[str]:
    return ["", f"## {title}", ""] + [f"- {item}" for item in items] if items else []


def render_advice_report(output: AdviceOutput, *, company: str, title: str, today: str) -> str:
    """把沟通建议排成给人看的 Markdown。草稿放在代码块里，方便整段复制。"""
    intent = output.recruiter_intent
    lines = [
        f"# 沟通建议：{company} — {title}",
        "",
        f"**日期:** {today}",
        f"**当前阶段:** {STAGE_NAMES[output.stage]}",
        f"**对方意图:** {intent.summary}（置信度{CONFIDENCE_NAMES[intent.confidence]}）",
    ]
    lines += [f"> {quote}" for quote in intent.evidence]
    lines += [
        "",
        "## 下一步",
        "",
        f"**{output.next_action.action}**",
        "",
        output.next_action.reason,
    ]
    lines += _section("对方在等你回答的", output.open_questions)
    lines += _section("发之前先确认", output.to_confirm)
    lines += _section("要小心的", output.risks)

    if output.drafts:
        lines += ["", "## 草稿", "", "草稿由你自己选择、修改和发送。【】里的内容需要你填写。"]
        for number, draft in enumerate(output.drafts, start=1):
            lines += [
                "",
                f"### 草稿 {number}：{draft.label}",
                "",
                "```text",
                draft.text,
                "```",
                "",
                f"依据：{draft.based_on}",
            ]
            if draft.cautions:
                lines += ["", "发之前请核实（下面这些说法在你的简历和沟通记录里没有依据）："]
                lines += [f"- {caution}" for caution in draft.cautions]
    lines += _section("不要说的", output.avoid)

    if output.suggested_job_stage:
        lines += [
            "",
            "## 岗位阶段建议",
            "",
            f"对话显示这个岗位可能已经进入「{STAGE_NAMES_OF_JOB[JobStage(output.suggested_job_stage)]}」。"
            f"{output.stage_reason}",
            "",
            "系统不会自动修改岗位阶段，是否更新由你决定。",
        ]
    return "\n".join(lines) + "\n"


USER_TEMPLATE = """请为下面这个岗位给出沟通建议。今天是 {today}。

<岗位信息>
公司：{company}
职位：{title}
城市：{city}
薪资：{salary}
当前进展：{job_stage}
</岗位信息>

<JD>
{jd}
</JD>

<基础简历>
{resume}
</基础简历>

<沟通记录>
{chat}
</沟通记录>

<候选人这次的要求>
{request}
</候选人这次的要求>"""


def build_user_message(
    *,
    company: str,
    title: str,
    city: str,
    salary: str,
    job_stage: str,
    jd: str,
    resume: str,
    chat: str,
    request: str,
    today: str,
) -> str:
    return USER_TEMPLATE.format(
        today=today,
        company=company or "未填写",
        title=title or "未填写",
        city=city or "未填写",
        salary=salary or "未填写",
        job_stage=job_stage,
        jd=jd.strip(),
        resume=resume.strip(),
        chat=chat.strip() or "（还没有沟通记录，请写第一次打招呼的消息）",
        request=request.strip() or "（没有特别要求）",
    )
