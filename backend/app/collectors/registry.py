"""已经适配的平台。采集图、接口和工具都从这里查。"""

from app.collectors.base import Platform

PLATFORMS: dict[str, Platform] = {}


def register(platform: Platform) -> Platform:
    PLATFORMS[platform.key] = platform
    return platform


def load_platforms() -> None:
    """导入各平台的适配器，让它们登记进来。后端启动时调用一次。"""
    from app.collectors.platforms import boss, job51, liepin, zhaopin  # noqa: F401
