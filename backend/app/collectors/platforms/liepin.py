"""猎聘。

搜索页打开后，页面自己会请求搜索接口（地址以 pc-search-job 结尾），响应里有岗位列表和分页。
每页约 40 个，翻页是点页面上的「下一页」。列表里没有职位描述，要逐个打开详情页，
从「职位介绍」那一块读。

2026-11-14 实测：列表不登录就能翻页（点一次，第 2 页 41 个新岗位）。接口里的 hasNext 一直是
false，要用 currentPage 和 totalPage 判断还有没有下一页。

2026-11-14 实测：没有登录时，猎聘有时把详情页带到一个登录页（wow.liepin.com 下，标题只有
「猎聘」，提示「请使用微信扫描二维码登录猎聘」）。同一次采集里第 1 个详情能读，第 2 个开始被带走；
不连调试连接、让浏览器自己打开也一样。这时按「需要登录」停下。
"""

import json
from urllib.parse import urlencode, urlsplit

from app.collectors.base import (
    MIN_JD_CHARS,
    Blocked,
    Listing,
    ListPage,
    PageResult,
    Platform,
    UnknownStructure,
    date_of,
)
from app.collectors.cities import cities_of
from app.collectors.registry import register


class Liepin(Platform):
    key = "liepin"
    name = "猎聘"
    home_url = "https://www.liepin.com/"
    cities = cities_of("liepin")
    list_capture = ".pc-search-job"
    detail_script = """() => {
      const node = document.querySelector('[data-selector="job-intro-content"]');
      if (node) return (node.innerText || '').trim();
      // 没有「职位介绍」，页面上是登录提示：被带到登录页了。
      const prompt = [...document.querySelectorAll('body *:not(script):not(style)')]
        .some(item => item.children.length === 0 && /登录猎聘/.test(item.textContent || ''));
      return prompt ? { login: true } : null;
    }"""

    next_page = """() => {
      const next = document.querySelector('.ant-pagination-next');
      if (!next || next.className.includes('ant-pagination-disabled')) return false;
      (next.querySelector('button') || next).click();
      return true;
    }"""

    def search_url(self, query: str, city_code: str) -> str:
        return f"https://www.liepin.com/zhaopin/?{urlencode({'key': query, 'dq': city_code})}"

    def parse_list(self, page: PageResult) -> ListPage:
        for captured in reversed(page.captured):
            try:
                data = json.loads(captured.body)["data"]
                cards = data["data"]["jobCardList"]
            except (ValueError, KeyError, TypeError):
                continue  # 同一个页面还会请求筛选项接口，地址相近，结构不同
            if not isinstance(cards, list):
                break
            listings = [listing for card in cards if (listing := _listing(card))]
            if cards and not listings:
                break
            pagination = data.get("pagination") or {}
            current, total = pagination.get("currentPage"), pagination.get("totalPage")  # 从 0 数
            has_more = isinstance(current, int) and isinstance(total, int) and current + 1 < total
            return ListPage(listings, has_more=has_more)
        raise UnknownStructure("没有读到搜索接口返回的岗位数据")

    def parse_detail(self, page: PageResult) -> str:
        if isinstance(page.data, dict) and page.data.get("login"):
            raise Blocked(f"{self.name}要求登录后才能看职位详情", show=page.url)
        if not isinstance(page.data, str) or len(page.data.strip()) < MIN_JD_CHARS:
            raise UnknownStructure("详情页里没有找到「职位介绍」")
        return page.data.strip()


def _listing(card) -> Listing | None:
    job = card.get("job") if isinstance(card, dict) else None
    if not isinstance(job, dict):
        return None
    parts = urlsplit(str(job.get("link") or ""))
    title = str(job.get("title") or "").strip()
    if not title or not (parts.hostname or "").endswith("liepin.com"):
        return None
    company = card.get("comp") if isinstance(card.get("comp"), dict) else {}
    return Listing(
        url=f"https://{parts.hostname}{parts.path}",
        title=title,
        company=str(company.get("compName") or "").strip(),
        city=str(job.get("dq") or "").strip(),
        salary=str(job.get("salary") or "").strip(),
        experience=str(job.get("requireWorkYears") or "").strip(),
        company_size=str(company.get("compScale") or "").strip(),
        industry=str(company.get("compIndustry") or "").strip(),
        posted_at=date_of(job.get("refreshTime")),
    )


register(Liepin())
