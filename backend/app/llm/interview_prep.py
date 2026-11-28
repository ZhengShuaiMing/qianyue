"""面试准备：输出结构、程序核对、报告正文。

分工：出题和准备规则写在 backend/prompts/interview_prep.md，模型负责预测问题和整理回答要点；
这里核对三件事：它引用的 JD、简历和面经原文是不是真的原文、回答要点里有没有材料之外的
数字和技术词、引用的证据编号存不存在。
面经是联网搜到的（app/tools/experiences.py）：只用来出题，不能当作候选人的经历写进回答要点。
"""

import hashlib
from collections.abc import Callable
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, StringConstraints

from app.core.config import BACKEND_DIR
from app.llm.evidence import QuoteSource, local_quote
from app.llm.facts import unsupported
from app.llm.parsing import InvalidOutput, compact, parse_json_as
from app.llm.tokens import trim_to_tokens

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "interview_prep.md").read_text(encoding="utf-8").strip()
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]

QUESTION_RANGE = (8, 16)  # 规则要求 10–14 道，程序留一点余量
MIN_QUOTE_CHARS = 4  # 引用的原文至少这么长，太短等于没引用
REPORT_TOKENS = 2000  # 评估和公司研究各自最多给模型这么多

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Category = Literal["technical", "project", "behavioral", "risk"]
Source = Literal["jd", "resume", "gap", "general", "web"]
MAX_WEB_QUESTIONS = 4

CATEGORY_NAMES = {
    "technical": "技术与专业",
    "project": "项目深挖",
    "behavioral": "行为问题",
    "risk": "风险与缺口",
}
SOURCE_NAMES = {
    "jd": "根据 JD 推测",
    "resume": "根据你的简历推测",
    "gap": "根据岗位要求和你的经历之间的差距推测",
    "general": "这类岗位的常见问题",
    "web": "网上的面经里有人被问到",
}
QUOTE_FROM = {"jd": "JD", "resume": "简历", "web": "面经"}


class Question(BaseModel):
    category: Category
    question: Text
    source: Source
    quote: str = ""
    web_id: str = ""  # source 是 web 时，出自哪一篇面经（W1、W2……）
    why: Text
    answer_points: list[Text] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    review_topics: list[str] = Field(default_factory=list)
    follow_ups: list[Text] = Field(min_length=1)
    to_confirm: str = ""


class AskThem(BaseModel):
    question: Text
    why: Text


class PlanItem(BaseModel):
    task: Text
    reason: Text


class PrepOutput(BaseModel):
    summary: Text
    focus: list[Text] = Field(min_length=1)
    questions: list[Question]
    ask_them: list[AskThem] = Field(min_length=1)
    plan: list[PlanItem] = Field(min_length=1)


def parse_prep(text: str) -> PrepOutput:
    return parse_json_as(text, PrepOutput)


def check_prep(
    output: PrepOutput,
    *,
    jd: str,
    resume: str,
    evidence: dict[str, str],
    allowed: str,
    experiences: dict[str, str] | None = None,
    mask: Callable[[str], str] = str,
) -> PrepOutput:
    """核对面试准备。evidence 是证据编号到正文；allowed 是候选人自己的全部材料；
    experiences 是面经编号到正文（没有搜到就是空）。"""
    problems = []
    experiences = experiences or {}
    from_web = sum(question.source == "web" for question in output.questions)
    if from_web > MAX_WEB_QUESTIONS:
        problems.append(f"来自面经的题有 {from_web} 道，最多 {MAX_WEB_QUESTIONS} 道")

    low, high = QUESTION_RANGE
    if not low <= len(output.questions) <= high:
        problems.append(f"问题有 {len(output.questions)} 道，应该是 10–14 道")
    missing = [
        name
        for category, name in CATEGORY_NAMES.items()
        if all(question.category != category for question in output.questions)
    ]
    if missing:
        problems.append(f"缺少这些类别的问题：{'、'.join(missing)}。四类都至少要有一道")

    originals = {
        "jd": QuoteSource("JD", jd, mask(jd)),
        "resume": QuoteSource("简历", resume, mask(resume)),
    }
    web = {name: QuoteSource(name, text, mask(text)) for name, text in experiences.items()}
    questions = []
    for number, question in enumerate(output.questions, start=1):
        quote = compact(question.quote)
        source = originals.get(question.source)
        if question.source == "web":
            source = web.get(question.web_id)
            if source is None:
                problems.append(
                    f"第 {number} 题的 source 是 web，但 web_id「{question.web_id}」"
                    "不是给过的面经。"
                    f"可用的面经只有：{'、'.join(experiences) or '没有'}。"
                    "没有面经时不要出 web 题，改成其他来源"
                )
            elif len(quote) < MIN_QUOTE_CHARS or quote not in compact(source.visible):
                problems.append(
                    f"第 {number} 题的 quote「{mask(question.quote)[:30]}」"
                    f"不是面经 {question.web_id} 里的原文。"
                    "请逐字照抄面经里写这道题的那一句，面经里没有这道题就不要说是面经里的"
                )
        elif source is not None:
            name = QUOTE_FROM[question.source]
            if len(quote) < MIN_QUOTE_CHARS:
                problems.append(
                    f"第 {number} 题的 source 是 {question.source}，quote 要填{name}原文"
                )
            elif quote not in compact(source.visible):
                problems.append(
                    f"第 {number} 题的 quote「{mask(question.quote)[:30]}」不是{name}里的原文。"
                    f"请逐字照抄{name}里的一句或半句，不要改写或拼接"
                )

        if (
            source is not None
            and len(quote) >= MIN_QUOTE_CHARS
            and quote in compact(source.visible)
        ):
            question = question.model_copy(update={"quote": local_quote(question.quote, [source])})
        questions.append(question)

        unknown = [item for item in question.evidence_ids if item not in evidence]
        if unknown:
            problems.append(
                f"第 {number} 题引用了不存在的证据 {'、'.join(unknown)}。"
                f"可用的证据只有：{'、'.join(evidence) or '没有'}"
            )

        extra = unsupported("\n".join(question.answer_points), allowed)
        if extra:
            problems.append(
                f"第 {number} 题的 answer_points 里出现了材料中没有的数字或技术词："
                f"{'、'.join(extra)}。要点只写候选人材料里有的；需要复习的知识点放进 "
                "review_topics，步骤数和时长用中文写"
            )

    if problems:
        raise InvalidOutput("；".join(problems))
    return output.model_copy(update={"questions": questions})


def finalize(output: PrepOutput) -> PrepOutput:
    """缺口题和常见题没有可引用的原文，模型填了也不显示。只有面经题才有 web_id。"""
    questions = [
        question.model_copy(
            update={
                "quote": question.quote if question.source in QUOTE_FROM else "",
                "web_id": question.web_id if question.source == "web" else "",
            }
        )
        for question in output.questions
    ]
    return output.model_copy(update={"questions": questions})


def _bullets(title: str, items: list[str]) -> list[str]:
    return [f"**{title}**", "", *[f"- {item}" for item in items], ""] if items else []


def _origin_note(output: PrepOutput, experiences: list[dict] | None) -> str:
    """报告开头的一句话：这些题是怎么来的。experiences 为 None 表示没有去查面经。"""
    from_web = sum(question.source == "web" for question in output.questions)
    tail = "回答要点只用了你材料里的真实信息。"
    if from_web:
        return (
            f"其中 {from_web} 道参考了网上的面经（题目下面标了出处），其余是根据 JD 和你的材料"
            f"推测的。面经是网友写的，不一定准确，也不一定是这个岗位的。{tail}"
        )
    base = f"下面的问题是根据 JD 和你的材料推测的，不是真实面经。{tail}"
    if experiences is None:
        return base
    if not experiences:
        return base + "联网搜过这家公司的面经，没有找到。"
    return base + "联网搜到了提到这家公司的内容，但里面没有可以直接用的面试题。"


def render_prep_report(
    output: PrepOutput,
    *,
    company: str,
    title: str,
    today: str,
    sources: dict[str, str],
    experiences: list[dict] | None = None,
) -> str:
    """把面试准备排成给人看的 Markdown。sources 是证据编号到出处；experiences 是搜到的面经。"""
    to_confirm = [question for question in output.questions if question.to_confirm.strip()]
    found = {item["id"]: item for item in experiences or []}
    lines = [
        f"# 面试准备：{company} — {title}",
        "",
        f"**日期:** {today}",
        f"**一句话:** {output.summary}",
        "",
        _origin_note(output, experiences),
        "",
        "## 这场面试的重点",
        "",
        *[f"- {item}" for item in output.focus],
    ]

    number = 0
    for category, name in CATEGORY_NAMES.items():
        questions = [question for question in output.questions if question.category == category]
        if not questions:
            continue
        lines += ["", f"## {name}"]
        for question in questions:
            number += 1
            block = ["", f"### {number}. {question.question}", ""]
            block.append(f"{SOURCE_NAMES[question.source]}。{question.why}")
            if question.quote:
                block += ["", f"> {QUOTE_FROM[question.source]}原文：{question.quote}"]
            if question.web_id in found:
                origin = found[question.web_id]
                partial = "" if origin["opened"] else "（只读到搜索摘要）"
                block += ["", f"出处：[{origin['title']}]({origin['url']}){partial}"]
            block.append("")
            block += _bullets("回答要点", question.answer_points)
            evidence = [sources[item] for item in question.evidence_ids if item in sources]
            if evidence:
                block += [f"用到的材料：{'；'.join(dict.fromkeys(evidence))}", ""]
            block += _bullets("可能的追问", question.follow_ups)
            block += _bullets("要复习的", question.review_topics)
            if question.to_confirm.strip():
                block += [f"**先想清楚:** {question.to_confirm.strip()}", ""]
            lines += block[:-1]  # 去掉末尾的空行，题与题之间只隔一行

    lines += ["", "## 可以问面试官的", ""]
    lines += [f"- **{item.question}** {item.why}" for item in output.ask_them]
    lines += ["", "## 准备清单", "", "按重要程度排列。"]
    lines += [""] + [
        f"{index}. **{item.task}** {item.reason}" for index, item in enumerate(output.plan, start=1)
    ]
    if to_confirm:
        lines += ["", "## 需要你先补齐的事实", ""]
        lines += [f"- {question.to_confirm.strip()}" for question in to_confirm]
    return "\n".join(lines) + "\n"


HUMAN_TEMPLATE = """请为下面这个岗位准备面试。今天是 {today}。

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

<证据片段>
{evidence}
</证据片段>

<岗位评估>
{evaluation}
</岗位评估>

<公司研究>
{research}
</公司研究>

<网上的面经>
{experiences}
</网上的面经>

<候选人这次的要求>
{request}
</候选人这次的要求>"""


def build_messages(
    *,
    company: str,
    title: str,
    jd: str,
    resume: str,
    evidence_block: str,
    evaluation: str,
    research: str,
    request: str,
    today: str,
    experiences_block: str = "",
) -> tuple[SystemMessage, HumanMessage]:
    human = HUMAN_TEMPLATE.format(
        today=today,
        company=company,
        title=title,
        jd=jd.strip(),
        resume=resume.strip(),
        evidence=evidence_block.strip() or "（没有检索到可用的证据片段）",
        evaluation=trim_to_tokens(evaluation.strip(), REPORT_TOKENS) or "（这个岗位还没有评估）",
        research=trim_to_tokens(research.strip(), REPORT_TOKENS) or "（这个岗位还没有公司研究）",
        experiences=experiences_block.strip() or "（没有搜到这家公司的面经，不要出 web 题）",
        request=request.strip() or "（没有特别要求）",
    )
    return SystemMessage(SYSTEM_PROMPT), HumanMessage(human)
