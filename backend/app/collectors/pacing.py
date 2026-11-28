"""访问节奏：翻页、读详情、换搜索之间各等多久。

等待时间在一个区间里随机取。这只是为了不给平台造成压力，不保证不被限制；
被限制了就停下，不通过并发或重启绕开等待。


"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

PAGE_SECONDS = (6.0, 11.0)  # 同一个搜索里翻到下一页之前
DETAIL_SECONDS = (5.0, 12.0)  # 每读一个岗位详情之前
REST_EVERY = 6  # 同一个平台每读这么多个详情，长休息一次
REST_SECONDS = (22.0, 45.0)
SEARCH_GAP_SECONDS = 22.0  # 同一个平台两次搜索之间至少隔这么久
DAILY_NEW_JOBS = 200  # 每个平台 24 小时内最多新增这么多个岗位


@dataclass
class Pacer:
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    uniform: Callable[[float, float], float] = random.uniform
    clock: Callable[[], float] = time.monotonic
    _details: dict[str, int] = field(default_factory=dict)
    _last_search: dict[str, float] = field(default_factory=dict)

    async def before_search(self, platform: str) -> None:
        last = self._last_search.get(platform)
        if last is not None:
            remaining = SEARCH_GAP_SECONDS - (self.clock() - last)
            if remaining > 0:
                await self.sleep(remaining)
        self._last_search[platform] = self.clock()

    async def before_page(self) -> None:
        await self.sleep(self.uniform(*PAGE_SECONDS))

    async def before_detail(self, platform: str) -> None:
        done = self._details.get(platform, 0)
        resting = done > 0 and done % REST_EVERY == 0
        await self.sleep(self.uniform(*(REST_SECONDS if resting else DETAIL_SECONDS)))
        self._details[platform] = done + 1
