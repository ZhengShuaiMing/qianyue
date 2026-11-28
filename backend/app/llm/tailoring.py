"""简历定制：修改方案的结构、程序校验、应用修改。

分工：定制规则写在 backend/prompts/tailoring.md，模型只提出「按行号定位的修改」；
这里负责三件事：检查方案是否自洽、检查新内容有没有材料之外的数字和技术词、
把用户接受的修改应用到基础简历上。最终简历的每一行，要么是原文，要么来自被接受的修改。
"""

import hashlib
import re
from collections.abc import Callable
from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, StringConstraints

from app.core.config import BACKEND_DIR
from app.db.models import Job
from app.llm.evidence import has_placeholder
from app.llm.facts import unsupported
from app.llm.parsing import InvalidOutput, parse_json_as

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "tailoring.md").read_text(encoding="utf-8").strip()
# 规则文件的指纹，参与判断「输入有没有变」。规则改了，旧的定制简历就不再被直接复用。
RULES_FINGERPRINT = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]

MAX_EDITS = 12
MAX_QUERIES = 20

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ---------- 模型输出的结构 ----------


class Edit(BaseModel):
    kind: Literal["replace", "delete", "insert", "move"]
    start: int | None = None  # replace / delete / move：起始行
    end: int | None = None  # 结束行，包含在内
    after: int | None = None  # insert / move：放到第几行后面，0 表示最前面
    new_text: str = ""
    fact_check: Literal["same_facts", "from_evidence", "needs_confirmation"] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    jd_basis: Text
    reason: Text

    @property
    def lines(self) -> range:
        """这条修改覆盖的原简历行号。insert 不覆盖任何行。"""
        return range(self.start, self.end + 1) if self.kind != "insert" else range(0)


class TailoringPlan(BaseModel):
    summary: Text
    edits: list[Edit]
    gaps: list[str]
    questions: list[str]


def parse_plan(text: str) -> TailoringPlan:
    """Output Parser：把模型返回的文本变成 TailoringPlan，允许外面包一层 ```json。"""
    return parse_json_as(text, TailoringPlan)


# ---------- 程序校验 ----------


def _shape_problems(edit: Edit, total: int) -> list[str]:
    """单条修改自身的问题：字段齐不齐、行号在不在范围里。"""
    needs_range = edit.kind != "insert"
    needs_after = edit.kind in ("insert", "move")
    needs_text = edit.kind in ("replace", "insert")
    problems = []

    if needs_range:
        if edit.start is None or edit.end is None:
            return [f"{edit.kind} 必须给出 start 和 end"]
        if not 1 <= edit.start <= edit.end <= total:
            return [f"行号 {edit.start}–{edit.end} 不在简历的 1–{total} 行之内"]
    if needs_after:
        if edit.after is None:
            return [f"{edit.kind} 必须给出 after"]
        if not 0 <= edit.after <= total:
            return [f"after={edit.after} 不在 0–{total} 之内"]
    if edit.kind == "move" and edit.start - 1 <= edit.after <= edit.end:
        problems.append("after 落在被移动的范围里或紧挨着它，等于没有移动")

    if needs_text and not edit.new_text.strip():
        problems.append(f"{edit.kind} 必须给出 new_text")
    if not needs_text and edit.new_text.strip():
        problems.append(f"{edit.kind} 不能带 new_text")
    if needs_text and edit.fact_check is None:
        problems.append(f"{edit.kind} 必须给出 fact_check")
    return problems


def unverified_facts(edit: Edit, resume_md: str, evidence: dict[str, str]) -> list[str]:
    """新内容里，在简历和所引用证据中都找不到的数字和英文技术词。"""
    if edit.kind not in ("replace", "insert"):
        return []
    allowed = (
        resume_md
        + "\n"
        + "\n".join(
            evidence[evidence_id] for evidence_id in edit.evidence_ids if evidence_id in evidence
        )
    )
    return unsupported(edit.new_text, allowed)


def _fact_problems(edit: Edit, resume_md: str, evidence: dict[str, str]) -> list[str]:
    """新内容有没有超出材料：引用的证据是否存在，数字和技术词是否都有出处。"""
    problems = []
    unknown = [evidence_id for evidence_id in edit.evidence_ids if evidence_id not in evidence]
    if unknown:
        problems.append(f"引用了不存在的证据 {'、'.join(unknown)}")
    if edit.fact_check == "from_evidence" and not edit.evidence_ids:
        problems.append("fact_check 为 from_evidence 时必须在 evidence_ids 里列出证据编号")

    # 标为需要确认的修改不退回：超出材料的内容会标出来，由用户决定。
    unverified = unverified_facts(edit, resume_md, evidence)
    if unverified and edit.fact_check != "needs_confirmation":
        problems.append(
            f"新内容里出现了简历和所引用证据中没有的数字或技术词：{'、'.join(unverified)}。"
            "只能使用材料里已有的；确实需要的话，把这条的 fact_check 改为 needs_confirmation "
            "交给候选人确认"
        )
    return problems


def drop_unchanged(plan: TailoringPlan, resume_md: str) -> TailoringPlan:
    """去掉「替换成一模一样的内容」这种没有实际改动的修改。"""
    lines = resume_md.splitlines()

    def changes_something(edit: Edit) -> bool:
        if edit.kind != "replace" or edit.start is None or edit.end is None:
            return True
        if not 1 <= edit.start <= edit.end <= len(lines):
            return True  # 行号不对，留给 check_plan 报告
        return edit.new_text.strip() != "\n".join(lines[edit.start - 1 : edit.end]).strip()

    return plan.model_copy(
        update={"edits": [edit for edit in plan.edits if changes_something(edit)]}
    )


def require_placeholder_confirmation(plan: TailoringPlan) -> TailoringPlan:
    edits = [
        edit.model_copy(update={"fact_check": "needs_confirmation"})
        if edit.kind in ("replace", "insert") and has_placeholder(edit.new_text)
        else edit
        for edit in plan.edits
    ]
    return plan.model_copy(update={"edits": edits})


def check_plan(
    plan: TailoringPlan,
    resume_md: str,
    evidence: dict[str, str],
    *,
    mask: Callable[[str], str] = str,
) -> None:
    """检查修改方案。有问题就抛出 InvalidOutput，信息里逐条写明哪条修改有什么问题。

    evidence：证据编号 → 证据原文。
    """
    total = len(resume_md.splitlines())
    visible_resume = mask(resume_md)
    visible_evidence = {key: mask(text) for key, text in evidence.items()}
    problems = []
    if len(plan.edits) > MAX_EDITS:
        problems.append(f"修改有 {len(plan.edits)} 条，最多 {MAX_EDITS} 条")

    valid: list[tuple[int, Edit]] = []
    for number, edit in enumerate(plan.edits, start=1):
        found = _shape_problems(edit, total)
        if not found:
            valid.append((number, edit))
            found = _fact_problems(edit, visible_resume, visible_evidence)
        problems += [f"第 {number} 条修改：{problem}" for problem in found]

    # 修改之间不能打架。只比较自身没问题的修改。
    for index, (number, edit) in enumerate(valid):
        for other_number, other in valid[index + 1 :]:
            shared = set(edit.lines) & set(other.lines)
            if not shared:
                continue
            kinds = {edit.kind, other.kind}
            if "move" not in kinds or kinds == {"move"}:
                problems.append(f"第 {number} 条和第 {other_number} 条修改的行范围重叠")
                continue
            # 一条是移动，另一条是改写或删除：改写的范围必须整个落在被移动的范围里。
            inner = other if edit.kind == "move" else edit
            if len(shared) != len(inner.lines):
                problems.append(
                    f"第 {number} 条和第 {other_number} 条修改的行范围只有一部分重叠。"
                    "改写或删除的范围要么完全在被移动的范围内，要么完全在外"
                )

    if problems:
        raise InvalidOutput("；".join(problems))


# ---------- 应用修改 ----------


def number_lines(resume_md: str) -> str:
    """给简历每行加上行号，模型按行号指出要改哪里。"""
    return "\n".join(f"{number}| {line}" for number, line in enumerate(resume_md.splitlines(), 1))


def original_text(resume_md: str, edit: Edit) -> str:
    lines = resume_md.splitlines()
    return "\n".join(lines[number - 1] for number in edit.lines)


def apply_edits(resume_md: str, edits: list[Edit]) -> str:
    """把修改应用到简历上，返回新的 Markdown。edits 必须来自通过了 check_plan 的方案。

    所有行号都指原简历。做法是先决定每一行变成什么，再决定这些行的先后顺序。
    """
    lines = resume_md.splitlines()
    content: dict[int, list[str]] = {number: [line] for number, line in enumerate(lines, 1)}
    inserted: dict[int, list[str]] = {}

    for edit in edits:
        if edit.kind == "replace":
            for number in edit.lines:
                content[number] = []
            content[edit.start] = edit.new_text.strip("\n").splitlines()
        elif edit.kind == "delete":
            for number in edit.lines:
                content[number] = []
        elif edit.kind == "insert":
            inserted.setdefault(edit.after, []).extend(edit.new_text.strip("\n").splitlines())

    order = list(content)
    for edit in edits:
        if edit.kind == "move":
            block = list(edit.lines)
            order = [number for number in order if number not in edit.lines]
            position = order.index(edit.after) + 1 if edit.after else 0
            order[position:position] = block

    result = list(inserted.get(0, []))
    for number in order:
        result += content[number]
        result += inserted.get(number, [])

    text = "\n".join(result).strip("\n") + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)  # 删除和移动可能留下连续的空行


# ---------- Prompt ----------


def jd_queries(jd_text: str) -> list[str]:
    """把 JD 拆成一条条要求，每条作为一个检索查询。"""
    queries = []
    for piece in re.split(r"[\n；;。]", jd_text):
        piece = re.sub(r"^\s*(?:\d+[.、)）]|[-*•·])\s*", "", piece).strip()
        if 8 <= len(piece) <= 120 and piece not in queries:
            queries.append(piece)
    return queries[:MAX_QUERIES]


HUMAN_TEMPLATE = """请为下面这个岗位定制简历。

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

<岗位评估里的定制计划>
{evaluation_plan}
</岗位评估里的定制计划>"""


def build_messages(
    job: Job, *, resume_md: str, evidence_block: str, evaluation_plan: str
) -> tuple[SystemMessage, HumanMessage]:
    human = HUMAN_TEMPLATE.format(
        company=job.company.strip() or "未填写",
        title=job.title.strip() or "未填写",
        jd=job.jd_text.strip(),
        resume=number_lines(resume_md),
        evidence=evidence_block.strip() or "（没有检索到可用的证据片段）",
        evaluation_plan=evaluation_plan.strip() or "（这个岗位还没有评估）",
    )
    return SystemMessage(SYSTEM_PROMPT), HumanMessage(human)
