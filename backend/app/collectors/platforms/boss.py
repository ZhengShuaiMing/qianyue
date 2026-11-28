"""BOSS 直聘。

搜索页打开后，页面自己会请求搜索接口 /wapi/zpgeek/search/joblist.json，响应里有岗位列表
（职位名、公司、城市、明文薪资）和是否还有下一批。每批 15 个，页面是滚动加载的：滚到底，
页面会自己请求下一批。列表里没有职位描述，要逐个打开详情页读。

采集的标签页开在后台，后台的页面不响应滚动（2026-11-15 实测：后台滚到底等 20 秒，页面没有
发出请求），所以列表页要开启焦点仿真（scroll_paging）。2026-11-16 实测：后台 + 焦点仿真，
滚两次读到 3 批共 45 个不同的岗位，页面没有被清空。

2026-11-14 实测：
- BOSS 会把被自动化库连着的页面清空。迁跃的专用浏览器不启用它检测的那个调试功能
  （见 app/collectors/browser.py），登录后列表和详情都能正常读到。
- 地址里的页码不起作用，第 1 页和第 2 页返回的是同一批岗位。标签页在前台时，在同一个标签页里
  滚到底，连滚两次读到过 3 批共 45 个不同的岗位。
- 2026-11-15 实测：后台的标签页里，列表第一批和详情页都能正常读到，薪资也有。
- 全新的专用浏览器打开搜索页会被要求安全验证，采集停下，由用户自己完成。
- 打开页面时会先经过一个「请稍候」的过渡页，浏览器自己会跳回来。
- 没有登录时，详情页的职位描述只显示前一部分，后面盖着一层「登录查看完整内容」，
  搜索接口和详情页也都不给薪资。截断的内容不能当成完整 JD，所以遇到这层遮挡按「需要登录」停下。
- 职位描述里插着看不见的干扰词，innerText 不包含它们。

2026-11-17 实测（全新未登录的专用浏览器，只存列表，连续两次）：没有被要求安全验证，第一批
15 个能读到；往下滚动后页面没有加载出新的岗位。未登录时只有第一批。
"""

import json
import re
from urllib.parse import urlencode

from app.collectors.base import (
    MIN_JD_CHARS,
    Blocked,
    Listing,
    ListPage,
    PageResult,
    Platform,
    UnknownStructure,
)
from app.collectors.cities import cities_of
from app.collectors.registry import register

_JOB_ID = re.compile(r"^[\w~-]+$")
_PRIVATE_USE = re.compile(r"[-]")  # 页面用自定义字体遮挡数字时出现的字符
_REFUSED = re.compile(r"验证码|安全验证|滑块|访问异常|访问过于频繁|登录")


class Boss(Platform):
    key = "boss"
    name = "BOSS 直聘"
    home_url = "https://www.zhipin.com/"
    cities = cities_of("boss")
    list_capture = "/wapi/zpgeek/search/joblist.json"
    # 读「职位描述」那一块看得见的文字。
    detail_script = """() => {
      const header = document.querySelector('.detail-content-header');
      const node = document.querySelector('.detail-content-header + .job-sec-text')
        || (header && header.closest('.job-detail-section')
            && header.closest('.job-detail-section').querySelector('.job-sec-text'))
        || document.querySelector('.job-sec-text');
      if (!node) return null;
      const text = (node.innerText || '').trim();
      // 没有登录时只显示一部分，后面盖着一层「登录查看完整内容」。
      if (node.querySelector('.zp-more-info-layer-wrapper') || /登录查看完整内容$/.test(text)) {
        return { partial: true };
      }
      return text;
    }"""

    next_page = """() => {
      window.scrollTo(0, document.body.scrollHeight);
      return true;
    }"""
    scroll_paging = True

    def search_url(self, query: str, city_code: str) -> str:
        return (
            f"https://www.zhipin.com/web/geek/jobs?{urlencode({'query': query, 'city': city_code})}"
        )

    def parse_list(self, page: PageResult) -> ListPage:
        for captured in reversed(page.captured):
            try:
                body = json.loads(captured.body)
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            message = str(body.get("message") or body.get("msg") or "")
            if _REFUSED.search(message):
                raise Blocked(
                    f"{self.name}的搜索接口要求登录或验证（{message[:40]}）", show=page.url
                )
            data = body.get("zpData")
            if body.get("code") not in (None, 0) or not isinstance(data, dict):
                break
            jobs = data.get("jobList")
            if not isinstance(jobs, list) or not isinstance(data.get("hasMore"), bool):
                break
            listings = [listing for job in jobs if (listing := _listing(job))]
            if jobs and not listings:
                break
            return ListPage(listings, has_more=data["hasMore"])
        raise UnknownStructure("没有读到搜索接口返回的岗位数据")

    def parse_detail(self, page: PageResult) -> str:
        if isinstance(page.data, dict) and page.data.get("partial"):
            raise Blocked(f"{self.name}的职位描述只显示了一部分，登录后才能看全", show=page.url)
        if not isinstance(page.data, str) or len(page.data.strip()) < MIN_JD_CHARS:
            raise UnknownStructure("详情页里没有找到「职位描述」")
        return page.data.strip()


def _listing(job) -> Listing | None:
    if not isinstance(job, dict):
        return None
    job_id = str(job.get("encryptJobId") or "").strip()
    title = str(job.get("jobName") or "").strip()
    if not title or not _JOB_ID.match(job_id):
        return None
    salary = str(job.get("salaryDesc") or "").strip()
    return Listing(
        url=f"https://www.zhipin.com/job_detail/{job_id}.html",
        title=title,
        company=str(job.get("brandName") or "").strip(),
        city=" ".join(
            part
            for key in ("cityName", "areaDistrict")
            if (part := str(job.get(key) or "").strip())
        ),
        salary="" if _PRIVATE_USE.search(salary) else salary,
        experience=str(job.get("jobExperience") or "").strip(),
        company_size=str(job.get("brandScaleName") or "").strip(),
        industry=str(job.get("brandIndustry") or "").strip(),
    )


register(Boss())
