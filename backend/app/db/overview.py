"""总览页的统计：数字卡片、阶段漏斗、各来源对比、按天/周/月的节奏、评分分布。只读，按需现算。

「走到过哪一步」：现在还在流程里（未投递、已投递、已笔试、已面试）的岗位，以现在的阶段为准，
这样误点了「已投递」又改回「未投递」不会多算；已经结束的（Offer、未通过、放弃）看改阶段的记录
走到过哪一步，例如面试后被拒也算面试过。「未通过」至少说明投递过；「放弃」可能从没投过。
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.collectors.registry import PLATFORMS
from app.db.jobs import LATEST_SCORE
from app.db.models import (
    STAGE_CATEGORY,
    Artifact,
    ArtifactKind,
    Job,
    JobCategory,
    JobEvent,
    JobStage,
)

DEPTH = {
    JobStage.not_applied: 0,
    JobStage.withdrawn: 0,
    JobStage.applied: 1,
    JobStage.rejected: 1,
    JobStage.assessment: 2,
    JobStage.interviewed: 3,
    JobStage.offer: 4,
}
APPLIED, INTERVIEWED, OFFER = 1, 3, 4
# 「值得投递」按最近一次评估的结论算：优先投递或值得投递。有已确认的硬性阻断时，
# 分数再高结论也是不建议，所以不按分数线数
WORTH = ("priority", "worth")
LATEST_VERDICT = (
    select(func.json_extract(Artifact.data_json, "$.scorecard.verdict"))
    .where(
        Artifact.job_id == Job.id,
        Artifact.kind == ArtifactKind.evaluation,
        col(Artifact.deleted_at).is_(None),
    )
    .order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc())
    .limit(1)
    .correlate(Job)
    .scalar_subquery()
)
# 和评估结论用同一组分数线：4.5 优先投、4.0 值得投、3.5 确认风险后再投
SCORE_BANDS = [("4.5 及以上", 4.5), ("4.0–4.4", 4.0), ("3.5–3.9", 3.5), ("3.5 以下", 0.0)]
# 趋势：按天看近 14 天，按周看近 8 周（周一开始），按月看近 6 个月
SPANS = {"day": 14, "week": 8, "month": 6}


def _day(value: str) -> date:
    """带时区的完整时间换成本地日期。"""
    return datetime.fromisoformat(value).astimezone().date()


def _source_name(source: str) -> str:
    if source == "manual":
        return "手动添加"
    platform = PLATFORMS.get(source)
    return platform.name if platform else source


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _average(scores: list[float]) -> float | None:
    return round(sum(scores) / len(scores), 1) if scores else None


def _periods(today: date, unit: str) -> list[date]:
    """这几个时间段的开头，从早到晚，最后一个包含今天。"""
    count = SPANS[unit]
    if unit == "day":
        return [today - timedelta(days=n) for n in range(count - 1, -1, -1)]
    if unit == "week":
        monday = today - timedelta(days=today.weekday())
        return [monday - timedelta(weeks=n) for n in range(count - 1, -1, -1)]
    starts = []
    year, month = today.year, today.month
    for _ in range(count):
        starts.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return starts[::-1]


def _count(days, starts: list[date]) -> list[int]:
    """每个日子落在哪个时间段；早于第一个时间段的不算。"""
    counts = [0] * len(starts)
    for day in days:
        for index in range(len(starts) - 1, -1, -1):
            if day >= starts[index]:
                counts[index] += 1
                break
    return counts


def build_overview(session: Session, today: date) -> dict:
    jobs = session.exec(
        select(
            Job.id,
            Job.source,
            Job.stage,
            Job.jd_text,
            Job.created_at,
            LATEST_SCORE.label("score"),
            LATEST_VERDICT.label("verdict"),
        ).where(col(Job.deleted_at).is_(None))
    ).all()
    events = session.exec(
        select(JobEvent.job_id, JobEvent.to_stage, JobEvent.happened_on)
        .where(col(JobEvent.job_id).in_([job.id for job in jobs]))
        .order_by(col(JobEvent.id))
    ).all()

    closed = {job.id for job in jobs if STAGE_CATEGORY[JobStage(job.stage)] == JobCategory.closed}
    depth = {job.id: DEPTH[JobStage(job.stage)] for job in jobs}
    # 第一次走到投递、面试的日子：按改阶段时填的日期（没填是改的那天）
    first: dict[int, dict[int, date]] = defaultdict(dict)
    for job_id, to_stage, happened_on in events:
        step = DEPTH[JobStage(to_stage)]
        if job_id in closed:
            depth[job_id] = max(depth[job_id], step)
        for mark in (APPLIED, INTERVIEWED):
            if step >= mark:
                day = date.fromisoformat(happened_on)
                first[job_id][mark] = min(first[job_id].get(mark, day), day)

    def reached(mark: int) -> list:
        return [job for job in jobs if depth[job.id] >= mark]

    def days_reaching(mark: int) -> list[date]:
        return [first[job.id][mark] for job in reached(mark) if mark in first[job.id]]

    total = len(jobs)
    evaluated = [job for job in jobs if job.score is not None]
    scores = [float(job.score) for job in evaluated]
    applied, interviewed, offers = reached(APPLIED), reached(INTERVIEWED), reached(OFFER)
    created = [_day(job.created_at) for job in jobs]

    trend = {}
    for unit in SPANS:
        starts = _periods(today, unit)
        columns = zip(
            starts,
            _count(created, starts),
            _count(days_reaching(APPLIED), starts),
            _count(days_reaching(INTERVIEWED), starts),
            strict=True,
        )
        trend[unit] = [
            {"start": start.isoformat(), "added": added, "applied": sent, "interviewed": met}
            for start, added, sent, met in columns
        ]

    rows: dict[str, dict] = defaultdict(
        lambda: {"jobs": 0, "scores": [], "applied": 0, "interviewed": 0, "offer": 0}
    )
    for job in jobs:
        row = rows[job.source]
        row["jobs"] += 1
        row["applied"] += depth[job.id] >= APPLIED
        row["interviewed"] += depth[job.id] >= INTERVIEWED
        row["offer"] += depth[job.id] >= OFFER
        if job.score is not None:
            row["scores"].append(float(job.score))

    bands, rest = [], list(scores)
    for label, floor in SCORE_BANDS:
        bands.append({"label": label, "count": sum(score >= floor for score in rest)})
        rest = [score for score in rest if score < floor]

    return {
        "total": total,
        "worth": sum(job.verdict in WORTH for job in evaluated),
        "rejected": sum(job.stage == JobStage.rejected for job in jobs),
        "withdrawn": sum(job.stage == JobStage.withdrawn for job in jobs),
        "evaluated": len(evaluated),
        "average_score": _average(scores),
        "funnel": [
            {"step": "total", "count": total},
            {"step": "has_jd", "count": sum(bool(job.jd_text.strip()) for job in jobs)},
            {"step": "evaluated", "count": len(evaluated)},
            {"step": "applied", "count": len(applied)},
            {"step": "interviewed", "count": len(interviewed)},
            {"step": "offer", "count": len(offers)},
        ],
        "rates": {
            "applied": _rate(len(applied), total),
            "interviewed": _rate(len(interviewed), len(applied)),
            "offer": _rate(len(offers), len(applied)),
        },
        "platforms": sorted(
            (
                {
                    "source": source,
                    "name": _source_name(source),
                    "jobs": row["jobs"],
                    "evaluated": len(row["scores"]),
                    "average_score": _average(row["scores"]),
                    "applied": row["applied"],
                    "interviewed": row["interviewed"],
                    "offer": row["offer"],
                    "applied_rate": _rate(row["applied"], row["jobs"]),
                }
                for source, row in rows.items()
            ),
            key=lambda row: (-row["jobs"], row["source"]),
        ),
        "trend": trend,
        "scores": bands,
        "points": [
            {"score": float(job.score), "added": _day(job.created_at).isoformat()}
            for job in sorted(evaluated, key=lambda job: job.created_at)
        ],
    }
