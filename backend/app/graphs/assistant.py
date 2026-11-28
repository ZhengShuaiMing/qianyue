"""总控 Agent：迁跃的对话入口。

它是一个 ReAct Agent，自己不做评估、不写简历，只负责三件事：查看岗位和成果、保存用户
提供的内容、把任务交接（Handoff）给专员。专员就是前面几步做好的那些图，各自在一次运行里
完成，结论交回总控，由总控转述给用户。

对话存在 Checkpoint 里，线程编号是 chat-<对话编号>，所以关掉页面、重启服务后还能接着聊。
改阶段这类写入操作在执行前会中断，等用户确认。同一轮里不需要确认的工具照常执行，
不会被等确认的操作卡住。
"""

import logging
from collections.abc import AsyncIterator
from datetime import date

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
)
from langchain_core.language_models import BaseChatModel, FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import Engine
from sqlmodel import Session

from app.collectors.registry import PLATFORMS
from app.core.config import BACKEND_DIR
from app.db.models import (
    DOCUMENT_KIND_NAMES,
    RUN_KIND_NAMES,
    STAGE_NAMES,
    Job,
    JobStage,
    RunKind,
    now_iso,
)
from app.graphs.runner import RunManager
from app.llm.screenshot import images_in
from app.llm.tokens import IMAGE_TOKENS, estimate_tokens
from app.tools.workspace import build_workspace_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "assistant.md").read_text(encoding="utf-8").strip()
SUMMARY_PROMPT = (BACKEND_DIR / "prompts" / "chat_summary.md").read_text(encoding="utf-8").strip()
SUMMARY_MARK = "（较早的对话已经压缩成下面这份摘要）"
SUMMARY_AT = 0.6  # 对话占到输入预算的这个比例时，把较早的部分压缩成摘要
KEEP_RECENT = 0.25  # 压缩时，最近的这么多（占输入预算的比例）保持原样


def count_tokens(messages) -> int:
    """估算一组消息有多少 Token。和别处用同一个估算办法；截图每张按 IMAGE_TOKENS 算，
    不按图片数据的长度算（那样一张图就会被当成几十万 Token）。"""
    parts = []
    images = 0
    for message in messages:
        parts.append(message.text)
        images += len(images_in(message))
        parts += [str(call["args"]) for call in getattr(message, "tool_calls", None) or []]
    return estimate_tokens("".join(parts)) + images * IMAGE_TOKENS


def is_summary(message: BaseMessage) -> bool:
    """这条消息是不是程序放进去的摘要（不是用户说的话）。"""
    return message.additional_kwargs.get("lc_source") == "summarization"


class ChatSummarizer(SummarizationMiddleware):
    """长对话自动摘要：对话快到输入上限时，把较早的部分换成一份摘要，最近几轮保持原样。

    用 LangChain 自带的摘要中间件，改了两处：摘要规则用 backend/prompts/chat_summary.md；
    摘要消息的开头用中文写明它是摘要。
    """

    def __init__(self, model: BaseChatModel, token_budget: int) -> None:
        super().__init__(
            model,
            trigger=("tokens", int(token_budget * SUMMARY_AT)),
            keep=("tokens", int(token_budget * KEEP_RECENT)),
            token_counter=count_tokens,
            summary_prompt=SUMMARY_PROMPT,
            trim_tokens_to_summarize=None,  # 要压缩的部分全部给模型看，不再裁
        )

    def _format_summary_prompt(self, messages) -> str:
        """摘要只读文字。带截图的话标出附了几张截图，摘要里至少记得发过什么。"""
        marked = []
        for message in messages:
            images = images_in(message)
            if images:
                note = f"〔附了 {len(images)} 张截图，内容看后面助手的回复〕"
                message = HumanMessage(f"{message.text}\n{note}".strip())
            marked.append(message)
        return super()._format_summary_prompt(marked)

    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        return [
            HumanMessage(
                content=f"{SUMMARY_MARK}\n\n{summary.strip()}",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]


def stamped(message: BaseMessage) -> BaseMessage:
    """在消息上记下时间，对话页显示用。放在 response_metadata 里：它不会发给模型。"""
    message.response_metadata = {**message.response_metadata, "at": now_iso()}
    return message


def sent_at(message: BaseMessage) -> str | None:
    return message.response_metadata.get("at")


class Timestamps(AgentMiddleware):
    """给助手说的每一条话记下时间。"""

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        for message in getattr(response, "result", None) or [response]:
            if isinstance(message, AIMessage):
                stamped(message)
        return response


TOOL_LABELS = {
    "list_jobs": "查看岗位列表",
    "search_workspace": "检索",
    "get_job": "查看岗位",
    "read_artifact": "读取成果",
    "add_job": "保存岗位",
    "save_communication": "保存沟通记录",
    "change_stage": "修改阶段",
    "save_star": "保存经历",
    "read_material": "读取材料",
    "save_material": "保存材料",
    "hand_off": "交给专员",
    "collect_jobs": "采集岗位",
    "open_platform_browser": "打开专用浏览器",
}
# 只读取对话历史、不让模型说话时用的占位模型。这样没有配置 Key 也能看历史。
NO_MODEL = FakeListChatModel(responses=[])
INTERRUPTED = "上一次执行被打断了，这个工具没有返回结果。需要的话请重新调用。"
REJECTED = "用户没有确认，这个操作没有执行：{description}。数据保持原样，不要原样再次尝试"


def thread_config(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": f"chat-{conversation_id}"}}


def describe_call(name: str, args: dict) -> str:
    """一次工具调用的简短说明，给用户看进度用。不带 JD、聊天内容这类正文。

    这里拿到的是模型原样给出的参数，还没经过工具的校验：参数不对时只是不写说明，
    由工具返回「参数错误」让模型自己改，不能让整轮对话断掉。
    """
    try:
        return _describe_call(name, args)
    except (KeyError, ValueError, TypeError, AttributeError):
        return ""


def _describe_call(name: str, args: dict) -> str:
    if name == "hand_off":
        specialist = RUN_KIND_NAMES.get(RunKind(args["specialist"]), args["specialist"])
        jobs = "、".join(f"#{job_id}" for job_id in args.get("job_ids", []))
        return f"{specialist}，岗位 {jobs}"
    if name == "collect_jobs":
        platforms = "、".join(PLATFORMS[key].name for key in args.get("platforms", []))
        scope = "、".join(args.get("queries", [])) + "｜" + "、".join(args.get("cities", []))
        return f"{platforms}｜{scope}"
    if name == "open_platform_browser":
        return PLATFORMS[args["platform"]].name if args.get("platform") in PLATFORMS else ""
    if name == "save_star":
        return str(args.get("title", ""))
    if name in ("read_material", "save_material"):
        return DOCUMENT_KIND_NAMES.get(args.get("kind"), "")
    if name == "add_job":
        return " — ".join(part for part in (args.get("company"), args.get("title")) if part)
    if name == "list_jobs":
        return args.get("keyword", "")
    if name == "search_workspace":
        return args.get("query", "")
    if name == "read_artifact":
        return f"成果 #{args.get('artifact_id')}"
    if "job_id" in args:
        return f"岗位 #{args['job_id']}"
    return ""


def build_assistant(
    *,
    model: BaseChatModel | None,
    engine: Engine,
    manager: RunManager,
    checkpointer: BaseCheckpointSaver,
    token_budget: int,
) -> CompiledStateGraph:
    """组装总控 Agent。只读取对话历史时，model 可以传 None。"""
    model = model or NO_MODEL

    def describe_stage_change(tool_call, state, runtime) -> str:
        """给用户确认时看的一句话：哪个岗位，从哪个阶段改到哪个阶段。"""
        args = tool_call["args"]
        target = STAGE_NAMES.get(args.get("stage"), str(args.get("stage")))
        with Session(engine) as session:
            job = session.get(Job, args.get("job_id"))
        if job is None or job.deleted_at:
            return f"把岗位 #{args.get('job_id')} 的阶段改为「{target}」（这个岗位不存在）"
        name = f"{job.company or '未填公司'} — {job.title or '未填职位'}（#{job.id}）"
        day = args.get("happened_on") or date.today().isoformat()
        return (
            f"把「{name}」的阶段从「{STAGE_NAMES[JobStage(job.stage)]}」改为「{target}」，"
            f"日期 {day}"
        )

    return create_agent(
        model,
        build_workspace_tools(engine, manager, model),
        # 用户说「昨天投的」时，助手要知道今天是哪天才能填改阶段的日期
        system_prompt=f"{SYSTEM_PROMPT}\n\n今天是 {date.today().isoformat()}。",
        middleware=[
            Timestamps(),
            # 对话快到输入上限时，把较早的部分压缩成摘要。
            ChatSummarizer(model, token_budget),
            # 写入用户求职进展的操作，执行前先停下来等用户确认。
            # per_call：只有需要确认的那次调用停下，同一轮里的其他工具照常执行。
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "change_stage": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": describe_stage_change,
                    }
                },
                interrupt_mode="per_call",
            ),
        ],
        checkpointer=checkpointer,
    )


def pending_actions(state) -> list[dict]:
    """对话停下来等用户确认的操作：{id, tool, description}。没有在等就是空列表。"""
    return [
        {
            "id": interrupt.id,
            "tool": interrupt.value["name"],
            "description": interrupt.value["description"],
        }
        for task in state.tasks
        for interrupt in task.interrupts
    ]


def unanswered_calls(messages: list[BaseMessage]) -> list[ToolMessage]:
    """上一轮被打断时，模型发起了、但没有拿到结果的工具调用。给每个补一条「被打断」的结果。

    模型接口要求每个工具调用后面都跟着它的结果，缺了会报错，对话就接不下去。
    """
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last_ai is None:
        return []
    return [
        ToolMessage(INTERRUPTED, tool_call_id=call["id"], name=call["name"])
        for call in last_ai.tool_calls
        if call["id"] not in answered
    ]


def decision(approved: set[str], actions: list[dict]) -> Command:
    """把用户的答复交还给 Agent。approved 是要执行的操作编号，其余的都不执行。

    不执行时把「原本要做什么」告诉模型，里面有岗位当前的阶段，免得它接下来说错。
    """
    answers = {
        action["id"]: {"type": "approve"}
        if action["id"] in approved
        else {"type": "reject", "message": REJECTED.format(description=action["description"])}
        for action in actions
    }
    return Command(resume=answers)


async def stream_turn(
    agent: CompiledStateGraph, config: dict, graph_input
) -> AsyncIterator[tuple[str, dict]]:
    """执行一轮对话，逐个产出事件：

    token 模型正在说的话；tool 调用了一个工具；run 交给专员并发起了一次运行；
    progress 专员的进度；result 专员做完后成果开头的原文；confirm 停下来等用户确认；
    done 这一轮结束。
    """
    usage = {"input_tokens": 0, "output_tokens": 0}
    model_calls = tool_calls = 0
    waiting = []
    async for mode, chunk in agent.astream(
        graph_input, config, stream_mode=["messages", "updates", "custom"]
    ):
        if mode == "custom":  # 交接工具转发的专员进度
            yield chunk["type"], {key: value for key, value in chunk.items() if key != "type"}
        elif mode == "messages":
            message, metadata = chunk
            # 只要助手自己说的话。工具里也会调用模型（例如整理经历时核对措辞），那些不给用户看。
            # 压缩对话时写摘要的那次调用也不给。
            if metadata.get("langgraph_node") != "model":
                continue
            if isinstance(message, AIMessage) and message.text:
                yield "token", {"text": str(message.text)}
        elif "__interrupt__" in chunk:
            waiting += [
                {
                    "id": interrupt.id,
                    "tool": interrupt.value["name"],
                    "description": interrupt.value["description"],
                }
                for interrupt in chunk["__interrupt__"]
            ]
        else:
            for message in (chunk.get("model") or {}).get("messages", []):
                model_calls += 1
                for key in usage:
                    usage[key] += (message.usage_metadata or {}).get(key, 0)
                for call in message.tool_calls:
                    tool_calls += 1
                    yield (
                        "tool",
                        {
                            "name": call["name"],
                            "label": TOOL_LABELS.get(call["name"], call["name"]),
                            "detail": describe_call(call["name"], call["args"]),
                        },
                    )

    logger.info(
        "对话一轮结束：模型调用 %s 次，工具调用 %s 次，input_tokens=%s output_tokens=%s 等确认=%s",
        model_calls,
        tool_calls,
        usage["input_tokens"],
        usage["output_tokens"],
        len(waiting),
    )
    if waiting:
        yield "confirm", {"actions": waiting}
    yield "done", {"waiting": bool(waiting), "usage": usage}
