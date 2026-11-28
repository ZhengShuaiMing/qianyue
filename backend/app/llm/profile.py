"""能力画像：几个岗位的 JD 汇总成一份「这类岗位普遍要什么、你现在什么情况」。

模型只负责归纳和对照；「X/Y 个岗位要求」由程序按模型列出的岗位编号来数，编号必须是这次给的
岗位。候选人「有」「部分有」的能力要摘录材料原句，程序逐字核对，对不上的标出来。
"""

import hashlib
import re
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, StringConstraints

from app.core.config import BACKEND_DIR
from app.llm.evidence import QuoteSource, local_quote, plain_quote, quote_sources
from app.llm.parsing import InvalidOutput, parse_json_as

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "profile.md").read_text(encoding="utf-8")
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]
JD_CHARS = 1800  # 每个 JD 最多给模型多少字；能力要求一般在前面
MAX_JOBS = 15
MIN_JOBS = 2

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
KIND_NAMES = {"skill": "技术和工具", "experience": "经验", "ability": "能力"}
CANDIDATE_NAMES = {
    "has": "有",
    "partial": "部分有",
    "missing": "没有",
    "unverified": "没有核对上",
}


class Ability(BaseModel):
    name: Text
    kind: Literal["skill", "experience", "ability"]
    jobs: list[int]
    requirement: Text
    candidate: Literal["has", "partial", "missing", "unverified"]
    evidence: str = ""


class ProfileOutput(BaseModel):
    abilities: list[Ability]
    summary: Text
    focus: list[str]


def parse_profile(text: str) -> ProfileOutput:
    return parse_json_as(text, ProfileOutput)


def check_profile(
    output: ProfileOutput, job_ids: list[int], materials: str | list[QuoteSource]
) -> ProfileOutput:
    """程序核对：岗位编号只留这次给的；没有岗位的能力去掉；材料原句对不上的标出来。

    按要求的岗位数从多到少排。一项都不剩时算不合格，让模型重写。
    """
    allowed = set(job_ids)
    if isinstance(materials, str):
        materials = quote_sources({"材料": materials}, str)
    texts = [(source, plain_quote(source.visible)) for source in materials]
    kept = []
    for ability in output.abilities:
        jobs = sorted({job for job in ability.jobs if job in allowed})
        if not jobs:
            continue
        update: dict = {"jobs": jobs}
        if ability.candidate in ("has", "partial"):
            pieces = [plain_quote(piece) for piece in re.split(r"…+|\.{3,}", ability.evidence)]
            pieces = [piece for piece in pieces if piece]
            matches = [source for source, text in texts if all(p in text for p in pieces)]
            if sum(map(len, pieces)) < 4 or not matches:
                update["candidate"] = "unverified"
            else:
                update["evidence"] = local_quote(
                    ability.evidence, matches, normalize=plain_quote, pieces=pieces
                )
        elif ability.candidate == "missing":
            update["evidence"] = ""
        kept.append(ability.model_copy(update=update))
    if not kept:
        raise InvalidOutput("abilities 里没有一项用的是给出的岗位编号。jobs 只能写下面给出的编号")
    kept.sort(key=lambda a: (-len(a.jobs), a.name))
    return output.model_copy(update={"abilities": kept})


def build_messages(jobs: list[dict], materials: dict[str, str]) -> list:
    """jobs：{id, company, title, jd_text}；materials：简历、补充资料、STAR 经历的正文。"""
    parts = ["下面先是候选人的材料，然后是他挑的岗位。", ""]
    for name, body in materials.items():
        parts += [f"<{name}>", body.strip() or "（未提供）", f"</{name}>", ""]
    parts.append(f"他挑了 {len(jobs)} 个岗位，编号和 JD 如下（JD 太长时只给前 {JD_CHARS} 字）：")
    for job in jobs:
        name = f"{job['company'] or '未填公司'} — {job['title'] or '未填职位'}"
        parts += ["", f"<岗位 #{job['id']}：{name}>", job["jd_text"].strip()[:JD_CHARS], "</岗位>"]
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage("\n".join(parts))]


def render_profile(output: ProfileOutput, jobs: list[dict], *, today: str) -> str:
    total = len(jobs)
    lines = [
        f"# 能力画像：{total} 个岗位",
        "",
        f"**日期:** {today}",
        "**岗位:** "
        + "、".join(f"#{job['id']} {job['company'] or job['title'] or '未命名'}" for job in jobs),
        "",
        f"> {output.summary}",
    ]
    if output.focus:
        lines += ["", "## 最值得先补的", ""] + [f"- {item}" for item in output.focus]
    lines += [
        "",
        "## 这类岗位要什么",
        "",
        "「几个岗位要求」由程序按岗位编号数出来。「你的情况」里的依据是材料原句，程序核对过；"
        "「没有核对上」表示模型说你有，但摘录的原句在材料里找不到，请自己看一眼。",
        "",
        "| 能力 | 类别 | 几个岗位要求 | 一般怎么要求 | 你的情况 | 依据 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for ability in output.abilities:
        cells = [
            ability.name,
            KIND_NAMES[ability.kind],
            f"{len(ability.jobs)}/{total}（" + "、".join(f"#{j}" for j in ability.jobs) + "）",
            ability.requirement,
            CANDIDATE_NAMES[ability.candidate],
            f"「{ability.evidence.strip()}」" if ability.evidence.strip() else "",
        ]
        lines.append(
            "| " + " | ".join(c.replace("\n", " ").replace("|", "\\|") for c in cells) + " |"
        )
    return "\n".join(lines)
