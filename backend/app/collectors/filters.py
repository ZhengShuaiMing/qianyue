"""采集时的筛选：按薪资、经验要求、职位名里的词，把不想要的岗位在读详情之前就跳过。

平台写薪资和经验的方式各不相同（2026-11-16 四个平台各读一页列表核对过）：
- 薪资：`40-60K`、`15-25k·13薪`（BOSS、猎聘），`1.5-3万`、`8千-1.5万·13薪`（智联、前程无忧），
  `8000-16000元`，`100-150元/天`，`面议`。
- 经验：`3-5年`、`3年以上`、`3年及以上`、`1年`、`经验不限`、`无需经验`，也可能是空的。

读不懂的一律不筛：宁可多留，不误删。
"""

import re
from dataclasses import dataclass, field

from app.collectors.base import Listing

_UNIT = {"k": 1.0, "K": 1.0, "千": 1.0, "万": 10.0}
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*([kK千万]?)\s*[-~–—至]\s*(\d+(?:\.\d+)?)\s*([kK千万元]?)")
_YEARS = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?年")
YEARS_TOLERANCE = 1  # 要求的最低年限比你的年限多这么多以内，仍然保留


def monthly_range(salary: str) -> tuple[float, float] | None:
    """月薪范围，单位是千元。按天、按小时计的，和读不懂的，返回 None。"""
    text = salary.split("·")[0].strip()  # 「·13薪」是一年发几个月，不影响月薪
    if not text or re.search(r"/\s*(?:天|日|时|小时|周)", text):
        return None
    found = _RANGE.search(text)
    if found is None:
        return None
    low, low_unit, high, high_unit = found.groups()
    if high_unit == "元" or not (low_unit or high_unit):
        scale_low = scale_high = 0.001  # 直接写的是元
    else:
        scale_high = _UNIT[high_unit or low_unit]
        scale_low = _UNIT[low_unit or high_unit]
    low_k, high_k = float(low) * scale_low, float(high) * scale_high
    if re.search(r"/\s*年|年薪", text):
        low_k, high_k = low_k / 12, high_k / 12
    return (low_k, high_k) if 0 < low_k <= high_k else None


def required_years(experience: str) -> int | None:
    """岗位要求的最低工作年限。不限经验是 0；没写或读不懂返回 None。"""
    text = experience.strip()
    if not text:
        return None
    if re.search(r"不限|无需|应届|在校|以内|以下", text):
        return 0
    found = _YEARS.search(text)
    return int(found.group(1)) if found else None


@dataclass(frozen=True)
class Filters:
    min_salary: int | None = None  # 千元/月。岗位月薪的上限低于它就跳过
    years: int | None = None  # 你的工作年限。岗位要求的最低年限比它多 1 年以上就跳过
    exclude: tuple[str, ...] = field(default_factory=tuple)  # 职位名里出现这些词就跳过

    @classmethod
    def from_input(cls, data: dict | None) -> "Filters":
        data = data or {}
        return cls(
            min_salary=data.get("min_salary"),
            years=data.get("years"),
            exclude=tuple(data.get("exclude") or ()),
        )

    def reject_reason(self, listing: Listing) -> str | None:
        """这个岗位为什么被跳过：salary、experience、title。不跳过返回 None。"""
        title = listing.title.lower()
        if any(word.lower() in title for word in self.exclude):
            return "title"
        if self.min_salary is not None:
            salary = monthly_range(listing.salary)
            if salary is not None and salary[1] < self.min_salary:
                return "salary"
        if self.years is not None:
            needed = required_years(listing.experience)
            if needed is not None and needed > self.years + YEARS_TOLERANCE:
                return "experience"
        return None
