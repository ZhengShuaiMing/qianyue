"""公司研究 Agent 的工具：联网搜索、读取网页。都是只读的。

Agent 每多想一步，之前所有的搜索结果和网页都会重新发给模型一遍，所以只保留简短的结果：
- 搜索结果只给来源编号、标题、站点和一段不长的摘要，不给完整链接。链接里的中文是转码过的，
  很费 Token；读网页时用编号就够了。
- 每次搜索给 4 条，一个网页给 2000 字，一次研究最多搜 6 次、读 4 个网页。

一条结果都还没搜到、搜索就已经失败两次时（断网、Key 失效、额度用完），整个研究直接停下：
再让模型写下去只会得到一份「信息不足」的报告，白花一次模型调用。
"""

import asyncio
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, tool

from app.tools.sources import SourceLog
from app.tools.web import WebClient, WebError

PAGE_CHARS = 2000  # 一个网页最多给模型多少字
SNIPPET_CHARS = 180  # 一条搜索结果的摘要最多给多少字
HITS_PER_SEARCH = 4
_SOURCE_ID = re.compile(r"^\[?[Ss]\d+\]?$")
FAILURES_BEFORE_STOP = 2


class SearchUnavailable(Exception):
    """搜索一直失败，什么都没搜到。信息是给用户看的原因。"""


def build_research_tools(
    web: WebClient, log: SourceLog, on_use: Callable[[str, str], None]
) -> list[BaseTool]:
    """为一次运行创建工具。log 记录这次运行见过的全部来源，on_use 用来报告进度。"""
    failures = 0

    @tool
    async def web_search(query: str) -> str:
        """联网搜索。输入搜索词，返回若干条结果，每条有来源编号、标题、链接和摘要。
        搜索词用公司全称加一个具体方面，例如「某某科技有限公司 融资」。"""
        if log.searches >= log.max_searches:
            return f"已经搜索了 {log.max_searches} 次，达到上限。请基于已有信息给出结论。"
        log.searches += 1
        on_use("web_search", query)
        nonlocal failures
        try:
            hits = await asyncio.to_thread(web.search, query, HITS_PER_SEARCH)
        except WebError as error:
            failures += 1
            if failures >= FAILURES_BEFORE_STOP and not log.dump():
                raise SearchUnavailable(str(error)) from None
            return f"搜索失败：{error}。可以换一个搜索词再试。"
        return "\n\n".join(
            f"[{source.id}] {source.title}｜{urlsplit(source.url).hostname or ''}\n"
            + " ".join(source.snippet.split())[:SNIPPET_CHARS]
            for source in log.add_hits(hits[:HITS_PER_SEARCH])
        )

    @tool
    async def read_page(source: str) -> str:
        """读取一个网页的正文，核实摘要里的说法。source 填搜索结果里的来源编号，例如 S3；
        也可以填一个完整的链接。有些网站需要登录或拒绝程序访问，会读取失败。"""
        url = source.strip()
        if _SOURCE_ID.match(url):
            known = log.find(url)
            if known is None:
                return f"没有编号为 {url} 的来源。请用搜索结果里出现过的编号。"
            url = known.url
        if log.pages >= log.max_pages:
            return f"已经读取了 {log.max_pages} 个网页，达到上限。请基于已有信息给出结论。"
        log.pages += 1
        on_use("read_page", url)
        try:
            page = await asyncio.to_thread(web.fetch, url)
        except WebError as error:
            return f"读取失败：{error}。这个来源只能按搜索摘要使用。"
        opened = log.add_page(url, page)
        text = page.text[:PAGE_CHARS]
        suffix = "\n（正文过长，后面的部分已省略）" if len(page.text) > PAGE_CHARS else ""
        return f"[{opened.id}] {page.title}\n{text}{suffix}"

    return [web_search, read_page]
