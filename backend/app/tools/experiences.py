"""联网找面经：这家公司的面试里，别人被问过什么。给面试准备用。

固定的两次搜索加几个网页，不让模型自己决定查什么：要找的东西很明确，这样也省 Token。
搜不到、读不到都不算失败，面试准备照常按 JD 和材料出题。

2026-11-16 实测后定的做法：
- 搜索词用「公司 + 岗位的核心词 + 面经」。带上完整职位名或不带岗位，搜到的多是通用的面试指南。
- 只留下提到这家公司的结果，同时提到岗位核心词的排在前面。
- 很多面经网站（牛客、知乎、CSDN）不让程序读正文，所以多试几个，读不到的用搜索摘要。
- 面经网页很长，开头多是导航。取问号最密集的那一段：面试题集中在那里。
"""

import asyncio
import logging
import re
from collections.abc import Callable

from app.llm.parsing import compact
from app.tools.web import WebClient, WebError

logger = logging.getLogger(__name__)

MAX_SOURCES = 4  # 最多给模型几篇
MAX_PAGES = 3  # 读到这么多篇正文就够了
MAX_FETCHES = 6  # 最多试着打开几个网页
SOURCE_CHARS = 2200  # 一篇最多给模型多少字
WINDOW_STEP = 400
HITS_PER_SEARCH = 6

_BRACKETS = re.compile(r"[（(][^）)]*[）)]")
_SUFFIX = re.compile(r"(?:股份有限公司|有限责任公司|有限公司|集团|公司)$")
_CITY = re.compile(r"^(?:北京|上海|深圳|广州|杭州|成都|南京|武汉|西安|苏州|天津|重庆)市?")
_GENERIC = re.compile(r"(?:信息技术|网络技术|科技|网络|软件|技术|信息|数据|智能|电子商务)$")


def company_key(company: str) -> str:
    """公司名里最能认出这家公司的那几个字：去掉括号、城市、「有限公司」「科技」这些通用的词。

    「腾讯科技（深圳）有限公司」→「腾讯」，「北京字节跳动科技有限公司」→「字节跳动」。
    用来判断一条搜索结果讲的是不是这家公司。
    """
    name = _CITY.sub("", _SUFFIX.sub("", _BRACKETS.sub("", company.strip())))
    while len(_GENERIC.sub("", name)) >= 2 and _GENERIC.search(name):
        name = _GENERIC.sub("", name)
    return (name[:4] if len(name) >= 2 else company.strip()).lower()


_LEVEL = re.compile(r"^(?:高级|资深|初级|中级|实习|校招|社招)")
_ROLE_SUFFIX = re.compile(r"(?:开发工程师|研发工程师|工程师|开发|研发|专家|实习生|岗)$")


def role_keyword(title: str) -> str:
    """职位名里的主干：去掉括号里的补充说明，太长就截短。"""
    return _BRACKETS.sub("", title).strip()[:12]


def role_core(title: str) -> str:
    """职位的核心词：「高级前端开发工程师」→「前端」，「Agent 工程师」→「Agent」。"""
    name = role_keyword(title)
    core = _ROLE_SUFFIX.sub("", _LEVEL.sub("", name)).strip()
    return core if len(core) >= 2 else name


def densest_part(text: str, size: int = SOURCE_CHARS) -> str:
    """文字里问号最密集的那一段。面经网页的开头多是导航，面试题集中在中间某一段。"""
    if len(text) <= size:
        return text
    best_start, best_count = 0, -1
    for start in range(0, len(text) - size + WINDOW_STEP, WINDOW_STEP):
        window = text[start : start + size]
        count = window.count("？") + window.count("?")
        if count > best_count:
            best_start, best_count = start, count
    return text[best_start : best_start + size]


async def find_experiences(
    web: WebClient, company: str, title: str, on_use: Callable[[str, str], None]
) -> list[dict]:
    """返回提到这家公司的面经：{id, url, title, text, opened}。一篇都没有时是空列表。

    on_use(工具, 说明) 在每次搜索、每次打开网页前调用，用来给看进度的人报告。"""
    key = compact(company_key(company))
    core = role_core(title)
    queries = [f"{company} {core} 面经", f"{company} {core} 面试题 面经"]
    hits: dict[str, object] = {}
    for query in queries:
        on_use("web_search", query)
        try:
            found = await asyncio.to_thread(web.search, query, HITS_PER_SEARCH)
        except WebError:
            continue
        for hit in found:
            if key in compact(hit.title + hit.snippet).lower():
                hits.setdefault(hit.url, hit)
    # 同时提到岗位核心词的排在前面。
    ranked = sorted(
        hits.values(), key=lambda hit: core.lower() not in (hit.title + hit.snippet).lower()
    )

    collected = []
    fetches = 0
    for hit in ranked:
        text, opened = " ".join(hit.snippet.split()), False
        if fetches < MAX_FETCHES and sum(item["opened"] for item in collected) < MAX_PAGES:
            fetches += 1
            on_use("read_page", hit.url)
            try:
                page = await asyncio.to_thread(web.fetch, hit.url)
            except WebError:
                page = None
            if page is not None and key in compact(page.text).lower():
                text, opened = densest_part(page.text), True
        if len(text) >= 20:
            collected.append(
                {"url": hit.url, "title": hit.title[:60], "text": text, "opened": opened}
            )
    # 读到正文的优先。
    collected.sort(key=lambda item: not item["opened"])
    sources = [
        {"id": f"W{number}", **item} for number, item in enumerate(collected[:MAX_SOURCES], 1)
    ]
    logger.info(
        "查面经：搜到提到这家公司的结果 %s 条，试着打开 %s 个网页，用了 %s 篇，其中读到正文 %s 篇",
        len(hits),
        fetches,
        len(sources),
        sum(source["opened"] for source in sources),
    )
    return sources
