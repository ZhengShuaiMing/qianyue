"""智联招聘。

搜索页的每个岗位都带完整的职位描述，所以不用再打开详情页。岗位数据有两个地方能读到：
- 列表面板（.job-list-panel）里每张岗位卡片绑定的数据。滚动加载出来的新岗位只在这里。
- 页面打开时的状态 window.__INITIAL_STATE__.positionList，只有第一批。面板读不到时用它。

2026-11-14 实测（未登录）：只有 20 个，页面说没有更多（hasMore 是 false）。地址里的页码会被去掉。
2026-11-17 复测（全新未登录的专用浏览器，连续两次）：还是只有一批 20 个，两次的岗位不完全一样。
2026-11-16 实测（登录后）：页面说还有更多。把最后一张卡片滚进视野，页面会自己加载下一批，
每批 20 个；滚两次读到 60 个，每个都带职位描述。列表在页面内部的面板里滚动，滚整个页面没有用。
采集的标签页开在后台，所以列表页要开启焦点仿真（scroll_paging）。
"""

from urllib.parse import urlencode, urlsplit

from app.collectors.base import Listing, ListPage, PageResult, Platform, UnknownStructure, date_of
from app.collectors.cities import cities_of
from app.collectors.registry import register


class Zhaopin(Platform):
    key = "zhaopin"
    name = "智联招聘"
    home_url = "https://www.zhaopin.com/"
    cities = cities_of("zhaopin")
    # 只取岗位本身的字段，不带出招聘人员的信息。
    list_script = """() => {
      const pick = (item) => ({
        url: item.positionUrl || item.positionURL || '',
        title: item.name || '',
        company: item.companyName || '',
        city: [item.workCity, item.cityDistrict].filter(Boolean).join(' '),
        salary: item.salary60 || '',
        experience: item.workingExp || '',
        description: (((item.jobDetailData || {}).position || {}).desc || {}).description || '',
        company_size: item.companySize || '',
        industry: item.industryName || '',
        posted_at: item.publishTime || '',
      });
      const panel = document.querySelector('.job-list-panel');
      const props = panel && panel.__vue__ && panel.__vue__.$props;
      const cards = panel ? [...panel.querySelectorAll('.job-card')] : [];
      const bound = cards
        .map((card) => card.__vue__ && card.__vue__.$props && card.__vue__.$props.job)
        .filter(Boolean);
      if (props && bound.length) return { hasMore: props.hasMore === true, jobs: bound.map(pick) };
      const state = window.__INITIAL_STATE__;
      if (!state || !Array.isArray(state.positionList)) return null;
      return { hasMore: state.hasMore === true, jobs: state.positionList.map(pick) };
    }"""
    next_page = """() => {
      const panel = document.querySelector('.job-list-panel');
      const props = panel && panel.__vue__ && panel.__vue__.$props;
      const last = panel && panel.querySelector('.job-card:last-child');
      if (!props || props.hasMore !== true || !last) return false;
      last.scrollIntoView({ block: 'end', behavior: 'instant' });
      return true;
    }"""
    scroll_paging = True

    def search_url(self, query: str, city_code: str) -> str:
        return f"https://www.zhaopin.com/sou/?{urlencode({'jl': city_code, 'kw': query})}"

    def parse_list(self, page: PageResult) -> ListPage:
        data = page.data
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise UnknownStructure("页面里没有找到岗位数据")
        listings = [listing for item in data["jobs"] if (listing := _listing(item))]
        if data["jobs"] and not listings:
            raise UnknownStructure("岗位数据里没有链接或职位名")
        return ListPage(listings, has_more=data.get("hasMore") is True)


def _listing(item) -> Listing | None:
    if not isinstance(item, dict):
        return None
    parts = urlsplit(str(item.get("url") or ""))
    title = str(item.get("title") or "").strip()
    if not title or not (parts.hostname or "").endswith("zhaopin.com"):
        return None
    return Listing(
        url=f"https://{parts.hostname}{parts.path}",
        title=title,
        company=str(item.get("company") or "").strip(),
        city=str(item.get("city") or "").strip(),
        salary=str(item.get("salary") or "").strip(),
        jd_text=str(item.get("description") or "").strip(),
        experience=str(item.get("experience") or "").strip(),
        company_size=str(item.get("company_size") or "").strip(),
        industry=str(item.get("industry") or "").strip(),
        posted_at=date_of(item.get("posted_at")),
    )


register(Zhaopin())
