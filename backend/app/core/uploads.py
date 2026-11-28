"""用户上传、还没被读过的截图，存在 data/uploads/<编号>/ 下。

没有模型 Key 时，截图要交给用户自己的 Agent 去看，所以先存进迁跃：识别截图的运行和助手发的截图
都从这里取，Agent 通过 MCP 拿到图片本身，看不到时也可以按本机路径打开。存 7 天后删掉。
"""

import base64
import binascii
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

KEEP_SECONDS = 7 * 24 * 3600
_DATA_URL = re.compile(r"^data:image/([\w.+-]+);base64,(.+)$", re.DOTALL)
_ID = re.compile(r"^[0-9a-f]{12}$")
_SUFFIX = {"jpeg": "jpg", "svg+xml": "svg"}
_MIME = {"jpg": "jpeg", "svg": "svg+xml"}


class UploadMissing(Exception):
    """编号不对，或者截图已经过期删掉了。"""


def save(data_dir: Path, images: list[str]) -> str:
    """把 data:image/...;base64,... 形式的图片按顺序存下来，返回编号。图片格式由调用方先检查过。"""
    upload_id = uuid.uuid4().hex[:12]
    folder = data_dir / "uploads" / upload_id
    folder.mkdir(parents=True)
    for number, image in enumerate(images, start=1):
        match = _DATA_URL.match(image)
        if match is None:
            raise ValueError(f"第 {number} 张不是图片")
        subtype = match.group(1).lower()
        try:
            data = base64.b64decode(match.group(2), validate=True)
        except binascii.Error:
            raise ValueError(f"第 {number} 张图片的内容不完整") from None
        (folder / f"{number}.{_SUFFIX.get(subtype, subtype)}").write_bytes(data)
    return upload_id


def paths(data_dir: Path, upload_id: str) -> list[Path]:
    """一次上传的图片文件，按上传时的顺序。"""
    folder = data_dir / "uploads" / upload_id
    if not _ID.match(upload_id) or not folder.is_dir():
        raise UploadMissing(f"没有编号为 {upload_id} 的截图，可能已经过期（只保留 7 天）")
    return sorted(folder.iterdir(), key=lambda path: int(path.stem))


def data_urls(data_dir: Path, upload_id: str) -> list[str]:
    """读回成 data:image/...;base64,... 形式，交给模型。"""
    return [
        f"data:image/{mime(path)};base64,{base64.b64encode(path.read_bytes()).decode()}"
        for path in paths(data_dir, upload_id)
    ]


def mime(path: Path) -> str:
    """文件后缀对应的 image/ 后面那段。"""
    suffix = path.suffix.lstrip(".").lower()
    return _MIME.get(suffix, suffix)


def clean(data_dir: Path) -> None:
    """删掉存了 7 天以上的上传。启动时调用。"""
    root = data_dir / "uploads"
    if not root.is_dir():
        return
    cutoff = time.time() - KEEP_SECONDS
    for folder in root.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            logger.info("删掉过期的上传截图：%s", folder.name)
