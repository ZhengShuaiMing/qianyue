from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.llm.privacy import PrivateChatModel


class LLMNotConfigured(Exception):
    pass


def create_chat_model(
    settings: Settings, *, timeout: float = 120, max_retries: int = 2
) -> BaseChatModel:
    """按配置创建聊天模型。全项目只在这里决定用哪家模型，业务代码拿到的都是统一接口。

    用 OpenAI 兼容协议接入，所以 DeepSeek、通义千问、本地 Ollama 只需要改设置，不用改代码。
    """
    if not settings.llm_configured:
        raise LLMNotConfigured(
            "还没有配置模型 Key。请在设置页填写，"
            "或者在 backend/.env 里填 QIANYUE_LLM_API_KEY 后重启。"
        )
    model_class = PrivateChatModel if settings.hide_personal_info else ChatOpenAI
    return model_class(
        **({"privacy_terms": settings.privacy_terms} if settings.hide_personal_info else {}),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        use_responses_api=False,
        stream_usage=True,  # 流式输出结束时附带 Token 用量
        timeout=timeout,
        max_retries=max_retries,
    )
