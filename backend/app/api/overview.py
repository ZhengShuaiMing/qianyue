"""总览页：岗位、投递和评分的统计，一次给全。"""

from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.db.overview import build_overview

router = APIRouter(prefix="/overview", tags=["总览"])


class FunnelStep(BaseModel):
    step: Literal["total", "has_jd", "evaluated", "applied", "interviewed", "offer"]
    count: int = Field(
        description="全部岗位、有 JD 的、评估过的、投递过的、面试过的、拿到 Offer 的。"
        "后三步按「走到过」算：结束了的岗位看改阶段的记录，还在流程里的以现在的阶段为准"
    )


class Rates(BaseModel):
    applied: float | None = Field(description="投递过的占全部岗位，0–1")
    interviewed: float | None = Field(description="面试过的占投递过的")
    offer: float | None = Field(description="拿到 Offer 的占投递过的")


class PlatformRow(BaseModel):
    source: str
    name: str
    jobs: int
    evaluated: int
    average_score: float | None
    applied: int
    interviewed: int
    offer: int
    applied_rate: float | None = Field(description="这个来源的岗位里投递过的比例")


class Period(BaseModel):
    start: str = Field(description="这一段的第一天：按天是那一天，按周是周一，按月是 1 号")
    added: int
    applied: int
    interviewed: int


class Trend(BaseModel):
    day: list[Period] = Field(description="近 14 天")
    week: list[Period] = Field(description="近 8 周")
    month: list[Period] = Field(description="近 6 个月")


class ScoreBand(BaseModel):
    label: str
    count: int


class ScorePoint(BaseModel):
    score: float = Field(description="最近一次评估的总分")
    added: str = Field(description="岗位添加的日期，YYYY-MM-DD")


class Overview(BaseModel):
    total: int
    worth: int = Field(description="最近一次评估 4.0 分及以上（值得投）的岗位")
    rejected: int = Field(description="现在的阶段是「未通过」的岗位")
    withdrawn: int = Field(description="现在的阶段是「已放弃」的岗位")
    evaluated: int
    average_score: float | None = Field(description="评估过的岗位最近一次评估的平均总分")
    funnel: list[FunnelStep]
    rates: Rates
    platforms: list[PlatformRow] = Field(description="按岗位数从多到少")
    trend: Trend = Field(description="从早到晚，最后一段包含今天")
    scores: list[ScoreBand] = Field(description="评分分布，从高到低，分数线和评估结论一致")
    points: list[ScorePoint] = Field(
        description="评估过的每个岗位：最近一次评估的总分和添加日期，按添加先后。评分分布图上一个岗位一个点"
    )


@router.get("", response_model=Overview)
def get_overview(session: SessionDep):
    """总览：数字卡片、全流程漏斗、各平台对比、按天/周/月的趋势、评分分布。"""
    return build_overview(session, date.today())
