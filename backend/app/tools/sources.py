"""一次运行里的来源登记表。

搜索结果和读过的网页都在这里登记并编号（S1、S2……）。模型在结论里只引用编号，
程序用这张表把编号还原成链接，并且知道哪些来源真的打开过原文、哪些只看了搜索摘要。
"""

from dataclasses import asdict, dataclass

from app.tools.web import Page, SearchHit

TITLE_CHARS = 60  # 有些搜索结果的标题是好几个标题拼在一起的，太长就截断


@dataclass
class Source:
    id: str
    url: str
    title: str
    snippet: str
    opened: bool = False  # 是否成功读过原文


class SourceLog:
    def __init__(self, max_searches: int = 6, max_pages: int = 4) -> None:
        self.max_searches = max_searches
        self.max_pages = max_pages
        self.searches = 0
        self.pages = 0
        self._by_url: dict[str, Source] = {}

    def _register(self, url: str, title: str, snippet: str = "") -> Source:
        source = self._by_url.get(url)
        if source is None:
            title = title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS] + "…"
            source = Source(id=f"S{len(self._by_url) + 1}", url=url, title=title, snippet=snippet)
            self._by_url[url] = source
        return source

    def add_hits(self, hits: list[SearchHit]) -> list[Source]:
        """登记一批搜索结果。同一个链接再次出现时沿用原来的编号。"""
        return [self._register(hit.url, hit.title, hit.snippet) for hit in hits]

    def add_page(self, requested_url: str, page: Page) -> Source:
        """登记一个读过原文的网页。"""
        source = self._by_url.get(requested_url) or self._register(page.url, page.title)
        source.opened = True
        source.title = source.title or page.title
        return source

    def find(self, source_id: str) -> Source | None:
        """按编号找来源（S3）。"""
        wanted = source_id.strip().strip("[]").upper()
        return next((source for source in self._by_url.values() if source.id == wanted), None)

    def dump(self) -> list[dict]:
        return [asdict(source) for source in self._by_url.values()]
