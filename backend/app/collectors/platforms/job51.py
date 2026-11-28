"""前程无忧。

搜索页打开后，页面自己会请求搜索接口 /api/job/search-pc，响应里每个岗位都带完整的职位描述，
所以不用再打开详情页（它的详情页会弹滑动验证）。每页 20 个，翻页是点页面上的「下一页」，
页面会自己请求下一页的数据。

2026-11-14 实测：不登录就能翻页，连点两次读到 3 页共 59 个不同的岗位。
"""

import json
from urllib.parse import urlencode, urlsplit

from app.collectors.base import Listing, ListPage, PageResult, Platform, UnknownStructure, date_of
from app.collectors.cities import cities_of
from app.collectors.registry import register


class Job51(Platform):
    key = "51job"
    name = "前程无忧"
    home_url = "https://www.51job.com/"
    cities = cities_of("51job")
    list_capture = "/api/job/search-pc"
    next_page = """() => {
      const next = document.querySelector('.el-pagination .btn-next');
      if (!next || next.disabled) return false;
      next.click();
      return true;
    }"""

    def search_url(self, query: str, city_code: str) -> str:
        params = {
            "jobArea": city_code,
            "keyword": query,
            "searchType": "2",
            "sortType": "0",
            "metro": "",
        }
        return f"https://we.51job.com/pc/search?{urlencode(params)}"

    def parse_list(self, page: PageResult) -> ListPage:
        # 第一次响应可能是网站自己的过渡页（不是 JSON），浏览器随后会自动重新请求。
        for captured in reversed(page.captured):
            try:
                items = json.loads(captured.body)["resultbody"]["job"]["items"]
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(items, list):
                break
            listings = [listing for item in items if (listing := _listing(item))]
            if items and not listings:
                break
            # 接口不说还有没有下一页。到了最后一页，「下一页」按钮是禁用的，翻页动作会说没有了。
            return ListPage(listings, has_more=bool(listings))
        raise UnknownStructure("没有读到搜索接口返回的岗位数据")


def _listing(item) -> Listing | None:
    if not isinstance(item, dict):
        return None
    parts = urlsplit(str(item.get("jobHref") or ""))
    title = str(item.get("jobName") or "").strip()
    if not title or not (parts.hostname or "").endswith("51job.com"):
        return None
    return Listing(
        url=f"https://{parts.hostname}{parts.path}",  # 去掉链接后面的跟踪参数
        title=title,
        company=str(item.get("fullCompanyName") or item.get("companyName") or "").strip(),
        city=str(item.get("jobAreaString") or "").strip(),
        salary=str(item.get("provideSalaryString") or "").strip(),
        jd_text=str(item.get("jobDescribe") or "").strip(),
        experience=str(item.get("workYearString") or "").strip(),
        company_size=str(item.get("companySizeString") or "").strip(),
        industry=str(item.get("companyIndustryType1Str") or "").strip(),
        posted_at=date_of(item.get("issueDateString")),
    )


register(Job51())
