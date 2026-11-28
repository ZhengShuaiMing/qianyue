"""截图录入：添加岗位、添加沟通时给的截图，保存时交给模型抄成要存的文字。

岗位和沟通记录存的是文字：评估、检索、沟通建议都读文字。这里只负责读，不写任何东西。
张数和大小不设限制，模型收不了时把它的原话告诉用户。
"""

import logging
import re
from typing import Literal

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.config import BACKEND_DIR
from app.llm.parsing import InvalidOutput, parse_json_as

logger = logging.getLogger(__name__)
_IMAGE = re.compile(r"^data:image/[\w.+-]+;base64,[A-Za-z0-9+/]+=*$")
RETRY = "上一次的输出不合格：{problem}\n\n请重新输出完整的 JSON 对象，修正上面的问题。只输出 JSON。"

SYSTEM_PROMPT = (BACKEND_DIR / "prompts" / "screenshot.md").read_text(encoding="utf-8")

JOB_TASK = """这是一个招聘岗位页面的截图。填这几项：

{"company": "公司名", "title": "职位名", "city": "工作城市", "salary": "薪资，按图里的写法",
 "jd_text": "岗位描述全文"}

`jd_text` 是职位描述、任职要求、福利、公司介绍等，按图里的分段和顺序原样抄，用换行分开。"""

CHAT_TASK = """这是和招聘方聊天的截图。按时间顺序把每条消息抄出来：

{"messages": [{"speaker": "recruiter", "text": "招聘方说的一条"},
              {"speaker": "me", "text": "我说的一条"}]}

`speaker` 只能是 `recruiter`（招聘方）或 `me`（用户自己）。聊天软件里用户自己发的消息一般在右边，
招聘方的在左边。时间、已读、系统提示（例如「对方已查看你的简历」）不算消息，不要抄。"""


class JobFromImage(BaseModel):
    company: str = ""
    title: str = ""
    city: str = ""
    salary: str = ""
    jd_text: str = ""


class ChatLine(BaseModel):
    speaker: Literal["recruiter", "me"]
    text: str


class ChatFromImage(BaseModel):
    messages: list[ChatLine]


TASKS = {
    "job": (JOB_TASK, JobFromImage),
    "communication": (CHAT_TASK, ChatFromImage),
}


class ImagesRejected(Exception):
    """模型不收图片（多半是只支持文字的模型），或者模型服务没有回应。"""

    def __init__(self, message: str, *, unsupported: bool) -> None:
        super().__init__(message)
        self.unsupported = unsupported


def images_in(message) -> list[str]:
    """一条消息里带的图片（data:image/...;base64,...），按顺序。纯文字的消息是空列表。"""
    if isinstance(message.content, str):
        return []
    return [
        part["image_url"]["url"]
        for part in message.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


def check_images(images: list[str]) -> list[str]:
    """只看是不是 data:image/...;base64,... 形式的图片。不合格抛 ValueError。"""
    for number, image in enumerate(images, start=1):
        if not _IMAGE.match(image):
            raise ValueError(f"第 {number} 张不是图片")
    return images


def short_error(error: Exception) -> str:
    """模型服务返回的错误，取前 200 个字给用户看。"""
    return (getattr(error, "message", None) or str(error))[:200]


def images_rejected(error: Exception) -> ImagesRejected:
    """模型调用出错时，说清楚是模型收不了图，还是模型服务没有回应。"""
    if isinstance(error, openai.BadRequestError):
        return ImagesRejected(
            f"模型没有收这几张图（{short_error(error)}）。现在用的模型可能不能看图，"
            "或者图片太大、太多；可以换一个支持图片输入的模型，或者少发几张",
            unsupported=True,
        )
    return ImagesRejected(f"模型没有回应：{short_error(error)}", unsupported=False)


async def read_screenshots(model: BaseChatModel, task: str, images: list[str]) -> BaseModel:
    """让模型读截图。输出不合格时退回重写一次，还不合格抛 InvalidOutput。"""
    messages = build_messages(task, images)
    for attempt in (1, 2):
        try:
            reply = await model.ainvoke(messages)
        except (openai.APIStatusError, openai.APIConnectionError) as error:
            raise images_rejected(error) from None
        usage = reply.usage_metadata or {}
        logger.info(
            "读截图（%s，%s 张）：第 %s 次 input_tokens=%s output_tokens=%s",
            task,
            len(images),
            attempt,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        try:
            return parse_reply(task, str(reply.text))
        except InvalidOutput as error:
            logger.warning("读截图的输出不合格（第 %s 次）：%s", attempt, error)
            messages = [
                *messages,
                AIMessage(str(reply.text)),
                HumanMessage(RETRY.format(problem=error)),
            ]
    raise InvalidOutput("模型连续两次没有按格式回答，请再试一次")


def build_messages(task: str, images: list[str]) -> list:
    """images：data:image/...;base64,... 形式的图片，按用户给的顺序。"""
    instruction = TASKS[task][0]
    content: list[dict] = [{"type": "text", "text": f"一共 {len(images)} 张图。{instruction}"}]
    content += [{"type": "image_url", "image_url": {"url": image}} for image in images]
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(content=content)]


def parse_reply(task: str, text: str) -> BaseModel:
    return parse_json_as(text, TASKS[task][1])


SPEAKER_PREFIX = {"recruiter": "招聘方", "me": "我"}


def chat_as_record(chat: ChatFromImage, typed: str = "", speaker: str = "transcript"):
    """聊天截图（加上用户在截图下面打的字）存成哪种沟通记录。

    只有一条消息就按谁说的存，几条就存成一整段聊天。打的字接在截图后面：
    选了「招聘方说的」「我说的」就按那个人说的一条算；选了「一整段聊天」就原样接上。
    返回 (speaker, text)。
    """
    lines = [(line.speaker, line.text.strip()) for line in chat.messages if line.text.strip()]
    typed = typed.strip()
    tail = ""
    if typed and speaker in SPEAKER_PREFIX:
        lines.append((speaker, typed))
    elif typed:
        tail = typed
    if len(lines) == 1 and not tail:
        return lines[0]
    text = "\n".join(f"{SPEAKER_PREFIX[who]}：{said}" for who, said in lines)
    return "transcript", "\n".join(part for part in (text, tail) if part)
