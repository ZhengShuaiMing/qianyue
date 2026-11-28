"""公司研究：结论的结构、程序核对、报告正文。

分工：研究规则写在 backend/prompts/research.md，Agent 负责搜索、阅读和写结论；
这里核对它引用的来源是不是工具真的返回过的，并根据「哪些网页真的打开读过」
调整置信度和总体判断。模型说了不算的部分，由这里说了算。
"""

import hashlib
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.core.config import BACKEND_DIR
from app.llm.parsing import InvalidOutput, parse_json_as

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "research.md").read_text(encoding="utf-8").strip()
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Confidence = Literal["high", "medium", "low"]
RiskLevel = Literal["low", "medium", "high", "insufficient"]

TOPIC_NAMES = {
    "business": "业务与近况",
    "team": "团队与技术",
    "employment": "主体与用工",
    "operation": "经营与履约",
    "hiring": "招聘一致性",
}
# 只有这两个方面的问题（合同主体、欠薪、经营异常）才能判为「有风险」。
MAJOR_RISK_TOPICS = ("employment", "operation")
SIGNAL_NAMES = {"risk": "风险信号", "positive": "正向信号", "neutral": "中性事实"}
CONFIDENCE_NAMES = {"high": "高", "medium": "中", "low": "低"}
RISK_NAMES = {
    "low": "没有发现需要担心的信号",
    "medium": "有需要在沟通或面试中确认的重要信号",
    "high": "有可靠来源支持的风险",
    "insufficient": "信息不足，无法判断",
}


class Finding(BaseModel):
    topic: Literal["business", "team", "employment", "operation", "hiring"]
    signal: Literal["positive", "risk", "neutral"]
    claim: Text
    source_ids: list[str] = Field(min_length=1)
    confidence: Confidence


class ResearchOutput(BaseModel):
    company_name: Text
    identity_confirmed: bool
    identity_note: Text
    overview: Text
    findings: list[Finding]
    unknowns: list[str]
    questions: list[str]
    risk_level: RiskLevel
    summary: Text


def parse_research(text: str) -> ResearchOutput:
    """Output Parser：把 Agent 最后的回答变成 ResearchOutput，允许外面包一层 ```json。"""
    return parse_json_as(text, ResearchOutput)


def check_research(output: ResearchOutput, sources: list[dict]) -> None:
    """核对来源：结论里引用的每个编号，都必须是工具真的返回过的。"""
    known = {source["id"] for source in sources}
    cited = set(re.findall(r"\[(S\d+)\]", output.overview))
    for finding in output.findings:
        cited.update(finding.source_ids)
    # 编号按 S1、S2…S10 的顺序列出。模型可能写出 "Source1" 这类不合格的编号，不能按数字解析
    order = lambda source_id: (len(source_id), source_id)  # noqa: E731
    unknown = sorted(cited - known, key=order)
    if unknown:
        available = "、".join(sorted(known, key=order)) or "没有任何来源"
        raise InvalidOutput(
            f"引用了工具没有返回过的来源 {'、'.join(unknown)}。可用的来源只有：{available}。"
            "只能引用这些编号；没有来源支持的说法请删掉或移到 unknowns"
        )


class CheckedFinding(Finding):
    verified: bool  # 支持它的来源里，有没有真的打开读过原文的
    fully_verified: bool = False  # 支持它的来源是不是全都读过原文

    @property
    def reading(self) -> str:
        if self.fully_verified:
            return "已读原文"
        return "部分来源已读原文，其余只有搜索摘要" if self.verified else "只有搜索摘要"


class ResearchResult(BaseModel):
    """程序核对之后的结论。"""

    risk_level: RiskLevel
    adjustments: list[str]  # 程序改了什么、为什么
    findings: list[CheckedFinding]


def finalize(output: ResearchOutput, sources: list[dict]) -> ResearchResult:
    """根据实际读过哪些原文，调整置信度和总体判断。"""
    opened = {source["id"] for source in sources if source["opened"]}
    adjustments = []

    findings = []
    lowered = 0
    for finding in output.findings:
        verified = bool(opened & set(finding.source_ids))
        confidence = finding.confidence
        if confidence == "high" and not verified:
            confidence = "medium"  # 没读过原文，只看了搜索摘要，不能算高置信度
            lowered += 1
        findings.append(
            CheckedFinding(
                **finding.model_dump(exclude={"confidence"}),
                confidence=confidence,
                verified=verified,
                fully_verified=set(finding.source_ids) <= opened,
            )
        )
    if lowered:
        adjustments.append(f"{lowered} 条发现只有搜索摘要、没有读过原文，置信度从高降为中。")

    risk_level = output.risk_level
    if not output.identity_confirmed and risk_level != "insufficient":
        risk_level = "insufficient"
        adjustments.append("公司主体没有确认，总体判断改为「信息不足」。")
    elif not findings and risk_level != "insufficient":
        risk_level = "insufficient"
        adjustments.append("没有任何有来源的发现，总体判断改为「信息不足」。")
    elif risk_level == "high" and not any(
        finding.signal == "risk"
        and finding.topic in MAJOR_RISK_TOPICS
        and finding.confidence == "high"  # 上面已经保证：高置信度一定读过原文
        for finding in findings
    ):
        risk_level = "medium"
        adjustments.append(
            "「有风险」需要一条读过原文、高置信度的用工或经营方面的风险发现支持，目前没有，"
            "总体判断从「有风险」降为「需要确认」。"
        )
    return ResearchResult(risk_level=risk_level, adjustments=adjustments, findings=findings)


def render_research_report(
    output: ResearchOutput, result: ResearchResult, sources: list[dict], *, today: str
) -> str:
    """把研究结论排成给人看的 Markdown 报告。"""
    identity = "已确认" if output.identity_confirmed else "未确认"
    lines = [
        f"# 公司研究：{output.company_name}",
        "",
        f"**日期:** {today}",
        f"**总体判断:** {RISK_NAMES[result.risk_level]}",
        f"**公司主体:** {identity}。{output.identity_note}",
        "",
        f"> {output.summary}",
        "",
        "## 公司概况",
        "",
        output.overview,
    ]

    for signal in ("risk", "positive", "neutral"):
        group = [finding for finding in result.findings if finding.signal == signal]
        if not group:
            continue
        lines += ["", f"## {SIGNAL_NAMES[signal]}", ""]
        for finding in group:
            cited = " ".join(f"[{source_id}]" for source_id in finding.source_ids)
            lines.append(
                f"- **{TOPIC_NAMES[finding.topic]}**：{finding.claim} {cited}"
                f"（置信度{CONFIDENCE_NAMES[finding.confidence]}，{finding.reading}）"
            )

    if output.unknowns:
        lines += ["", "## 没查到的", ""] + [f"- {item}" for item in output.unknowns]
    if output.questions:
        lines += ["", "## 值得问对方的问题", ""] + [f"- {item}" for item in output.questions]
    if result.adjustments:
        lines += ["", "## 程序核对后的调整", ""] + [f"- {item}" for item in result.adjustments]

    cited = set(re.findall(r"\[(S\d+)\]", output.overview))
    for finding in result.findings:
        cited.update(finding.source_ids)
    used = [source for source in sources if source["id"] in cited]
    if used:
        lines += ["", "## 来源", ""]
        for source in used:
            reading = "已读原文" if source["opened"] else "只有搜索摘要"
            title = source["title"] or source["url"]
            lines.append(f"- [{source['id']}] [{title}]({source['url']})（{reading}）")

    return "\n".join(lines) + "\n"


USER_TEMPLATE = """请研究下面这个岗位所属的公司。今天是 {today}。

<岗位信息>
公司：{company}
职位：{title}
城市：{city}
薪资：{salary}
</岗位信息>

<候选人这次特别想了解的>
{focus}
</候选人这次特别想了解的>

<JD>
{jd}
</JD>"""


JD_CHARS = 1500  # 研究公司用不着整份 JD：看业务和团队对不对得上，开头这些就够了


def _clip(jd: str) -> str:
    return jd if len(jd) <= JD_CHARS else jd[:JD_CHARS] + "\n（JD 较长，后面的部分已省略）"


def build_user_message(
    *, company: str, title: str, city: str, salary: str, jd: str, focus: str, today: str
) -> str:
    return USER_TEMPLATE.format(
        today=today,
        company=company,
        title=title or "未填写",
        city=city or "未填写",
        salary=salary or "未填写",
        focus=focus.strip() or "（没有特别要求）",
        jd=_clip(jd.strip()) or "（这个岗位还没有 JD）",
    )
