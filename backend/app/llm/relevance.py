"""相关性筛选：搜索结果里哪些岗位和关键词明显无关。规则在 backend/prompts/relevance.md。

平台的搜索结果不都相关（例如在智联搜「前端开发」，采回来一个 Java 后端岗位）。
只把职位名交给模型，一页一次。判断不出来、输出读不懂时，一个都不筛。
"""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.config import BACKEND_DIR
from app.llm.parsing import InvalidOutput, parse_json_as

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "relevance.md").read_text(encoding="utf-8").strip()
MAX_SHARE = 0.8  # 模型说超过这个比例的岗位都无关时，多半是它理解错了，这一页不筛


class RelevanceOutput(BaseModel):
    irrelevant: list[int]


async def irrelevant_titles(model: BaseChatModel, query: str, titles: list[str]) -> set[int]:
    """返回明显无关的岗位在 titles 里的位置（从 0 开始）。出任何问题都返回空集合。"""
    if not titles:
        return set()
    numbered = "\n".join(f"{number}. {title}" for number, title in enumerate(titles, start=1))
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"搜索关键词：{query}\n\n职位名：\n{numbered}"),
    ]
    try:
        reply = await model.ainvoke(messages)
        output = parse_json_as(str(reply.text), RelevanceOutput)
    except InvalidOutput as error:
        logger.warning("相关性筛选的输出不合格，这一页不筛：%s", error)
        return set()
    except Exception as error:  # noqa: BLE001  筛选只是锦上添花，模型出错不能让采集停下
        logger.warning("相关性筛选没有完成，这一页不筛：%s", type(error).__name__)
        return set()
    picked = {number - 1 for number in output.irrelevant if 1 <= number <= len(titles)}
    usage = reply.usage_metadata or {}
    logger.info(
        "相关性筛选：%s 个职位名里挑出 %s 个无关的 input_tokens=%s output_tokens=%s",
        len(titles),
        len(picked),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
    )
    if len(picked) > len(titles) * MAX_SHARE:
        logger.warning("相关性筛选挑出的比例过高，这一页不筛")
        return set()
    return picked
