"""一次采集的范围：哪些平台、哪些关键词、哪些城市、每个搜索要多少个（可以按平台分别给）。
接口、助手和 MCP 共用。"""

from pydantic import BaseModel, Field, model_validator

from app.collectors.registry import PLATFORMS

MAX_SEARCHES = 25
MAX_PER_SEARCH = 20
MAX_TOTAL = 100


class CollectionRequest(BaseModel):
    """采集的范围。平台 × 关键词 × 城市的每一种组合是一个搜索。"""

    platforms: list[str] = Field(
        min_length=1, description="平台：`51job` `liepin` `zhaopin` `boss`"
    )
    queries: list[str] = Field(min_length=1, description="搜索关键词，例如「前端开发」")
    cities: list[str] = Field(min_length=1, description="城市的中文名，例如「北京」")
    per_search: int = Field(
        default=10, ge=1, le=MAX_PER_SEARCH, description="每个搜索新增多少个有完整 JD 的岗位"
    )
    per_platform: dict[str, int] = Field(
        default_factory=dict,
        description='按平台单独给数量，例如 {"boss": 15, "51job": 20}；没写到的平台用 per_search',
    )
    min_salary: int | None = Field(
        default=None, ge=1, le=500, description="最低月薪，单位千元。岗位月薪的上限低于它就跳过"
    )
    years: int | None = Field(
        default=None,
        ge=0,
        le=50,
        description="你的工作年限。岗位要求的最低年限比它多 1 年以上就跳过",
    )
    exclude: list[str] = Field(
        default_factory=list, max_length=20, description="职位名里出现这些词就跳过，例如「实习」"
    )
    relevant_only: bool = Field(
        default=True,
        description="跳过和关键词明显无关的岗位（模型根据职位名判断，没有配置模型 Key 时不筛）",
    )
    evaluate: bool = Field(default=False, description="采完后自动评估新增的岗位（需要模型 Key）")
    list_only: bool = Field(
        default=False,
        description="不打开详情页，只保存列表上的信息。列表不带职位描述的岗位标为「JD 不完整」，"
        "补上 JD 后才能评估。不想登录猎聘、BOSS 直聘时用",
    )

    @model_validator(mode="after")
    def check_scope(self):
        self.platforms = list(dict.fromkeys(self.platforms))
        self.queries = list(dict.fromkeys(query.strip() for query in self.queries))
        self.cities = list(dict.fromkeys(city.strip() for city in self.cities))
        self.exclude = list(dict.fromkeys(word.strip() for word in self.exclude if word.strip()))
        if not all(self.queries) or any(len(query) > 40 for query in self.queries):
            raise ValueError("关键词不能为空，也不要超过 40 个字")
        unknown = [key for key in self.platforms if key not in PLATFORMS]
        if unknown:
            raise ValueError(f"不支持的平台：{'、'.join(unknown)}。可选：{'、'.join(PLATFORMS)}")
        for key in self.platforms:
            missing = [city for city in self.cities if city not in PLATFORMS[key].cities]
            if missing:
                raise ValueError(
                    f"{PLATFORMS[key].name}没有城市「{'、'.join(missing)}」。请用城市的中文名，例如「北京」"
                )
        stray = [key for key in self.per_platform if key not in self.platforms]
        if stray:
            raise ValueError(f"per_platform 里的平台没有选：{'、'.join(stray)}")
        bad = [key for key, count in self.per_platform.items() if not 1 <= count <= MAX_PER_SEARCH]
        if bad:
            raise ValueError(f"每个搜索的数量要在 1 到 {MAX_PER_SEARCH} 之间：{'、'.join(bad)}")
        searches = self.searches()
        total = sum(search["count"] for search in searches)
        if len(searches) > MAX_SEARCHES or total > MAX_TOTAL:
            raise ValueError(
                f"范围太大：{len(searches)} 个搜索，一共 {total} 个岗位。"
                f"一次最多 {MAX_SEARCHES} 个搜索、总共 {MAX_TOTAL} 个岗位"
            )
        return self

    def run_input(self) -> dict:
        """交给采集运行的输入。"""
        return {
            "searches": self.searches(),
            "filters": {
                "min_salary": self.min_salary,
                "years": self.years,
                "exclude": self.exclude,
                "relevant_only": self.relevant_only,
                "list_only": self.list_only,
            },
            "evaluate": self.evaluate,
        }

    def searches(self) -> list[dict]:
        """每个搜索带着自己要新增的数量：按平台给了就用平台的，没给就用 per_search。"""
        return [
            {
                "platform": platform,
                "query": query,
                "city": city,
                "count": self.per_platform.get(platform, self.per_search),
            }
            for platform in self.platforms
            for query in self.queries
            for city in self.cities
        ]
