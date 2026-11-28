"""Agent 代答模型：没有模型 Key 时，由用户自己的 Agent（通过 MCP 连着迁跃）替迁跃的模型作答。

迁跃的图照常运行，到了要调用模型的地方，AgentChatModel 不去请求任何 API，而是把这次要问的内容
（已按隐私设置脱敏）挂成一个「模型请求」，等对面的 Agent 用 MCP 工具领走、写好、交回，再把回复
当成模型的输出交还给图。提示词、程序校验、不合格重试、保存、进度和断点都和有 Key 时一样。

需要工具的步骤（公司研究、沟通建议里的 ReAct）也照常：请求里列出迁跃的工具，Agent 交回要调用的
工具和参数，由迁跃执行，结果作为下一个请求的内容。这样研究报告引用的来源都出自迁跃的搜索记录，
程序能核对。

MCP 曾经有让服务端借用客户端模型的 sampling，但 2026-07-28 版规范已弃用，主流客户端也不支持，
所以这里走普通的工具调用：Agent 调「发起任务」拿到第一个请求，调「交回」拿到下一个，直到完成。
"""

import asyncio
import itertools
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from app.core.context import run_id_var
from app.llm.privacy import mask_payload

logger = logging.getLogger(__name__)

# Agent 领走请求后多久没交回就算放弃。运行会失败，可以从断点继续。
REPLY_TIMEOUT_SECONDS = 30 * 60


class AgentNoReply(Exception):
    """等了太久，Agent 一直没有交回。"""


@dataclass
class ModelRequest:
    id: str
    run_id: str
    messages: list[dict]  # OpenAI 格式，已脱敏
    tools: list[dict]  # OpenAI 格式的工具定义；没有工具就是空列表
    created_at: str
    # 和本运行上一个请求是同一段对话时：上一个请求的编号，和这次新增的消息从第几条开始
    continues: str | None = None
    new_from: int = 0
    # 系统要求和本运行之前某个请求的一字不差时，那个请求的编号（批量评估时每个岗位都一样）
    same_system_as: str | None = None
    reply: asyncio.Future = field(repr=False, default=None)  # type: ignore[assignment]


class AgentBroker:
    """在图和 MCP 工具之间转交模型请求。一个后端进程一个，所有运行共用。"""

    def __init__(self, on_request: Callable[[ModelRequest], Awaitable[None]]):
        self._on_request = on_request
        self._pending: dict[str, ModelRequest] = {}
        # 每个运行最近一个请求的（编号、消息、Agent 的回复），用来判断下一个请求是不是接着同一段对话
        self._last: dict[str, tuple[str, list[dict], dict]] = {}
        self._systems: dict[str, dict[str, str]] = {}  # 运行 → {系统要求全文: 第一次出现的请求编号}
        self._taken: set[str] = set()  # Agent 已经领过请求的运行：它在做了，不再是「等接手」
        self._numbers = itertools.count(1)

    async def ask(self, run_id: str, messages: list[dict], tools: list[dict]) -> dict:
        """挂起一个请求，等 Agent 交回 {"text", "tool_calls"}。"""
        request = ModelRequest(
            id=f"{next(self._numbers)}-{uuid.uuid4().hex[:6]}",
            run_id=run_id,
            messages=messages,
            tools=tools,
            created_at=datetime.now(UTC).isoformat(),
            reply=asyncio.get_running_loop().create_future(),
        )
        last = self._last.get(run_id)
        if last and messages[: len(last[1]) + 1] == [*last[1], last[2]]:
            request.continues, request.new_from = last[0], len(last[1]) + 1
        if messages and messages[0]["role"] == "system":
            seen = self._systems.setdefault(run_id, {})
            request.same_system_as = seen.setdefault(str(messages[0]["content"]), request.id)
            if request.same_system_as == request.id:
                request.same_system_as = None
        self._pending[request.id] = request
        try:
            await self._on_request(request)
            async with asyncio.timeout(REPLY_TIMEOUT_SECONDS):
                reply = await request.reply
        except TimeoutError:
            raise AgentNoReply(
                f"等了 {REPLY_TIMEOUT_SECONDS // 60} 分钟，你的 Agent 没有交回结果。"
                "在 Agent 里让它从断点继续这个任务即可"
            ) from None
        finally:
            self._pending.pop(request.id, None)
        assistant = {"role": "assistant", "content": reply["text"]}
        if reply["tool_calls"]:
            assistant["tool_calls"] = [
                {
                    "type": "function",
                    "id": call["id"],
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["args"], ensure_ascii=False),
                    },
                }
                for call in reply["tool_calls"]
            ]
        self._last[run_id] = (request.id, messages, assistant)
        return reply

    def answer(self, request_id: str, text: str, tool_calls: list[dict]) -> str | None:
        """交回一个请求。有问题时返回原因（请求仍然挂着，Agent 可以改了再交）。"""
        request = self._pending.get(request_id)
        if request is None or request.reply.done():
            return f"没有在等编号为 {request_id} 的请求：它可能已经交回，或者任务已结束、被取消。"
        known = {tool["function"]["name"] for tool in request.tools}
        calls = []
        for call in tool_calls:
            name, args = call.get("name"), call.get("args", {})
            if name not in known:
                listed = "、".join(sorted(known)) or "（这一步不能用工具）"
                return f"没有交回：这一步没有叫 {name} 的工具。可用的工具：{listed}"
            if not isinstance(args, dict):
                return f"没有交回：工具 {name} 的 args 要是一个对象"
            calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}"})
        if not text.strip() and not calls:
            return "没有交回：text 和 tool_calls 至少要有一个"
        request.reply.set_result({"text": text, "tool_calls": calls})
        return None

    def get(self, request_id: str) -> ModelRequest | None:
        return self._pending.get(request_id)

    def take(self, request: ModelRequest) -> None:
        """请求的内容交给了 Agent。"""
        self._taken.add(request.run_id)

    def state(self, run_id: str) -> str | None:
        """给界面看：waiting 有请求等 Agent 来领，working Agent 已经在做，None 不归 Agent 管。"""
        if run_id in self._taken:
            return "working"
        return "waiting" if self.waiting_on(run_id) else None

    def waiting_on(self, run_id: str) -> bool:
        """这个运行有没有请求在等 Agent 交回。"""
        return any(r.run_id == run_id and not r.reply.done() for r in self._pending.values())

    def next_pending(self, run_id: str | None = None) -> ModelRequest | None:
        """最早的一个还在等的请求。给了 run_id 时优先这个运行的。"""
        waiting = [r for r in self._pending.values() if not r.reply.done()]
        mine = [r for r in waiting if r.run_id == run_id]
        return (mine or waiting or [None])[0]

    def forget(self, run_id: str) -> None:
        """运行结束后不再需要比对上一段对话。"""
        self._last.pop(run_id, None)
        self._systems.pop(run_id, None)
        self._taken.discard(run_id)


class AgentChatModel(BaseChatModel):
    """把模型调用转交给 Agent 的聊天模型。只能在事件循环里用（图都是异步运行的）。"""

    broker: Any = Field(exclude=True, repr=False)
    # 开了「发给模型前隐藏个人信息」时是用户填的敏感词（可以为空字符串）；关了是 None
    privacy_terms: str | None = Field(default=None, exclude=True, repr=False)

    # 同一个 Agent 重复回答同一个问题不是独立的样本，评估不做分数线附近的复评
    independent_samples: bool = False

    @property
    def _llm_type(self) -> str:
        return "qianyue-agent"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError("Agent 代答只支持异步调用")

    async def _agenerate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        payload = convert_to_openai_messages(messages)
        if self.privacy_terms is not None:
            mask_payload(messages, payload, self.privacy_terms)
        reply = await self.broker.ask(run_id_var.get(), payload, kwargs.get("tools") or [])
        message = AIMessage(content=reply["text"], tool_calls=reply["tool_calls"])
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """create_agent 用它告诉模型有哪些工具。工具定义随请求交给 Agent。"""
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])
