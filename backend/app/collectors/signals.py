"""停止信号：页面是不是在要求登录或验证，或者访问被限制了。

出现任何一种就停，不重试、不换来源、不处理验证码。判断只看三样：页面的状态码、
最终地址的路径、页面开头的明确提示。JD 正文里出现「验证」「登录」这些词不算。
"""

import re
from urllib.parse import urlsplit

from app.collectors.base import PageResult

_VERIFY_PATH = re.compile(
    r"(?:^|/)(?:[^/]*security[-_]?check[^/]*|security(?:\.html?)?(?:/|$)"
    r"|(?:captcha|verify[a-z]*|verification|antispam)(?:[/.-]|$)|intercept(?:/|$))",
    re.I,
)
_LOGIN_PATH = re.compile(r"(?:^|/)(?:login|signin|passport)(?:[/.]|$)", re.I)
_VERIFY_TEXT = re.compile(r"请(?:完成|通过|进行)(?:以下)?(?:安全|人机|滑块|滑动)?验证")
_VERIFY_LINES = {
    "安全验证",
    "验证码",
    "人机验证",
    "滑块验证",
    "滑动验证",
    "访问异常",
    "环境异常",
    "网络环境异常",
    "访问过于频繁",
    "操作频繁",
}
_LOGIN_TEXT = re.compile(r"请先登录|登录后查看|登录后可查看")
LOGIN = "需要登录"
VERIFY = "需要完成安全验证"
NEEDS_USER = {LOGIN, VERIFY}  # 这两种要用户自己在专用浏览器里处理
# 页面被清空。有的平台发现页面被自动化工具读取，会把它换成空白页（BOSS 直聘实测如此）。
BLANKED = "把页面清空了"
HEAD_CHARS = 600  # 只看页面开头这么多字。验证页和登录墙的提示都在最前面


def block_reason(page: PageResult) -> str | None:
    """页面在拦人就返回原因（给用户看的话），否则返回 None。"""
    if page.url == "about:blank":
        return BLANKED
    if page.status == 401:
        return LOGIN
    if page.status in (403, 429):
        return f"访问被限制（HTTP {page.status}）"

    parts = urlsplit(page.url)
    if _VERIFY_PATH.search(parts.path):
        return VERIFY
    if _LOGIN_PATH.search(parts.path) or parts.hostname and parts.hostname.startswith("passport."):
        return LOGIN

    head = page.text[:HEAD_CHARS]
    lines = {line.strip().rstrip("！!。.") for line in head.splitlines()}
    if _VERIFY_TEXT.search(head) or lines & _VERIFY_LINES:
        return VERIFY
    if _LOGIN_TEXT.search(head):
        return LOGIN
    return None
