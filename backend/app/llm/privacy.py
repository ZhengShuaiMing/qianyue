"""只在构造模型请求时改副本。对话断点与基础材料继续保存原文。"""

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

from langchain_openai import ChatOpenAI
from pydantic import Field

PLACEHOLDERS = ("〔手机号〕", "〔邮箱〕", "〔已隐藏〕")
_PHONE = r"(?<!\d)(?:(?:\+?86)[ -]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)"
_EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+"
# None 用于不含对话历史的任务（例如岗位截图录入）。对话中只允许当轮的图。
_image_turn = ContextVar[str | None]("chat_image_turn", default=None)


@contextmanager
def chat_image_turn(message_id: str):
    token = _image_turn.set(message_id)
    try:
        yield
    finally:
        _image_turn.reset(token)


def mask_text(text: str, terms: str) -> str:
    # 每行一个词，也可以在一行里用空格分开。整行和拆开的词都算，宁可多遮不漏遮。
    words = set()
    for line in terms.splitlines():
        if line.strip():
            words.add(line.strip())
            words.update(line.split())
    words = sorted(words, key=len, reverse=True)
    pattern = f"(?P<phone>{_PHONE})|(?P<email>{_EMAIL})"
    if words:
        pattern += "|(?P<custom>" + "|".join(map(re.escape, words)) + ")"
    labels = dict(zip(("phone", "email", "custom"), PLACEHOLDERS, strict=True))
    return re.sub(pattern, lambda m: labels[m.lastgroup], text)


def _mask_values(value, terms):
    if isinstance(value, dict):
        return {key: _mask_values(item, terms) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_values(item, terms) for item in value]
    return mask_text(value, terms) if isinstance(value, str) else value


def _mask_json(text: str, terms: str) -> str:
    # LangChain 工具消息和工具参数可能包含 Unicode 转义的 JSON 字符串。
    if text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text)
        except ValueError:
            pass
        else:
            return json.dumps(_mask_values(value, terms), ensure_ascii=False)
    return mask_text(text, terms)


def mask_payload(originals, payload_messages: list[dict], terms: str) -> None:
    """就地遮住 OpenAI 格式消息（发出去的副本）里的个人信息。工具名、调用 ID 保持原值。

    originals 是对应的 LangChain 消息，用来判断截图属不属于当轮。
    PrivateChatModel 和 Agent 代答模型（app/llm/agent_model.py）共用。
    """
    for original, message in zip(originals, payload_messages, strict=True):
        content = message["content"]
        if isinstance(content, str):
            mask = _mask_json if message["role"] == "tool" else mask_text
            message["content"] = mask(content, terms)
        elif isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "image_url":
                    if _image_turn.get() is None or _image_turn.get() == original.id:
                        parts.append(part)
                elif part.get("type") == "text":
                    parts.append({**part, "text": mask_text(part["text"], terms)})
                else:
                    parts.append(part)
            message["content"] = parts or "〔历史截图〕"
        for call in message.get("tool_calls", []):
            fn = call["function"]
            call["function"] = {**fn, "arguments": _mask_json(fn["arguments"], terms)}


class PrivateChatModel(ChatOpenAI):
    privacy_terms: str = Field(exclude=True, repr=False)

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        mask_payload(messages, payload["messages"], self.privacy_terms)
        return payload


def mask_for_model(model, text: str) -> str:
    """发给模型前会脱敏的模型（PrivateChatModel、开了隐藏的代答模型），校验引用时也按同样的规则比对。"""
    terms = getattr(model, "privacy_terms", None)
    return mask_text(text, terms) if terms is not None else text
