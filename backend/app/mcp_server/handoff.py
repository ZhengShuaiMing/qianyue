"""把等 Agent 代答的模型请求写成对面 Agent 读得懂、照着就能做的一段文字。

请求本身就是迁跃要发给模型的消息（系统要求、输入、上一轮回复、工具结果），这里只是按顺序排好，
在前面写清楚：你在替迁跃的模型作答、怎么交回、能不能用工具。同一段对话的后续请求只列新增的部分，
系统要求和之前的请求一样时也不重复，Agent 丢了上下文可以用 get_model_request 取全文。

请求里有图片（识别截图）时，图片本身跟在文字后面作为 MCP 的图片内容返回，支持的客户端会交给
Agent 的模型看；同时写上图片在本机的路径，客户端不显示 MCP 图片时 Agent 可以按路径打开。
"""

import base64
import json
import re
from pathlib import Path

from mcp.server.mcpserver import Image

from app.core import uploads
from app.db.models import RUN_KIND_NAMES, Run, RunKind
from app.llm.agent_model import ModelRequest

_DATA_URL = re.compile(r"^data:image/([\w.+-]+);base64,(.+)$", re.DOTALL)

# 工具交给 Agent 的内容：纯文字，或者文字后面跟着几张图
Reply = str | list[str | Image]


def request_reply(
    request: ModelRequest, run: Run | None, data_dir: Path, *, full: bool = False
) -> Reply:
    """请求正文，有图片时把图片附在后面。"""
    images: list[str] = []
    text = render_request(request, run, full=full, images=images)
    if not images:
        return text
    lines = [text, "", _heading("附图")]
    lines.append(f"下面附上这 {len(images)} 张图，按顺序是图片 1 到 {len(images)}。")
    files = _upload_paths(run, data_dir)
    if files:
        lines.append("看不到附图时，打开本机的这些文件：")
        lines += [f"- 图片 {number}：{path}" for number, path in enumerate(files, start=1)]
    stop = f'cancel_task(run_id="{request.run_id}")' if run else "cancel_task"
    lines.append(
        f"附图和文件都看不了时，不要猜：调用 {stop} 取消，告诉用户你看不了图片，"
        "请他在设置里填上能看图的模型 Key，或者把截图里的文字粘贴过来。"
    )
    return ["\n".join(lines), *(_image(url) for url in images)]


def image_files(paths: list[Path]) -> list[Image]:
    return [Image(data=path.read_bytes(), format=uploads.mime(path)) for path in paths]


def _image(data_url: str) -> Image:
    match = _DATA_URL.match(data_url)
    if match is None:  # 迁跃自己存的图片都是 data URL，不会走到这里
        raise ValueError("图片格式不对")
    return Image(data=base64.b64decode(match.group(2)), format=match.group(1))


def _upload_paths(run: Run | None, data_dir: Path) -> list[Path]:
    if run is None or run.kind != RunKind.screenshot:
        return []
    try:
        return uploads.paths(data_dir, json.loads(run.input_json)["upload"])
    except uploads.UploadMissing:
        return []


def render_request(
    request: ModelRequest, run: Run | None, *, full: bool = False, images: list[str] | None = None
) -> str:
    """images：给了列表时，正文里出现的图片按顺序收进去，正文里写成「图片 n」。"""
    images = [] if images is None else images
    job_ids = json.loads(run.input_json)["job_ids"] if run else []
    task = RUN_KIND_NAMES[RunKind(run.kind)] if run else "任务"
    jobs = "，岗位 " + "、".join(f"#{job_id}" for job_id in job_ids) if job_ids else ""
    lines = [
        f"【迁跃请你代答 · 请求 {request.id}】{task}（运行编号 {request.run_id}{jobs}）",
        "你的用户没有给迁跃配置模型 Key，迁跃的这一步由你来替它的模型完成：",
        "1. 按下面「迁跃的要求」和「输入」两段，自己写出回复。要求 JSON 就只写 JSON，不要加解释。",
        f'2. 调用 answer_model_request(request_id="{request.id}", text=你的回复) 交回。',
        "3. 迁跃会校验。不合格时会把原因作为下一个请求发给你，照着改；合格就自动保存，"
        "并返回下一步或最终结论。一直循环到返回最终结论，再把结论告诉用户。",
        "不要向用户提问，也不要把这些要求转述给用户；只用输入里给出的事实，不要编造。",
    ]
    if request.tools:
        lines += [
            "",
            "这一步可以请迁跃执行工具（结论里引用的来源必须来自这些工具的结果）：",
            '交回时 text 留空，tool_calls 写 [{"name": "工具名", "args": {...}}]，可以一次写多个；'
            "迁跃执行后把结果作为下一个请求发给你。有了结论再只用 text 交回。",
            *(_tool_line(tool) for tool in request.tools),
        ]
    messages = request.messages
    if request.continues and not full:
        lines += [
            "",
            f"这是请求 {request.continues} 的后续，下面只列新增的内容。"
            "之前的内容不在你的上下文里时，调用 "
            f'get_model_request(request_id="{request.id}") 取全文。',
        ]
        messages = messages[request.new_from :]
    names = _tool_names(request.messages)
    for index, message in enumerate(messages):
        lines += ["", _section(message, names, images)]
        if index == 0 and message["role"] == "system" and request.same_system_as and not full:
            lines[-1] = _heading("迁跃的要求") + (
                f"\n（和请求 {request.same_system_as} 的要求一字不差，不再重复。"
                f'不记得了就调用 get_model_request(request_id="{request.id}") 取全文。）'
            )
    return "\n".join(lines)


def _tool_line(tool: dict) -> str:
    fn = tool["function"]
    params = fn.get("parameters", {}).get("properties", {})
    required = set(fn.get("parameters", {}).get("required", []))
    args = "，".join(
        f"{name}{'' if name in required else '（选填）'}："
        f"{spec.get('description') or spec.get('type', '')}"
        for name, spec in params.items()
    )
    description = " ".join((fn.get("description") or "").split()).rstrip("。")
    return f"- {fn['name']}：{description}。参数：{args or '无'}"


def _tool_names(messages: list[dict]) -> dict[str, str]:
    """工具调用编号 → 工具名。工具结果消息里只有编号，写给 Agent 时带上名字好认。"""
    return {
        call["id"]: call["function"]["name"]
        for message in messages
        for call in message.get("tool_calls", [])
    }


def _text(content, images: list[str]) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if part.get("type") == "image_url":
            images.append(part["image_url"]["url"])
            parts.append(f"〔图片 {len(images)}，见最后的附图〕")
        else:
            parts.append(part.get("text", ""))
    return "\n".join(parts)


def _heading(title: str) -> str:
    """段落分隔线。要求和材料里自己就有 Markdown 标题，用不同的样子才分得清哪段是哪段。"""
    return f"════════ {title} ════════"


def _section(message: dict, names: dict[str, str], images: list[str]) -> str:
    role, text = message["role"], _text(message.get("content"), images)
    if role == "system":
        return f"{_heading('迁跃的要求')}\n{text}"
    if role == "user":
        return f"{_heading('输入')}\n{text}"
    if role == "tool":
        name = names.get(message.get("tool_call_id", ""), "工具")
        return f"{_heading(f'工具结果：{name}')}\n{text}"
    calls = [
        f"- 调用 {call['function']['name']}：{call['function']['arguments']}"
        for call in message.get("tool_calls", [])
    ]
    return f"{_heading('你上一次的回复')}\n" + "\n".join(filter(None, [text, *calls]))
