"""措辞核对：新写的内容里，有没有材料不支持的说法。

app/llm/facts.py 只能查数字和英文技术词。中文措辞的升级（把「参与」写成「独立承接」）程序查不出来，
所以再让模型逐条对照一次。规则在 backend/prompts/factcheck.md。

两点约定：
- 模型必须逐字指出是新内容里的哪几个字。程序核对这几个字确实在新内容里，对不上的不算。
- 发现的问题不退回重写，而是标在那一条上交给用户判断：核对本身也可能判断错。
"""

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.config import BACKEND_DIR
from app.llm.parsing import InvalidOutput, compact, parse_json_as

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "factcheck.md").read_text(encoding="utf-8").strip()
ATTEMPTS = 2


@dataclass(frozen=True)
class Item:
    basis: str  # 这条内容声称根据的原文
    text: str  # 新写的内容


class Finding(BaseModel):
    quote: str
    reason: str


class ItemFindings(BaseModel):
    id: int
    findings: list[Finding] = Field(default_factory=list)


class CheckOutput(BaseModel):
    items: list[ItemFindings]


HUMAN_TEMPLATE = """<材料>
{material}
</材料>

{items}"""

ITEM_TEMPLATE = """<条目 id="{number}">
<依据>
{basis}
</依据>
<新内容>
{text}
</新内容>
</条目>"""


def build_messages(material: str, items: list[Item]) -> list[BaseMessage]:
    blocks = [
        ITEM_TEMPLATE.format(
            number=number, basis=item.basis.strip() or "（没有）", text=item.text.strip()
        )
        for number, item in enumerate(items, start=1)
    ]
    human = HUMAN_TEMPLATE.format(material=material.strip(), items="\n\n".join(blocks))
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(human)]


def read_findings(raw: str, items: list[Item]) -> list[list[str]]:
    """把模型的输出变成每个条目的提醒。只保留程序核对得上的：

    条目编号存在；指出的那几个字确实在新内容里；而且不是依据里原样就有的话。
    """
    output = parse_json_as(raw, CheckOutput)
    cautions: list[list[str]] = [[] for _ in items]
    for result in output.items:
        if not 1 <= result.id <= len(items):
            continue
        item = items[result.id - 1]
        for finding in result.findings:
            quote = compact(finding.quote)
            if not quote or not finding.reason.strip():
                continue
            if quote not in compact(item.text) or quote in compact(item.basis):
                continue
            caution = f"「{finding.quote.strip()}」{finding.reason.strip()}"
            if caution not in cautions[result.id - 1]:
                cautions[result.id - 1].append(caution)
    return cautions


async def check_wording(
    model: BaseChatModel, *, material: str, items: list[Item], label: str
) -> list[list[str]]:
    """逐条核对，返回和 items 一一对应的提醒列表（没有问题的是空列表）。

    label 只用在日志里，写明是哪个岗位的哪种内容。
    """
    if not items:
        return []
    messages = build_messages(material, items)
    for attempt in range(1, ATTEMPTS + 1):
        reply = await model.ainvoke(messages)
        usage = reply.usage_metadata or {}
        try:
            cautions = read_findings(str(reply.text), items)
        except InvalidOutput as error:
            logger.warning("%s：措辞核对第 %s 次的输出不合格：%s", label, attempt, error)
            continue
        logger.info(
            "%s：措辞核对 %s 条，有提醒的 %s 条 input_tokens=%s output_tokens=%s",
            label,
            len(items),
            sum(1 for found in cautions if found),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        return cautions
    raise RuntimeError("措辞核对没有完成：模型的输出不合格。可以恢复这次运行重试")
