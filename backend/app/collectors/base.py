"""采集的基本约定：浏览器给出什么、平台适配器要做什么。

采集分三层，互不知道对方的细节：
- 浏览器（Browser）：打开一个地址，把页面最终的样子交出来；也可以留着一个标签页连续操作。
- 平台适配器（Platform）：知道某个平台的搜索地址怎么拼、怎么翻页、列表和详情怎么解析。
  都是纯函数和几段在页面里执行的读取脚本。
- 采集图（app/graphs/collection.py）：决定读哪些页、等多久、什么时候停、怎么入库。
"""

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

MIN_JD_CHARS = 20  # 比这还短的文字不当作职位描述


@dataclass(frozen=True)
class Captured:
    """页面自己发出的一次接口请求的响应。只读取，不重放。"""

    url: str
    status: int
    body: str


@dataclass
class PageResult:
    """一个页面加载之后的样子。"""

    url: str  # 最终地址，可能已经被跳转到登录或验证页
    status: int | None = None  # 页面本身的 HTTP 状态码
    title: str = ""
    text: str = ""  # 页面上能看到的文字
    captured: list[Captured] = field(default_factory=list)
    data: Any = None  # 在页面里执行脚本取到的数据


@dataclass(frozen=True)
class Listing:
    """列表页上的一个岗位。"""

    url: str
    title: str
    company: str
    city: str = ""
    salary: str = ""
    jd_text: str = ""  # 有些平台的列表数据里就带着完整的职位描述，这时不用再打开详情页
    experience: str = ""  # 平台写的经验要求，例如「3-5年」「经验不限」。只用来筛选，不入库
    company_size: str = ""  # 公司规模，例如「100-499人」
    industry: str = ""  # 公司所在行业
    posted_at: str = ""  # 平台上显示的发布（猎聘是刷新）日期，YYYY-MM-DD；没有就是空


_DATE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


def date_of(text) -> str:
    """平台给的时间（「2026-09-10 15:12:02」「20261016174917」）只留日期。认不出就是空。"""
    found = _DATE.match(str(text or "").strip())
    return "-".join(found.groups()) if found else ""


@dataclass
class ListPage:
    listings: list[Listing]
    has_more: bool  # 后面还有没有下一页


class UnknownStructure(Exception):
    """页面或接口的结构和适配器认识的不一样。不猜，交给上层决定停不停。"""


class Blocked(Exception):
    """这个平台现在不能继续读：要登录、要验证、被限流，或者结构变了。信息直接给用户看。

    show：需要用户自己去处理（登录、完成验证）时，给出停下的那个页面的地址。
    采集会把它在专用浏览器里打开并带到前面。
    """

    def __init__(self, message: str, *, show: str | None = None) -> None:
        super().__init__(message)
        self.show = show


class BrowserUnavailable(Exception):
    """专用浏览器起不来或连不上。信息直接给用户看。"""


Ready = Callable[[PageResult], bool]


class Tab(Protocol):
    """一个保持打开的标签页。一个搜索从头到尾用同一个，翻页在页面里做。"""

    async def goto(
        self, url: str, *, script: str | None = None, ready: Ready | None = None
    ) -> PageResult:
        """打开一个地址，返回页面的样子。script 和 ready 的含义见 Browser.open。"""
        ...

    async def act(
        self, action: str, *, script: str | None = None, ready: Ready | None = None
    ) -> PageResult | None:
        """在页面里执行一个动作（点「下一页」、滚到底），再返回页面的样子。

        action 是一个函数，返回 false 表示页面上没有可做的动作（没有下一页），这时返回 None。
        返回的 captured 只包含这个动作之后页面发出的接口响应。
        """
        ...


class Browser(Protocol):
    def tab(
        self, capture: str | None = None, *, focused: bool = False
    ) -> AbstractAsyncContextManager[Tab]:
        """新开一个标签页并留着，用完断开并关掉。capture 的含义见 open。

        focused：让这个开在后台的标签页像在前台一样工作（焦点仿真）。只给靠滚动加载下一批的
        列表页用：后台的页面不响应滚动。
        """
        ...

    async def open(
        self,
        url: str,
        *,
        capture: str | None = None,
        script: str | None = None,
        ready: Ready | None = None,
    ) -> PageResult:
        """新开一个标签页打开这个地址，返回页面的样子，然后断开并关掉这个标签页。

        capture：地址里含这段文字的接口响应会被记下来。
        script：页面加载后在页面里执行的脚本，返回值放进 data。
        ready：页面还在加载时会反复调用它，返回 True 就不再等。
        """
        ...

    async def show(self, url: str) -> None:
        """在专用浏览器里打开一个地址并留着，给用户自己登录或完成验证用。"""
        ...

    async def close(self) -> None: ...


class Platform:
    """一个招聘平台的适配器。子类只描述这个平台的地址和页面结构，不做任何等待和联网。"""

    key: str
    name: str
    home_url: str
    cities: dict[str, str]  # 城市名 → 这个平台的城市码
    list_capture: str | None = None
    list_script: str | None = None
    detail_script: str | None = None

    # 在搜索页里翻到下一页的动作：这个平台自己的翻页方式（点「下一页」或滚到底）。
    # 是一个在页面里执行的函数，返回 false 表示没有下一页。None 表示只读第一页。
    next_page: str | None = None
    # 翻页是不是靠滚动。是的话，列表的标签页要开启焦点仿真，否则在后台滚了也不加载。
    scroll_paging: bool = False

    def search_url(self, query: str, city_code: str) -> str:
        """搜索结果第一页的地址。后面的页不靠改地址，靠 next_page。"""
        raise NotImplementedError

    def parse_list(self, page: PageResult) -> ListPage:
        """从列表页取出岗位。结构不认识时抛 UnknownStructure。"""
        raise NotImplementedError

    def parse_detail(self, page: PageResult) -> str:
        """从详情页取出 JD 正文。读不出来时抛 UnknownStructure。"""
        raise NotImplementedError

    def list_ready(self, page: PageResult) -> bool:
        return self._conclusive(self.parse_list, page)

    def detail_ready(self, page: PageResult) -> bool:
        return self._conclusive(self.parse_detail, page)

    @staticmethod
    def _conclusive(parse, page: PageResult) -> bool:
        """页面是不是已经能下结论了：读到了内容，或者明确被拦。结构还不认识就再等等。"""
        try:
            parse(page)
        except UnknownStructure:
            return False
        except Blocked:
            return True
        return True
