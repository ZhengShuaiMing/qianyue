"""对话入口：和总控 Agent 说话。

每段对话有一个编号，历史存在 Checkpoint 里，带上编号就能接着聊。
对话变长以后，较早的部分会自动压缩成摘要（见 app/graphs/assistant.py 的 ChatSummarizer）。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

import openai
from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import select

from app.api.deps import ChatModelDep, RunManagerDep, SessionDep, SettingsDep
from app.api.sse import SSE_HEADERS, sse
from app.db.models import ConversationTitle, now_iso
from app.graphs.assistant import (
    TOOL_LABELS,
    build_assistant,
    decision,
    is_summary,
    pending_actions,
    sent_at,
    stamped,
    stream_turn,
    thread_config,
    unanswered_calls,
)
from app.llm.privacy import chat_image_turn
from app.llm.screenshot import check_images, images_in, images_rejected
from app.llm.tokens import estimate_tokens
from app.rag.workspace import CHAT, Item

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

ConversationId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
MAX_MESSAGE_SHARE = 0.5  # 一条消息最多占输入预算的这个比例


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=20000)
    images: list[str] = Field(
        default_factory=list,
        description="随消息发的截图（data:image/...;base64,...），直接放进这句话发给助手，"
        "留在对话历史里。张数和大小不限，模型收不了时会说明",
    )
    conversation_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="对话编号。不填就开始一段新对话，编号在第一条 `conversation` 事件里返回",
    )

    @field_validator("images")
    @classmethod
    def real_images(cls, images: list[str]) -> list[str]:
        return check_images(images)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def say_something(self):
        if not self.message and not self.images:
            raise ValueError("先写点什么，或者发一张截图")
        return self


class ConfirmRequest(BaseModel):
    approved: list[str] = Field(
        description="要执行的操作编号（等确认的操作里的 id）。没列出的都不执行，"
        "空列表表示全部不执行"
    )


class PendingAction(BaseModel):
    id: str
    tool: str
    description: str


class ChatMessage(BaseModel):
    role: str = Field(
        description="`user` 你说的，`assistant` 助手说的，`summary` 较早的对话压缩成的摘要，"
        "`result` 专员成果开头的原文（和流式输出里的 `result` 事件是同一份）"
    )
    text: str
    at: str | None = Field(default=None, description="这条话的时间。较早版本存下的对话没有")
    images: list[str] | None = Field(
        default=None, description="你随这句话发的截图（data:image/...;base64,...）"
    )
    tools: list[str] = Field(description="助手这一步调用了哪些工具")
    result: dict | None = Field(
        default=None, description="只有 `result` 有：成果编号、岗位编号、标题、是否只是开头"
    )


class ConversationSummary(BaseModel):
    id: str
    title: str = Field(description="你改过的名字；没改过时是第一句你说的话，最多 40 个字")
    updated_at: str
    waiting: bool = Field(description="有没有等你确认的操作")


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=40)

    @field_validator("title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("名字不能为空")
        return value


class Conversation(BaseModel):
    id: str
    messages: list[ChatMessage]
    waiting: list[PendingAction] = Field(description="等你确认的操作。没有就是空")


def _assistant(request: Request, manager, model) -> CompiledStateGraph:
    return build_assistant(
        model=model,
        engine=request.app.state.engine,
        manager=manager,
        checkpointer=manager.checkpointer,
        token_budget=request.app.state.settings.llm_input_token_budget,
    )


def _stream(
    request: Request, conversation_id: str, agent, graph_input, *, with_images: str | None = None
) -> StreamingResponse:
    """执行一轮并以 SSE 返回。同一段对话同时只处理一轮。

    with_images：这一轮带截图的那句话的编号。模型收不了图时把这句话从历史里删掉，
    不然之后每一轮都会带着这几张图再失败一次。"""
    busy: set[str] = request.app.state.chats_in_progress
    if conversation_id in busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "这段对话正在回复中，请等它结束")
    busy.add(conversation_id)
    config = thread_config(conversation_id)

    async def events() -> AsyncIterator[str]:
        try:
            yield sse("conversation", {"id": conversation_id})
            with chat_image_turn(with_images or ""):
                async for event, data in stream_turn(agent, config, graph_input):
                    yield sse(event, data)
        except openai.APIStatusError as error:
            if with_images and isinstance(error, openai.BadRequestError):
                logger.warning("模型没有收截图：%s", error)
                await agent.aupdate_state(config, {"messages": [RemoveMessage(id=with_images)]})
                message = f"{images_rejected(error)}。这句话没有留在对话里，可以重新发"
            else:
                logger.exception("对话失败")
                message = f"{type(error).__name__}: {error}"
            yield sse("error", {"message": message})
        except Exception as error:  # noqa: BLE001  流已经开始，只能把错误作为事件告诉前端
            logger.exception("对话失败")
            yield sse("error", {"message": f"{type(error).__name__}: {error}"})
        finally:
            busy.discard(conversation_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/stream")
async def chat(
    body: ChatRequest,
    request: Request,
    manager: RunManagerDep,
    model: ChatModelDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """对助手说一句话。返回 SSE：`conversation` 对话编号；`token` 助手正在说的话；
    `tool` 助手调用了一个工具；`run` 交给专员并发起了一次运行；`progress` 专员的进度；
    `result` 专员做完后成果开头的原文（结论由程序原样给出，助手不转述）；
    `confirm` 助手想执行一个需要你确认的操作（用 `/api/chat/{id}/confirm` 答复）；
    `done` 这一轮结束；出错时为 `error`。"""
    conversation_id = body.conversation_id or uuid4().hex
    agent = _assistant(request, manager, model)
    state = await agent.aget_state(thread_config(conversation_id))
    if pending_actions(state):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "有一个操作在等你确认，请先确认或取消，再继续对话"
        )

    history = state.values.get("messages", [])
    # 对话变长以后，较早的部分会自动压缩成摘要，不用开新对话。这里只挡一种情况：
    # 这一句话本身就太长（例如一次贴了好几份 JD），压缩历史也放不下。
    tokens = estimate_tokens(body.message)
    if tokens > settings.llm_input_token_budget * MAX_MESSAGE_SHARE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "这条消息太长，超出了模型一次能处理的长度。请分几次发送，例如每次只粘贴一份 JD",
        )
    # 截图直接放进这句话交给模型看，留在对话历史里，之后几轮也看得到。
    said = HumanMessage(body.message, id=uuid4().hex)
    if body.images:
        content = [{"type": "text", "text": body.message}] if body.message else []
        content += [{"type": "image_url", "image_url": {"url": image}} for image in body.images]
        said = HumanMessage(content=content, id=said.id)
    # 上一轮如果被打断，先给没有结果的工具调用补上结果，再接上这次的话。
    messages = [*unanswered_calls(history), stamped(said)]
    return _stream(
        request,
        conversation_id,
        agent,
        {"messages": messages},
        with_images=said.id if body.images else None,
    )


@router.post("/{conversation_id}/confirm")
async def confirm(
    conversation_id: ConversationId,
    body: ConfirmRequest,
    request: Request,
    manager: RunManagerDep,
    model: ChatModelDep,
) -> StreamingResponse:
    """确认助手想执行的操作（例如修改岗位阶段）：列出的执行，没列出的不执行，然后助手接着说。
    同一轮里有几个操作时可以只执行其中几个。返回的 SSE 与 `/api/chat/stream` 相同。"""
    agent = _assistant(request, manager, model)
    state = await agent.aget_state(thread_config(conversation_id))
    actions = pending_actions(state)
    if not actions:
        raise HTTPException(status.HTTP_409_CONFLICT, "这段对话里没有等你确认的操作")
    unknown = set(body.approved) - {action["id"] for action in actions}
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "部分操作已不在待确认列表中，请刷新页面后重新确认",
        )
    return _stream(request, conversation_id, agent, decision(set(body.approved), actions))


TITLE_CHARS = 40
LIST_LIMIT = 50


async def _chat_threads(manager) -> list[str]:
    """Checkpoint 里所有助手对话的线程编号。表在第一次用到时才建；建过了不会重复建。"""
    await manager.checkpointer.setup()
    cursor = await manager.checkpointer.conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'chat-%'"
    )
    return [row[0] for row in await cursor.fetchall()]


def _default_title(messages) -> str | None:
    """没改过名的对话叫什么：你说的第一句话，最多 TITLE_CHARS 个字。还没说过话时是 None。"""
    first = next(
        (m for m in messages if isinstance(m, HumanMessage) and not is_summary(m)),
        None,
    )
    if first is None:
        return None
    title = " ".join(first.text.split()) or "（发了截图）"
    return title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")


def _summary(conversation_id: str, state, renamed: str | None) -> ConversationSummary | None:
    """对话列表里的一项。还没有内容的对话（刚建、没说话）返回 None。"""
    title = _default_title(state.values.get("messages", []))
    if title is None or not state.created_at:
        return None
    return ConversationSummary(
        id=conversation_id,
        title=renamed or title,
        updated_at=state.created_at,
        waiting=bool(pending_actions(state)),
    )


async def conversation_items(request: Request, manager, session) -> list[Item]:
    """全部助手对话，整理成全局检索要入库的记录：你说的话和助手的回答，不含工具结果和摘要。"""
    renamed = {row.id: row.title for row in session.exec(select(ConversationTitle)).all()}
    agent = _assistant(request, manager, None)
    items = []
    for thread_id in await _chat_threads(manager):
        conversation_id = thread_id.removeprefix("chat-")
        state = await agent.aget_state(thread_config(conversation_id))
        lines = []
        for message in state.values.get("messages", []):
            if is_summary(message) or not message.text.strip():
                continue
            if isinstance(message, HumanMessage):
                lines.append(f"我：{message.text.strip()}")
            elif isinstance(message, AIMessage):
                lines.append(f"助手：{message.text.strip()}")
        summary = _summary(conversation_id, state, renamed.get(conversation_id))
        if not lines or summary is None:
            continue
        title = summary.title
        items.append(
            Item(
                key=f"{CHAT}{conversation_id}",
                type="chat",
                kind="chat",
                title=title,
                date=state.created_at,
                link=f"/assistant/{conversation_id}",
                text="\n".join(lines),
            )
        )
    return items


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(request: Request, manager: RunManagerDep, session: SessionDep):
    """最近的对话，最新的在前，最多 50 段。对话在 Checkpoint 里，改过的名字在 conversations 表。"""
    thread_ids = await _chat_threads(manager)
    renamed = {row.id: row.title for row in session.exec(select(ConversationTitle)).all()}
    agent = _assistant(request, manager, None)
    conversations = []
    for thread_id in thread_ids:
        conversation_id = thread_id.removeprefix("chat-")
        state = await agent.aget_state(thread_config(conversation_id))
        summary = _summary(conversation_id, state, renamed.get(conversation_id))
        if summary:
            conversations.append(summary)
    conversations.sort(key=lambda item: item.updated_at, reverse=True)
    return conversations[:LIST_LIMIT]


@router.get("/{conversation_id}", response_model=Conversation, response_model_exclude_none=True)
async def get_conversation(
    conversation_id: ConversationId, request: Request, manager: RunManagerDep
):
    """一段对话的历史，以及有没有等你确认的操作。"""
    agent = _assistant(request, manager, None)
    state = await agent.aget_state(thread_config(conversation_id))
    history = state.values.get("messages", [])
    if not history:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"对话 {conversation_id} 不存在")
    messages = []
    for message in history:
        if is_summary(message):  # 较早的对话压缩成的摘要，不是用户说的话
            messages.append(ChatMessage(role="summary", text=str(message.text), tools=[]))
        elif isinstance(message, HumanMessage):
            messages.append(
                ChatMessage(
                    role="user",
                    text=message.text,
                    at=sent_at(message),
                    tools=[],
                    images=images_in(message) or None,
                )
            )
        elif isinstance(message, AIMessage):
            tools = [TOOL_LABELS.get(call["name"], call["name"]) for call in message.tool_calls]
            messages.append(
                ChatMessage(
                    role="assistant", text=str(message.text), at=sent_at(message), tools=tools
                )
            )
        elif isinstance(message, ToolMessage) and message.artifact:
            for conclusion in message.artifact:
                details = {key: value for key, value in conclusion.items() if key != "text"}
                messages.append(
                    ChatMessage(role="result", text=conclusion["text"], tools=[], result=details)
                )
    return Conversation(id=conversation_id, messages=messages, waiting=pending_actions(state))


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: ConversationId,
    body: ConversationRename,
    request: Request,
    manager: RunManagerDep,
    session: SessionDep,
):
    """给对话改名。"""
    if f"chat-{conversation_id}" not in await _chat_threads(manager):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"对话 {conversation_id} 不存在")
    row = session.get(ConversationTitle, conversation_id) or ConversationTitle(
        id=conversation_id, title=body.title
    )
    row.title = body.title
    row.updated_at = now_iso()
    session.add(row)
    session.commit()
    # 直接读这一段，不从列表里找：列表只有最近 50 段，改旧对话的名字也要能返回
    state = await _assistant(request, manager, None).aget_state(thread_config(conversation_id))
    summary = _summary(conversation_id, state, body.title)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"对话 {conversation_id} 还没有内容")
    return summary


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: ConversationId, request: Request, manager: RunManagerDep, session: SessionDep
):
    """删除一段对话和它的全部历史，找不回来。对话里发起的评估等运行和成果不受影响。"""
    if conversation_id in request.app.state.chats_in_progress:
        raise HTTPException(status.HTTP_409_CONFLICT, "这段对话正在回复中，请等回复结束后再删除")
    if f"chat-{conversation_id}" not in await _chat_threads(manager):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"对话 {conversation_id} 不存在")
    await manager.checkpointer.adelete_thread(f"chat-{conversation_id}")
    await asyncio.to_thread(manager.knowledge.workspace.forget, f"{CHAT}{conversation_id}")
    row = session.get(ConversationTitle, conversation_id)
    if row:
        session.delete(row)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
