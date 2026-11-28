"""设置页：模型服务和联网搜索。保存后立即生效，不用重启。"""

from typing import Literal

import httpx
import openai
from ddgs.exceptions import DDGSException, TimeoutException
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.api.deps import SessionDep, SettingsDep
from app.core.config import SearchProviderKey
from app.db import app_settings
from app.llm.factory import LLMNotConfigured, create_chat_model
from app.tools.web import PROVIDERS, SearchKeyRejected, WebError, search_function

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsRead(BaseModel):
    hide_personal_info: bool
    privacy_terms: str
    llm_base_url: str
    llm_model: str
    llm_api_key_set: bool = Field(description="有没有 Key。Key 本身不返回")
    llm_api_key_tail: str = Field(
        description="Key 的最后 4 位，方便你认出是哪一个；没有 Key 时为空"
    )
    search_provider: SearchProviderKey = Field(description="联网搜索用哪家")
    search_api_key_set: bool
    search_api_key_tail: str
    saved_in_app: bool = Field(description="是不是在设置页保存过。没保存过时显示的是 .env 里的值")


class SettingsSave(BaseModel):
    hide_personal_info: bool | None = None
    privacy_terms: str | None = None
    llm_base_url: str = Field(min_length=1, max_length=300)
    llm_model: str = Field(min_length=1, max_length=100)
    llm_api_key: str | None = Field(
        default=None, max_length=300, description="新的 Key。不填表示沿用现在的 Key"
    )
    search_provider: SearchProviderKey | None = Field(default=None, description="不填表示不改")
    search_api_key: str | None = Field(
        default=None, max_length=300, description="搜索服务的新 Key。不填表示沿用"
    )

    @field_validator("llm_base_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("模型服务地址要以 http:// 或 https:// 开头")
        return value

    @field_validator("llm_model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("模型名不能为空")
        return value.strip()

    @field_validator("llm_api_key", "search_api_key")
    @classmethod
    def strip_key(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def _tail(secret) -> tuple[bool, str]:
    key = secret.get_secret_value().strip() if secret else ""
    return bool(key), key[-4:] if len(key) >= 8 else ""


def _read(settings, saved: bool) -> SettingsRead:
    llm_set, llm_tail = _tail(settings.llm_api_key)
    search_set, search_tail = _tail(settings.search_api_key)
    return SettingsRead(
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_api_key_set=llm_set,
        hide_personal_info=settings.hide_personal_info,
        privacy_terms=settings.privacy_terms,
        llm_api_key_tail=llm_tail,
        search_provider=settings.search_provider,
        search_api_key_set=search_set,
        search_api_key_tail=search_tail,
        saved_in_app=saved,
    )


@router.get("", response_model=SettingsRead)
def get_settings(settings: SettingsDep, session: SessionDep):
    """现在用的配置。设置页保存过的项优先，没保存过的沿用 backend/.env。"""
    return _read(settings, app_settings.load(session) is not None)


@router.put("", response_model=SettingsRead)
def save_settings(body: SettingsSave, request: Request, session: SessionDep):
    """保存配置，立即生效。Key 不填就沿用现在的；搜索服务不填就不改。
    选了要 Key 的搜索服务时必须有 Key：换了服务要填新的，旧的 Key 是另一家的。"""
    current = request.app.state.settings
    provider = body.search_provider or current.search_provider
    if provider in PROVIDERS:
        switched = provider != current.search_provider
        if not (body.search_api_key or (not switched and current.search_api_key)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{PROVIDERS[provider].lead}需要填写 Key 才能使用；"
                "不想申请 Key 时可以选择 DuckDuckGo",
            )
    keep = app_settings.KEEP
    row = app_settings.save(
        session,
        llm_base_url=body.llm_base_url,
        llm_model=body.llm_model,
        llm_api_key=body.llm_api_key,
        hide_personal_info=body.hide_personal_info if body.hide_personal_info is not None else keep,
        privacy_terms=body.privacy_terms if body.privacy_terms is not None else keep,
        search_provider=body.search_provider or keep,
        search_api_key=body.search_api_key,
    )
    request.app.state.use_settings(app_settings.apply(request.app.state.base_settings, row))
    return _read(request.app.state.settings, True)


class Secret(BaseModel):
    value: str = Field(description="完整的 Key；没有时为空")


@router.get("/secret/{name}", response_model=Secret)
def read_secret(name: Literal["llm_api_key", "search_api_key"], settings: SettingsDep):
    """设置页按住眼睛图标时取一次完整的 Key。后端只监听本机，请求日志只记地址不记内容。"""
    secret = getattr(settings, name)
    return Secret(value=secret.get_secret_value() if secret else "")


class ProbeResult(BaseModel):
    status: Literal["ok", "auth", "missing_key", "timeout", "model_not_found", "empty", "failed"]
    message: str


class ModelProbe(BaseModel):
    """设置页上还没保存的值。不填的项用已保存的配置，整个不传就等于测试已保存的配置。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_model: str | None = Field(default=None, max_length=100)
    llm_api_key: str | None = Field(
        default=None, max_length=300, description="留空表示用已保存的 Key"
    )


class SearchProbe(BaseModel):
    search_provider: SearchProviderKey | None = None
    search_api_key: str | None = Field(
        default=None, max_length=300, description="留空表示用已保存的 Key"
    )


def _probe_settings(settings, body: ModelProbe | None):
    """把表单里填的值叠到已保存的配置上，只用来测试，不写入任何地方。"""
    if body is None:
        return settings
    update = {}
    if body.llm_base_url and body.llm_base_url.strip():
        update["llm_base_url"] = body.llm_base_url.strip().rstrip("/")
    if body.llm_model and body.llm_model.strip():
        update["llm_model"] = body.llm_model.strip()
    if body.llm_api_key and body.llm_api_key.strip():
        update["llm_api_key"] = SecretStr(body.llm_api_key.strip())
    return settings.model_copy(update=update) if update else settings


@router.post("/test/model")
async def probe_model(settings: SettingsDep, body: ModelProbe | None = None) -> ProbeResult:
    settings = _probe_settings(settings, body)
    if not settings.llm_base_url.startswith(("http://", "https://")):
        return ProbeResult(status="failed", message="模型服务地址要以 http:// 或 https:// 开头。")
    try:
        model = create_chat_model(settings, timeout=15, max_retries=0)
        await model.ainvoke("你好，请回复 OK。")
    except LLMNotConfigured:
        return ProbeResult(status="missing_key", message="请先保存模型 Key。")
    except openai.APITimeoutError:
        return ProbeResult(status="timeout", message="模型服务响应超时。")
    except (openai.AuthenticationError, openai.PermissionDeniedError):
        return ProbeResult(status="auth", message="模型服务拒绝了当前凭据，请检查 Key 和模型权限。")
    except openai.APIError as error:
        if error.code == "model_not_found":
            return ProbeResult(status="model_not_found", message="服务商找不到已保存的模型。")
        return ProbeResult(status="failed", message="模型请求失败，请核对服务地址和模型名。")
    except (httpx.HTTPError, ValueError):
        return ProbeResult(status="failed", message="模型请求失败，请核对服务地址和模型名。")
    return ProbeResult(status="ok", message="模型已响应。")


@router.post("/test/search")
def probe_search(settings: SettingsDep, body: SearchProbe | None = None) -> ProbeResult:
    provider = (body and body.search_provider) or settings.search_provider
    typed = (body.search_api_key or "").strip() if body else ""
    if provider == "ddgs":
        key = None
    elif typed:
        key = typed
    elif provider == settings.search_provider:
        # 没换服务商，沿用已保存的 Key；换了就必须填新的，旧的 Key 是另一家的。
        key = settings.search_api_key.get_secret_value() if settings.search_api_key else None
    else:
        key = None
    if provider != "ddgs" and not key:
        name = PROVIDERS[provider].lead if provider in PROVIDERS else provider
        return ProbeResult(status="missing_key", message=f"{name}需要填写 Key 才能测试。")
    try:
        # 连通性只测一次，工作流里的「换词重试」不用于这个按钮。
        hits = search_function(provider, key)("Python", 1)
    except (httpx.TimeoutException, TimeoutException):
        return ProbeResult(status="timeout", message="搜索服务响应超时。")
    except SearchKeyRejected:
        return ProbeResult(status="auth", message="搜索服务拒绝了当前凭据，请检查 Key 和额度。")
    except DDGSException as error:
        if str(error) != "No results found.":
            return ProbeResult(status="failed", message="搜索请求失败，请检查当前服务。")
        hits = []
    except (httpx.HTTPError, ValueError, RuntimeError, WebError):
        return ProbeResult(status="failed", message="搜索请求失败，请检查当前服务。")
    if not hits:
        return ProbeResult(status="empty", message="搜索服务可访问，本次没有查询结果。")
    return ProbeResult(status="ok", message="搜索成功，已收到结果。")
