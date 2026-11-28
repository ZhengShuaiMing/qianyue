"""ReAct Agent 的一次完整运行：模型决定调用哪个工具，看到结果后再决定下一步，直到给出结论。

结论不合格时，把原因告诉它，让它在原来的对话上接着改，最多再来 2 次。
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool

from app.graphs.common import MAX_RETRIES, RETRY_TEMPLATE
from app.llm.parsing import InvalidOutput

logger = logging.getLogger(__name__)

# 一轮「模型 → 工具」算两步。工具自己有次数上限，这里只是兜底，防止循环停不下来。
RECURSION_LIMIT = 60


class AgentGaveUp(Exception):
    """Agent 的结论始终不合格。"""


@dataclass
class AgentStats:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


T = TypeVar("T")


async def run_agent(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    user_message: str,
    parse: Callable[[str], T],
) -> tuple[T, AgentStats]:
    """运行 Agent，返回 (通过核对的结论, 用量统计)。

    parse 负责解析并核对 Agent 的最终回答，不合格时抛 InvalidOutput。
    """
    # checkpointer=False：这个 Agent 在外层图的一个节点里跑，不单独存断点。节点重跑时它从头开始。
    agent = create_agent(model, tools, system_prompt=system_prompt, checkpointer=False)
    messages = [HumanMessage(user_message)]
    stats = AgentStats()
    for attempt in range(1, MAX_RETRIES + 2):
        seen = len(messages)
        state = await agent.ainvoke({"messages": messages}, {"recursion_limit": RECURSION_LIMIT})
        messages = state["messages"]
        for message in messages[seen:]:
            if isinstance(message, AIMessage):
                usage = message.usage_metadata or {}
                stats.model_calls += 1
                stats.tool_calls += len(message.tool_calls)
                stats.input_tokens += usage.get("input_tokens", 0)
                stats.output_tokens += usage.get("output_tokens", 0)
        try:
            return parse(str(messages[-1].text)), stats
        except InvalidOutput as error:
            logger.warning("Agent 的结论不合格（第 %s 次）：%s", attempt, error)
            if attempt > MAX_RETRIES:
                raise AgentGaveUp(f"连续 {attempt} 次不合格：{error}") from None
            messages = [*messages, HumanMessage(RETRY_TEMPLATE.format(problem=error))]
    raise AssertionError("unreachable")
