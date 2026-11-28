"""岗位采集图。一次运行包含若干个搜索（平台 × 关键词 × 城市），一个一个做。

    collect ─▶ collect ─▶ …… ─▶ END
    （每次处理一个搜索，做完存一次断点）

每个搜索分两段：
1. 读列表。打开搜索页，留在这一个标签页里；候选岗位不够数，就用这个平台自己的方式翻页
   （点「下一页」或滚到底），读页面自己加载出来的下一页。
2. 读详情并保存。列表读完、标签页关掉之后，逐个岗位打开详情页读 JD。

先读完列表再读详情：列表的标签页开着的时候不开别的页面，访问是一个接一个的。

采集是静默的：标签页都开在后台，不把浏览器带到前面。只有平台要求登录或验证时，才把停下的
那个页面在专用浏览器里打开并带到前面，让用户自己处理。靠滚动加载的平台（BOSS 直聘、智联），
列表页开启焦点仿真，在后台也能滚出下一批（见 app/collectors/browser.py）。

几条规矩：
- 遇到登录、验证、限流，或者页面结构不认识，这个平台就停，同一平台后面的搜索也不再做；
  其他平台照常。不重试，不换别的办法去读。
- 已经有完整 JD 的岗位不再打开详情页，也不占这次的数量。所以重新发起同样的采集很便宜。
- 列表数据里自带职位描述的平台（前程无忧、智联），不打开详情页。
- 列表和详情之间按 app/collectors/pacing.py 的节奏等待；每个平台 24 小时内新增的岗位有上限。
- 用户给了筛选条件（薪资、经验、职位名里不要的词）时，不符合的岗位不读详情、不保存。
  有模型时还会把一页的职位名交给它，跳过和关键词明显无关的。这是采集里唯一用到模型的地方。
"""

import logging
import operator
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, TypedDict
from urllib.parse import urlsplit

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine
from sqlmodel import Session

from app.collectors.base import (
    MIN_JD_CHARS,
    Blocked,
    Browser,
    BrowserUnavailable,
    Listing,
    PageResult,
    Platform,
    Ready,
    UnknownStructure,
)
from app.collectors.filters import Filters
from app.collectors.pacing import DAILY_NEW_JOBS, Pacer
from app.collectors.signals import BLANKED, NEEDS_USER, block_reason
from app.db import jobs as job_store
from app.llm.relevance import irrelevant_titles

logger = logging.getLogger(__name__)

NODE_LABELS = {"collect": "采集岗位"}
MAX_PAGES = 5  # 一个搜索最多读几页列表（滚动加载的平台，一批算一页）
MAX_DETAIL_FAILURES = 3  # 连续这么多个详情读不出 JD，就当作平台改版，停下


BLOCK_SETTLE_SECONDS = 5.0  # 拦截页要稳定停留这么久，才算真的被拦


def settled(parsed: Ready, clock: Callable[[], float] = time.monotonic) -> Ready:
    """页面什么时候算加载完了：连续两次读到一样的内容，或者拦截页稳定地停留了一会儿。

    内容要连续两次一样：页面刚出现时脚本可能还在改它，读得太早会拿到没稳定的内容。

    不能一看到像登录或验证的地址就下结论。有些平台打开时会先经过一个「请稍候」的过渡页，
    浏览器自己会跳回来（BOSS 实测如此），过渡页的地址看起来就像登录页。
    等它停稳了还在拦，才是真的要用户处理。
    """
    since: float | None = None
    last = None

    def ready(page: PageResult) -> bool:
        nonlocal since, last
        if block_reason(page) is None:
            since = None
            snapshot = (page.url, page.data, len(page.captured)) if parsed(page) else None
            stable = snapshot is not None and snapshot == last
            last = snapshot
            return stable
        last = None
        if since is None:
            since = clock()
        return clock() - since >= BLOCK_SETTLE_SECONDS

    return ready


class Search(TypedDict):
    platform: str
    query: str
    city: str


class CollectionInput(TypedDict):
    searches: list[Search]
    filters: dict  # 筛选条件：min_salary、years、exclude、relevant_only


class CollectionState(CollectionInput, total=False):
    index: int  # 下一个要做的搜索
    blocked: dict[str, str]  # 已经停下的平台 → 原因
    results: Annotated[list[dict], operator.add]


@dataclass
class Tally:
    """一个搜索的计数。"""

    saved: list[int] = field(default_factory=list)  # 新增的、有完整 JD 的岗位编号
    existing: int = 0  # 已经有的，没有重复读取
    incomplete: int = 0  # 只读到列表信息
    pages: int = 0
    exhausted: bool = False  # 没有可以继续读的下一页了
    stalled: bool = False  # 翻了页，但没有读到新的岗位
    first_page_only: bool = False  # 后面还有，但这个平台只读得到第一页
    skipped: dict[str, int] = field(default_factory=dict)  # 被筛掉的：原因 → 个数
    unrelated: list[str] = field(default_factory=list)  # 被判为和关键词无关的职位名
    room: int | None = None  # 离每天的上限还剩多少个；比要的少时才记
    list_only: bool = False  # 只存列表：JD 不完整的岗位也算进数量

    @property
    def counted(self) -> int:
        """算进数量的岗位数。"""
        return len(self.saved) + (self.incomplete if self.list_only else 0)

    @property
    def empty(self) -> bool:
        return not (self.saved or self.existing or self.incomplete)

    def summary(self, target: int, *, finished: bool = True) -> str:
        """finished=False 表示中途停下了，这时不解释为什么没凑够数。"""
        parts = [f"新增 {len(self.saved)} 个岗位"]
        if self.existing:
            parts.append(f"{self.existing} 个已经有了，没有重复读取")
        if self.incomplete:
            parts.append(f"{self.incomplete} 个只读到列表信息，标为 JD 不完整，补上 JD 后才能评估")
        if self.skipped:
            reasons = "、".join(
                f"{SKIP_NAMES[reason]} {count} 个" for reason, count in self.skipped.items()
            )
            parts.append(f"按你的条件跳过 {sum(self.skipped.values())} 个（{reasons}）")
        if self.unrelated:  # 这一项是模型判断的，把职位名列出来，方便你看它判得对不对
            names = "、".join(self.unrelated[:UNRELATED_SHOWN])
            more = "等" if len(self.unrelated) > UNRELATED_SHOWN else ""
            parts.append(f"判为无关的职位名：{names}{more}")
        if finished and self.counted < target:
            if self.room is not None and self.counted >= self.room:
                parts.append(f"到了每个平台 24 小时最多新增 {DAILY_NEW_JOBS} 个的上限")
            elif self.exhausted:
                parts.append("列表已经读完")
            elif self.first_page_only:
                parts.append("这个平台只读第一页")
            elif self.stalled:
                parts.append(f"读了 {self.pages} 页列表，再翻页没有读到新的岗位")
            else:
                parts.append(f"读了 {self.pages} 页列表后停止")
        return "；".join(parts)


UNRELATED_SHOWN = 10  # 结果里最多列出这么多个被判为无关的职位名

SKIP_NAMES = {
    "salary": "薪资不够",
    "experience": "经验要求太高",
    "title": "职位名里有你排除的词",
    "relevance": "和关键词无关",
}


def search_label(platform: Platform, search: Search) -> str:
    return f"{platform.name}「{search['query']}」{search['city']}"


@dataclass
class CollectionNodes:
    engine: Engine
    browser: Browser
    pacer: Pacer
    platforms: dict[str, Platform]
    model: BaseChatModel | None = None  # 只用来判断岗位和关键词是否相关；没有就不筛

    async def collect(self, state: CollectionState) -> dict:
        index = state.get("index", 0)
        search = state["searches"][index]
        platform = self.platforms[search["platform"]]
        label = search_label(platform, search)
        blocked = dict(state.get("blocked", {}))
        tally = Tally()

        if platform.key in blocked:
            status, reason = "blocked", f"没有开始：{blocked[platform.key]}"
        else:
            try:
                filters = state.get("filters") or {}
                await self._collect_search(platform, search, search["count"], tally, label, filters)
                status, reason = "done", tally.summary(search["count"])
            except (Blocked, BrowserUnavailable) as error:
                message = str(error)
                if isinstance(error, Blocked) and error.show:
                    message += await self._ask_user(error.show)
                blocked[platform.key] = message
                status = "blocked"
                before = tally.summary(search["count"], finished=False)
                reason = message if tally.empty else f"{message}。停下之前：{before}"
        logger.info(
            "采集 %s 第 %s 个搜索：%s，新增 %s，已有 %s，JD 不完整 %s，列表 %s 页",
            platform.key,
            index + 1,
            status,
            len(tally.saved),
            tally.existing,
            tally.incomplete,
            tally.pages,
        )
        result = {
            "search": label,
            "job_id": None,
            "artifact_id": None,
            "status": status,
            "reason": reason,
            "saved": tally.saved,
        }
        return {"index": index + 1, "blocked": blocked, "results": [result]}

    async def _ask_user(self, url: str) -> str:
        """把停下的那个页面在专用浏览器里打开并带到前面，返回接在原因后面的一句话。"""
        tail = "再重新发起采集；已经保存的岗位不会重复读取"
        try:
            await self.browser.show(url)
        except BrowserUnavailable:
            return f"。请打开专用浏览器处理好，{tail}"
        return f"。已经在专用浏览器里打开了这个页面，请你处理好，{tail}"

    def _stop_if_blocked(self, platform: Platform, page: PageResult) -> None:
        """页面在拦人就抛 Blocked。"""
        reason = block_reason(page)
        if reason == BLANKED:
            raise Blocked(
                f"{platform.name}把页面清空了，没有继续。迁跃不重试，也不换别的办法去读。"
                "这个平台的岗位可以先手动添加"
            )
        if reason in NEEDS_USER:
            raise Blocked(f"{platform.name}{reason}", show=page.url)
        if reason:
            raise Blocked(f"{platform.name}{reason}，没有继续。请过一段时间再试")

    async def _collect_search(
        self,
        platform: Platform,
        search: Search,
        target: int,
        tally: Tally,
        label: str,
        filters: dict,
    ) -> None:
        progress = get_stream_writer()
        with Session(self.engine) as session:
            recent = job_store.count_collected_since(session, platform.key, hours=24)
        room = DAILY_NEW_JOBS - recent
        if room <= 0:
            raise Blocked(
                f"{platform.name}过去 24 小时里已经采了 {recent} 个岗位，"
                f"到了每个平台 24 小时最多新增 {DAILY_NEW_JOBS} 个的上限，没有继续。"
                "这是迁跃自己设的上限，为的是不让访问太多被平台限制"
            )
        if room < target:
            tally.room = target = room
        await self.pacer.before_search(platform.key)
        candidates, stopped = await self._read_list(platform, search, target, tally, label, filters)
        if stopped:
            # 列表翻到一半被拦：不再打开任何页面，只把列表里已经带着职位描述的岗位存好。
            candidates = [item for item in candidates if len(item.jd_text) >= MIN_JD_CHARS]

        # 只存列表：不打开详情页。列表没带职位描述的岗位存成「JD 不完整」，也算进数量里。
        list_only = bool(filters.get("list_only"))
        tally.list_only = list_only
        failures = 0
        seen = ""

        for listing in candidates:
            if tally.counted >= target:
                break
            jd_text = listing.jd_text if len(listing.jd_text) >= MIN_JD_CHARS else ""
            if not jd_text and not list_only:  # 列表里没带职位描述，打开详情页读
                await self.pacer.before_detail(platform.key)
                detail = await self.browser.open(
                    listing.url,
                    script=platform.detail_script,
                    ready=settled(platform.detail_ready),
                )
                self._stop_if_blocked(platform, detail)
                try:
                    jd_text = platform.parse_detail(detail)
                    failures = 0
                except UnknownStructure:
                    failures += 1
                    # 记下这个页面的样子，停下时告诉用户，方便判断是改版还是被要求登录。
                    path = urlsplit(detail.url).path[:60]
                    seen = f"标题是「{detail.title[:30]}」，地址是 {path}"
            with Session(self.engine) as session:
                job, outcome = job_store.save_collected(
                    session,
                    url=listing.url,
                    source=platform.key,
                    company=listing.company,
                    title=listing.title,
                    city=listing.city,
                    salary=listing.salary,
                    jd_text=jd_text,
                    company_size=listing.company_size,
                    industry=listing.industry,
                    posted_at=listing.posted_at,
                )
                job_id = job.id
            if outcome in ("new", "completed"):
                tally.saved.append(job_id)
            elif outcome == "incomplete":
                tally.incomplete += 1
            else:
                tally.existing += 1
            progress({"progress": f"{label}：已保存 {tally.counted}/{target} 个"})
            if failures >= MAX_DETAIL_FAILURES:
                raise Blocked(
                    f"{platform.name}连续 {failures} 个岗位的详情页读不出 JD，没有继续。"
                    f"最后一个页面的{seen}。可能是平台在要求登录，也可能是平台改版了"
                )
        if stopped:
            raise stopped

    async def _keep(self, fresh: list[Listing], query: str, filters: dict, tally: Tally) -> list:
        """按条件筛一页岗位，返回留下的。被筛掉的记进 tally.skipped，不读详情、不保存。"""
        rules = Filters.from_input(filters)
        kept = []
        for listing in fresh:
            reason = rules.reject_reason(listing)
            if reason:
                tally.skipped[reason] = tally.skipped.get(reason, 0) + 1
            else:
                kept.append(listing)
        if kept and self.model is not None and filters.get("relevant_only", True):
            unrelated = await irrelevant_titles(self.model, query, [item.title for item in kept])
            if unrelated:
                tally.skipped["relevance"] = tally.skipped.get("relevance", 0) + len(unrelated)
                tally.unrelated += [kept[index].title for index in sorted(unrelated)]
                kept = [item for index, item in enumerate(kept) if index not in unrelated]
        return kept

    async def _read_list(
        self,
        platform: Platform,
        search: Search,
        target: int,
        tally: Tally,
        label: str,
        filters: dict,
    ) -> tuple[list[Listing], Blocked | None]:
        """在一个标签页里读搜索结果，不够数就用这个平台自己的方式翻页。

        返回还没有完整 JD 的岗位，以及翻页途中遇到的拦截（第一页就被拦直接抛出）。
        """
        progress = get_stream_writer()
        seen: set[str] = set()
        candidates: list[Listing] = []
        url = platform.search_url(search["query"], platform.cities[search["city"]])
        focused = platform.scroll_paging and platform.next_page is not None
        async with self.browser.tab(platform.list_capture, focused=focused) as tab:
            progress({"progress": f"{label}：读取第 1 页列表"})
            page = await tab.goto(
                url, script=platform.list_script, ready=settled(platform.list_ready)
            )
            for page_number in range(1, MAX_PAGES + 1):
                try:
                    self._stop_if_blocked(platform, page)
                except Blocked as error:
                    if page_number == 1:
                        raise
                    return candidates, error
                try:
                    list_page = platform.parse_list(page)
                except UnknownStructure as error:
                    if page_number == 1:
                        raise Blocked(
                            f"{platform.name}的列表页和预期的结构不一样，没有继续（{error}）。"
                            "可能是平台改版了，也可能是它现在要求登录："
                            "可以打开专用浏览器登录这个平台后再试"
                        ) from None
                    tally.stalled = True  # 翻了页，但没有读到新的列表
                    break
                fresh = [item for item in list_page.listings if item.url not in seen]
                if page_number > 1 and not fresh:
                    tally.stalled = True
                    break
                tally.pages = page_number
                seen.update(item.url for item in fresh)
                for listing in await self._keep(fresh, search["query"], filters, tally):
                    with Session(self.engine) as session:
                        known = job_store.find_by_url(session, listing.url)
                    if known and (known.deleted_at or known.has_jd or filters.get("list_only")):
                        tally.existing += 1
                    else:
                        candidates.append(listing)

                if len(candidates) >= target:
                    break
                if not fresh or not list_page.has_more:
                    tally.exhausted = True
                    break
                if platform.next_page is None:
                    tally.first_page_only = True
                    break
                if page_number == MAX_PAGES:
                    break
                await self.pacer.before_page()
                progress({"progress": f"{label}：读取第 {page_number + 1} 页列表"})
                page = await tab.act(
                    platform.next_page,
                    script=platform.list_script,
                    ready=settled(has_new(platform, seen)),
                )
                if page is None:  # 页面上没有下一页了
                    tally.exhausted = True
                    break
        return candidates, None


def has_new(platform: Platform, seen: set[str]) -> Ready:
    """翻页之后，页面上有没有出现还没见过的岗位。"""

    def ready(page: PageResult) -> bool:
        try:
            listings = platform.parse_list(page).listings
        except UnknownStructure:
            return False
        except Blocked:
            return True
        return any(item.url not in seen for item in listings)

    return ready


def after_collect(state: CollectionState) -> str:
    return "collect" if state["index"] < len(state["searches"]) else END


def build_collection_graph(
    *,
    engine: Engine,
    browser: Browser,
    pacer: Pacer,
    platforms: dict[str, Platform],
    checkpointer: BaseCheckpointSaver,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    nodes = CollectionNodes(engine, browser, pacer, platforms, model)

    graph = StateGraph(CollectionState, input_schema=CollectionInput)
    graph.add_node("collect", nodes.collect)
    graph.add_edge(START, "collect")
    graph.add_conditional_edges("collect", after_collect, ["collect", END])
    return graph.compile(checkpointer=checkpointer)
