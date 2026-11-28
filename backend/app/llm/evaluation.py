"""岗位评估：模型输出的结构、程序算分、报告正文。

分工：评估规则写在 backend/prompts/evaluation.md，模型只负责各维度得分和文字；
总分、信息覆盖率、置信度和投递结论在这里按固定规则计算，模型改不了。
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, StringConstraints, create_model

from app.core.config import BACKEND_DIR
from app.db.models import Job
from app.llm.evidence import QuoteSource, local_quote, plain_quote, quote_sources
from app.llm.parsing import InvalidOutput, parse_json_as

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "evaluation.md").read_text(encoding="utf-8").strip()
# 规则文件的指纹。它参与判断「输入有没有变」：规则改了，旧报告就不再被直接复用。
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Dimension:
    key: str
    name: str
    weight: int  # 百分比


DIMENSIONS = (
    Dimension("role_fit", "目标岗位契合度", 25),
    Dimension("resume_match", "简历证据匹配度", 20),
    Dimension("level_scope", "职级与职责范围", 15),
    Dimension("compensation", "人民币薪资与完整薪酬", 15),
    Dimension("growth", "成长空间", 10),
    Dimension("company_stability", "公司与业务稳定性", 5),
    Dimension("tech_value", "技术与业务价值", 5),
    Dimension("work_mode", "通勤、出差和工作方式", 5),
)
# 这两个维度不可评估，说明 JD 或简历证据本身不完整，置信度直接判低。
CORE_DIMENSIONS = ("role_fit", "resume_match")
# 这些维度不可评估时，用户补一点信息就能评。报告里告诉他补什么。
HOW_TO_SCORE = {
    "role_fit": "在补充资料里写上你的求职方向。",
    "compensation": "在补充资料或本次条件里写上期望月薪；岗位没写薪资的，问到后补进岗位信息。",
    "work_mode": "给岗位补上工作城市，并在补充资料里写上你的期望城市、能否接受出差或驻场。",
}


# ---------- 模型输出的结构 ----------

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DimensionScore(BaseModel):
    score: int | None = Field(ge=1, le=5, description="1–5 的整数；不可评估时为 null")
    evidence: Text


# 八个维度各是一个必填字段，少一个或多一个都过不了校验。
Dimensions = create_model(
    "Dimensions", **{dimension.key: (DimensionScore, ...) for dimension in DIMENSIONS}
)


class Blocker(BaseModel):
    issue: Text
    status: Literal["confirmed", "pending"]
    evidence: Text
    # JD 或候选人材料里说明这个门槛的原句，逐字摘录。程序会去原文里核对。
    quote: str = ""


class EvaluationOutput(BaseModel):
    """字段保持扁平：长文本放进嵌套对象时，模型容易漏掉或提前写收尾的括号。"""

    company: str = ""
    title: str = ""
    direction: Text
    summary: Text
    keywords: list[str]
    a_overview: Text
    b_resume_match: Text
    c_level: Text
    d_compensation: Text
    e_plan: Text
    dimensions: Dimensions
    blockers: list[Blocker]
    key_unknowns: list[str]


def parse_output(text: str) -> EvaluationOutput:
    """Output Parser：把模型返回的文本变成 EvaluationOutput，允许外面包一层 ```json。"""
    return parse_json_as(text, EvaluationOutput)


MIN_QUOTE_CHARS = 4


def check_blocker_quotes(
    output: EvaluationOutput, sources: str | list[QuoteSource]
) -> EvaluationOutput:
    """「已确认」的硬性阻断会直接把结论定为不建议投，所以它摘录的原句必须真在 JD 或材料里。

    查不到（或没摘录）的改成「待核实」，并在依据里写明，免得一个模型自己编出来的门槛否掉岗位。
    「待核实」的原句查不到时也不当原句显示，在依据里注明。
    原句可以用「……」省略中间部分，每一段都要找得到。
    """
    if isinstance(sources, str):
        sources = quote_sources({"材料": sources}, str)
    texts = [(source, plain_quote(source.visible)) for source in sources]
    blockers = []
    for blocker in output.blockers:
        pieces = [plain_quote(piece) for piece in re.split(r"…+|\.{3,}", blocker.quote)]
        pieces = [piece for piece in pieces if piece]
        matches = [source for source, text in texts if all(p in text for p in pieces)]
        if sum(map(len, pieces)) >= MIN_QUOTE_CHARS and matches:
            original = local_quote(blocker.quote, matches, normalize=plain_quote, pieces=pieces)
            blocker = blocker.model_copy(update={"quote": original})
        elif blocker.status == "confirmed":
            note = (
                f"（摘录的原句「{blocker.quote}」在 JD 和材料里没有找到，按待核实处理）"
                if blocker.quote.strip()
                else "（没有摘录原句，无法核对，按待核实处理）"
            )
            blocker = blocker.model_copy(
                update={"status": "pending", "evidence": blocker.evidence + note, "quote": ""}
            )
        elif blocker.quote.strip():
            note = f"（摘录的「{blocker.quote}」在 JD 和材料里没有找到）"
            blocker = blocker.model_copy(update={"evidence": blocker.evidence + note, "quote": ""})
        blockers.append(blocker)
    return output.model_copy(update={"blockers": blockers})


# ---------- 程序算分 ----------


class Verdict(StrEnum):
    priority = "priority"
    worth = "worth"
    conditional = "conditional"
    not_recommended = "not_recommended"


VERDICT_LABELS = {
    Verdict.priority: "强匹配，建议优先投递",
    Verdict.worth: "匹配良好，值得投递",
    Verdict.conditional: "有条件匹配，确认关键风险后再投",
    Verdict.not_recommended: "不建议投入主要精力",
}


class Confidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


CONFIDENCE_LABELS = {Confidence.high: "高", Confidence.medium: "中", Confidence.low: "低"}


class Scorecard(BaseModel):
    total: float  # 1–5，一位小数
    coverage: int  # 信息覆盖率，百分比
    confidence: Confidence
    verdict: Verdict
    confirmed_blockers: int
    pending_blockers: int


VERDICT_BOUNDARIES = (Decimal("3.5"), Decimal("4.0"), Decimal("4.5"))  # 结论换档的分数线
RECHECKS = 2  # 总分在分数线附近时，再评这么多次


def near_boundary(total: float) -> bool:
    """总分正好在分数线上，或者只差 0.1 没到：同一份输入再评一次，结论就可能换一档。

    总分只有一位小数，换档发生在「线下 0.1」和「线上」这两个分数之间，所以只看这两个。
    高于分数线 0.1 时不触发，避免扩大复评范围和模型开销。
    """
    value = Decimal(str(total))
    return any(
        Decimal("0") <= boundary - value <= Decimal("0.1") for boundary in VERDICT_BOUNDARIES
    )


def merge_by_median(outputs: list[EvaluationOutput]) -> EvaluationOutput:
    """几次评估合成一份：每个维度取这几次得分的中间值，其余内容用第一次的。

    某个维度的得分和依据要对得上，所以整条照搬得分等于中间值的那一次。
    多数认为不可评估的维度按不可评估算；得分个数是偶数时取偏低的那个。
    """
    first = outputs[0]
    merged = {}
    for dimension in DIMENSIONS:
        candidates = [getattr(output.dimensions, dimension.key) for output in outputs]
        scores = sorted(item.score for item in candidates if item.score is not None)
        if len(scores) * 2 <= len(candidates):
            middle = None
        else:
            middle = scores[(len(scores) - 1) // 2]
        merged[dimension.key] = next(item for item in candidates if item.score == middle)
    return first.model_copy(update={"dimensions": Dimensions(**merged)})


def compute_scorecard(output: EvaluationOutput) -> Scorecard:
    scores = {
        dimension: getattr(output.dimensions, dimension.key).score for dimension in DIMENSIONS
    }
    scored = {dimension: score for dimension, score in scores.items() if score is not None}

    # 覆盖率 = 可评估维度的权重之和；总分只在这些维度里做加权平均。
    coverage = sum(dimension.weight for dimension in scored)
    if coverage == 0:
        raise InvalidOutput("没有任何可评估的维度，无法评分")
    weighted = sum(dimension.weight * score for dimension, score in scored.items())
    total = (Decimal(weighted) / coverage).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    if total >= Decimal("4.5"):
        verdict = Verdict.priority
    elif total >= Decimal("4.0"):
        verdict = Verdict.worth
    elif total >= Decimal("3.5"):
        verdict = Verdict.conditional
    else:
        verdict = Verdict.not_recommended
    # 硬性阻断不能被其他维度的高分抵消，分数照算，结论单独处理：
    # 已确认不满足门槛，就是不建议投；还没核实清楚的，最多到「确认关键风险后再投」。
    confirmed = sum(blocker.status == "confirmed" for blocker in output.blockers)
    pending = sum(blocker.status == "pending" for blocker in output.blockers)
    if confirmed:
        verdict = Verdict.not_recommended
    elif pending and verdict in (Verdict.priority, Verdict.worth):
        verdict = Verdict.conditional

    # 置信度只看证据够不够：覆盖率、核心维度能不能评、有没有可能推翻结论的待核实阻断。
    core_missing = any(dimension.key in CORE_DIMENSIONS for dimension in scores.keys() - scored)
    if coverage < 60 or core_missing:
        confidence = Confidence.low
    elif coverage >= 85 and not pending:
        confidence = Confidence.high
    else:
        confidence = Confidence.medium

    return Scorecard(
        total=float(total),
        coverage=coverage,
        confidence=confidence,
        verdict=verdict,
        confirmed_blockers=confirmed,
        pending_blockers=pending,
    )


# ---------- Prompt 与报告 ----------

# 不变的材料在前、每个岗位不同的内容在后：批量评估时每次请求的开头都一样，
# 模型服务商（例如 DeepSeek）能复用这段开头的缓存，输入更便宜。日期也放到最后。
HUMAN_TEMPLATE = """下面先是候选人的材料，然后是要评估的岗位。

<基础简历>
{resume}
</基础简历>

<补充资料>
{supplement}
</补充资料>

<STAR 经历>
{star}
</STAR 经历>

请评估下面这个岗位。

<岗位信息>
公司：{company}
职位：{title}
城市：{city}
薪资：{salary}
公司规模：{company_size}
行业：{industry}
平台显示的发布日期：{posted_at}
链接：{url}
</岗位信息>

<JD>
{jd}
</JD>

<本次条件>
{conditions}
</本次条件>

今天是 {today}。"""


def build_messages(
    job: Job, *, resume: str, supplement: str, star: str, conditions: str
) -> tuple[SystemMessage, HumanMessage]:
    def or_missing(value: str | None, missing: str = "（未提供）") -> str:
        return (value or "").strip() or missing

    human = HUMAN_TEMPLATE.format(
        today=date.today().isoformat(),
        company=or_missing(job.company, "未填写"),
        title=or_missing(job.title, "未填写"),
        city=or_missing(job.city, "未填写"),
        salary=or_missing(job.salary, "未填写"),
        company_size=or_missing(job.company_size, "未知"),
        industry=or_missing(job.industry, "未知"),
        posted_at=or_missing(job.posted_at, "未知"),
        url=or_missing(job.url, "未填写"),
        jd=job.jd_text.strip(),
        resume=resume.strip(),
        supplement=or_missing(supplement),
        star=or_missing(star),
        conditions=or_missing(conditions),
    )
    return SystemMessage(SYSTEM_PROMPT), HumanMessage(human)


def report_title(job: Job, output: EvaluationOutput) -> str:
    company = job.company.strip() or output.company.strip() or "未知公司"
    title = job.title.strip() or output.title.strip() or "未知职位"
    return f"岗位评估：{company} — {title}"


def _blocker_summary(scorecard: Scorecard) -> str:
    parts = []
    if scorecard.confirmed_blockers:
        parts.append(f"已确认 {scorecard.confirmed_blockers} 项")
    if scorecard.pending_blockers:
        parts.append(f"待核实 {scorecard.pending_blockers} 项")
    return "，".join(parts) or "无"


def _cell(text: str) -> str:
    """表格单元格里不能有换行和竖线。"""
    return text.replace("\n", " ").replace("|", "\\|")


def render_report(
    job: Job,
    output: EvaluationOutput,
    scorecard: Scorecard,
    *,
    today: str,
    recheck_totals: list[float] | None = None,
) -> str:
    """把结构化结果排成给人看的 Markdown 报告。数字全部来自 scorecard。

    recheck_totals：总分在分数线附近、多评了几次时，每一次各自的总分。
    """
    lines = [
        f"# {report_title(job, output)}",
        "",
        f"**日期:** {today}",
        f"**URL:** {job.url or '未提供'}",
        f"**岗位方向:** {output.direction}",
        f"**结论:** {VERDICT_LABELS[scorecard.verdict]}",
        f"**评分:** {scorecard.total}/5",
        f"**信息覆盖率:** {scorecard.coverage}%",
        f"**评分置信度:** {CONFIDENCE_LABELS[scorecard.confidence]}",
        f"**硬性阻断:** {_blocker_summary(scorecard)}",
    ]
    if recheck_totals:
        each = "、".join(str(total) for total in recheck_totals)
        lines.append(
            f"**复评:** 第一次的总分在结论的分数线附近，所以一共评了 {len(recheck_totals)} 次，"
            f"每个维度取中间值（各次总分：{each}）"
        )
    lines += ["", f"> {output.summary}"]

    for heading, body in (
        ("A）岗位概览", output.a_overview),
        ("B）简历匹配", output.b_resume_match),
        ("C）级别与定位", output.c_level),
        ("D）薪资与市场", output.d_compensation),
        ("E）定制计划", output.e_plan),
    ):
        lines += ["", f"## {heading}", "", body]

    lines += ["", "## 评分明细", "", "| 维度 | 权重 | 得分 | 依据 |", "| --- | ---: | ---: | --- |"]
    for dimension in DIMENSIONS:
        item = getattr(output.dimensions, dimension.key)
        score = "不可评估" if item.score is None else item.score
        lines.append(
            f"| {dimension.name} | {dimension.weight}% | {score} | {_cell(item.evidence)} |"
        )

    missing = [
        dimension
        for dimension in DIMENSIONS
        if dimension.key in HOW_TO_SCORE and getattr(output.dimensions, dimension.key).score is None
    ]
    if missing:
        lines += ["", "## 怎样让这份评估更准", "", "下面几项这次没有计分，补上信息后重新评估："]
        lines += [
            f"- **{dimension.name}**（权重 {dimension.weight}%）：{HOW_TO_SCORE[dimension.key]}"
            for dimension in missing
        ]

    if output.blockers:
        lines += ["", "## 硬性阻断", ""]
        for blocker in output.blockers:
            status = "已确认" if blocker.status == "confirmed" else "待核实"
            quote = f"（原句：「{blocker.quote.strip()}」）" if blocker.quote.strip() else ""
            lines.append(f"- **{status}**：{blocker.issue}。{blocker.evidence}{quote}")
    if output.key_unknowns:
        lines += ["", "## 待确认", ""]
        lines += [f"- {item}" for item in output.key_unknowns]
    if output.keywords:
        lines += ["", "## 关键词", "", "、".join(output.keywords)]

    return "\n".join(lines) + "\n"
