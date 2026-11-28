from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from langchain_core.language_models import BaseChatModel
from sqlmodel import Session
from starlette.requests import HTTPConnection

from app.core.config import Settings
from app.graphs.runner import RunManager
from app.llm.agent_model import AgentChatModel
from app.llm.factory import LLMNotConfigured, create_chat_model
from app.rag.knowledge import KnowledgeBase

# 参数类型用 HTTPConnection：它是普通请求和 WebSocket 连接共同的父类，两种接口都能用这些依赖。


def get_settings(connection: HTTPConnection) -> Settings:
    return connection.app.state.settings


def get_session(connection: HTTPConnection) -> Iterator[Session]:
    with Session(connection.app.state.engine) as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_chat_model(settings: SettingsDep) -> BaseChatModel:
    try:
        return create_chat_model(settings)
    except LLMNotConfigured as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None


ChatModelDep = Annotated[BaseChatModel, Depends(get_chat_model)]


def resolve_chat_model(app) -> BaseChatModel:
    """供 MCP 等接口外调用方获取应用当前使用的对话模型。"""
    override = app.dependency_overrides.get(get_chat_model)
    return override() if override else create_chat_model(app.state.settings)


def resolve_mcp_model(app) -> BaseChatModel:
    """MCP 发起任务用的模型：配了 Key 用 Key；没配时由连着的 Agent 代答（llm/agent_model.py）。"""
    try:
        return resolve_chat_model(app)
    except LLMNotConfigured:
        settings = app.state.settings
        return AgentChatModel(
            broker=app.state.runs.agent,
            privacy_terms=settings.privacy_terms if settings.hide_personal_info else None,
        )


def get_run_manager(connection: HTTPConnection) -> RunManager:
    return connection.app.state.runs


RunManagerDep = Annotated[RunManager, Depends(get_run_manager)]


def get_knowledge(connection: HTTPConnection) -> KnowledgeBase:
    return connection.app.state.knowledge


KnowledgeDep = Annotated[KnowledgeBase, Depends(get_knowledge)]
