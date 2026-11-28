"""Output Parser：把模型返回的文本变成 Pydantic 对象。各个功能共用。"""

from json import JSONDecodeError
from typing import TypeVar

from langchain_core.utils.json import parse_json_markdown
from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)


class InvalidOutput(ValueError):
    """模型输出不合格。信息里不带原文，可以反馈给模型重试，也可以写进日志。"""


def parse_json_as(text: str, schema: type[M]) -> M:
    """解析模型输出的 JSON，允许外面包一层 ```json。不合格时抛 InvalidOutput，写明哪个字段不对。"""
    try:
        data = parse_json_markdown(text)
    except JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise InvalidOutput("输出不是一个 JSON 对象")
    try:
        return schema.model_validate(data)
    except ValidationError as error:
        problems = "；".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise InvalidOutput(f"JSON 字段不合格：{problems}") from None


def compact(text: str) -> str:
    """去掉全部空白。比较两段文字是不是同一句话时用，忽略换行和空格的差异。"""
    return "".join(text.split())
