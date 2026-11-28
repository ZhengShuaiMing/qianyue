"""采集相关的两个小接口：有哪些平台、打开专用浏览器。发起采集用 POST /api/runs。"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import RunManagerDep, SettingsDep
from app.collectors.base import BrowserUnavailable
from app.collectors.browser import find_chrome
from app.collectors.registry import PLATFORMS

router = APIRouter(prefix="/collection", tags=["collection"])


class PlatformRead(BaseModel):
    key: str
    name: str
    reads_detail_pages: bool = Field(description="采集时要不要逐个打开岗位详情页")


class BrowserOpen(BaseModel):
    platform: str = Field(description="要打开哪个平台的首页：`51job` `liepin` `zhaopin` `boss`")


class BrowserOpened(BaseModel):
    message: str


@router.get("/platforms", response_model=list[PlatformRead])
def list_platforms():
    """可以采集的平台。"""
    return [
        PlatformRead(key=key, name=item.name, reads_detail_pages=item.detail_script is not None)
        for key, item in PLATFORMS.items()
    ]


class BrowserStatus(BaseModel):
    available: bool
    path: str | None = Field(description="找到的 Chrome 可执行文件；没找到时为空")
    message: str


@router.get("/browser/status", response_model=BrowserStatus)
def browser_status(settings: SettingsDep) -> BrowserStatus:
    """采集页启动时检查一次：本机有没有 Chrome。只检查文件，不启动浏览器。"""
    try:
        path = find_chrome(settings.chrome_path)
        return BrowserStatus(available=True, path=path, message="已找到本机的 Chrome")
    except BrowserUnavailable as error:
        return BrowserStatus(available=False, path=None, message=str(error))


@router.post("/browser", response_model=BrowserOpened)
async def open_browser(body: BrowserOpen, manager: RunManagerDep):
    """在专用浏览器里打开一个平台的首页，让你自己登录或完成验证。

    专用浏览器是一个单独的 Chrome 窗口，用户目录在 data/browser/，和你日常用的 Chrome 互不影响。
    登录状态留在这个目录里，下次采集会沿用。迁跃不处理验证码，也不读取或导出 Cookie。
    """
    if body.platform not in PLATFORMS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"不支持的平台：{body.platform}。可选：{'、'.join(PLATFORMS)}",
        )
    platform = PLATFORMS[body.platform]
    try:
        await manager.browser.show(platform.home_url)
    except BrowserUnavailable as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None
    return BrowserOpened(
        message=f"已在专用浏览器里打开{platform.name}。登录或完成验证后，重新发起采集即可"
    )
