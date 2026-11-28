"""岗位的读写逻辑。接口、之后的 Tool 和 MCP 都调用这里，规则只写一遍。"""

import hashlib
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, col, or_, select

from app.db.engine import serialized
from app.db.models import (
    STAGE_CATEGORY,
    Artifact,
    ArtifactKind,
    Job,
    JobCategory,
    JobEvent,
    JobPrep,
    JobStage,
    now_iso,
)
from app.db.similar import find_similar

JobSort = Literal["recent", "score"]


class JobNotFound(Exception):
    pass


class UrlTaken(ValueError):
    """改成的链接已经是另一个岗位的（链接唯一）。"""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"岗位 #{job_id} 已经用了这个链接")
        self.job_id = job_id


def jd_hash_of(jd_text: str) -> str | None:
    """忽略空白差异后的 JD 指纹，用来判断是不是同一份 JD。"""
    compact = "".join(jd_text.split())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest() if compact else None


def set_stage(
    session: Session, job: Job, to_stage: JobStage, note: str = "", happened_on: date | None = None
) -> None:
    """改阶段并记一条变更。happened_on 不填是今天。"""
    event = JobEvent(job_id=job.id, from_stage=job.stage, to_stage=to_stage, note=note)
    if happened_on:
        event.happened_on = happened_on.isoformat()
    session.add(event)
    job.stage = to_stage


# 最近一次评估的总分，按评分排序用
LATEST_SCORE = (
    select(Artifact.score)
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


def _touch(session: Session, job: Job) -> Job:
    job.updated_at = now_iso()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@serialized
def create_or_get_job(
    session: Session, *, url: str | None, jd_text: str, restore_deleted: bool = True, **fields
) -> tuple[Job, bool]:
    """新增岗位，返回 (岗位, 是否新建)。同一链接或同一份 JD 只保留一条。

    用户手动添加一个删除过的岗位时会恢复它；自动采集时不恢复（restore_deleted=False）。
    """
    url = (url or "").strip() or None
    jd_hash = jd_hash_of(jd_text)

    existing = None
    if url:
        existing = session.exec(select(Job).where(Job.url == url)).first()
    if existing is None and jd_hash:
        existing = session.exec(select(Job).where(Job.jd_hash == jd_hash)).first()
    if existing:
        if existing.deleted_at and restore_deleted:
            existing.deleted_at = None
            _touch(session, existing)
        return existing, False

    job = Job(url=url, jd_text=jd_text, jd_hash=jd_hash, stage=JobStage.not_applied, **fields)
    session.add(job)
    session.flush()  # 先拿到 job.id，变更记录要引用它
    job.similar_to = find_similar(session, job)
    session.add(
        JobEvent(job_id=job.id, from_stage=None, to_stage=JobStage.not_applied, note="添加岗位")
    )
    session.commit()
    session.refresh(job)
    return job, True


def alive_ids(session: Session, ids: set[int]) -> set[int]:
    """这些岗位里还没删除的编号。"""
    if not ids:
        return set()
    statement = select(Job.id).where(col(Job.id).in_(ids), Job.deleted_at == None)  # noqa: E711
    return set(session.exec(statement).all())


def stage_dates(session: Session, jobs: list[Job]) -> dict[int, str]:
    """每个岗位走到现在这个阶段是哪天（最近一次改到这个阶段时填的日期）。没改过阶段的不在里面。"""
    if not jobs:
        return {}
    current = {job.id: job.stage for job in jobs}
    statement = (
        select(JobEvent.job_id, JobEvent.to_stage, JobEvent.happened_on)
        .where(col(JobEvent.job_id).in_(current))
        .order_by(col(JobEvent.id))
    )
    dates: dict[int, str] = {}
    for job_id, to_stage, happened_on in session.exec(statement).all():
        if to_stage == current[job_id]:
            dates[job_id] = happened_on
    return dates


def find_by_url(session: Session, url: str) -> Job | None:
    """按链接找岗位，包括已删除的。"""
    return session.exec(select(Job).where(Job.url == url)).first()


def count_collected_since(session: Session, source: str, hours: int) -> int:
    """这个平台最近若干小时里采集进来多少个岗位（包括后来删掉的）。"""
    since = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    statement = (
        select(func.count()).select_from(Job).where(Job.source == source, Job.created_at >= since)
    )
    return session.exec(statement).one()


def save_collected(
    session: Session,
    *,
    url: str,
    source: str,
    company: str,
    title: str,
    city: str,
    salary: str,
    jd_text: str,
    company_size: str = "",
    industry: str = "",
    posted_at: str = "",
) -> tuple[Job, str]:
    """保存采集到的一个岗位，返回 (岗位, 结果)。重复采集同一个岗位不会多出一条。

    结果：new 新增且有完整 JD；completed 原来只有列表信息，这次补上了 JD；
    incomplete 只有列表信息；duplicate 链接不同但 JD 和已有岗位一样，或者是你删除过的岗位。
    """
    existing = find_by_url(session, url)
    if existing:
        if existing.deleted_at or existing.has_jd:
            return existing, "duplicate"
        if not jd_text.strip():
            return existing, "incomplete"
        return update_job(session, existing.id, {"jd_text": jd_text}), "completed"

    job, created = create_or_get_job(
        session,
        url=url,
        jd_text=jd_text,
        restore_deleted=False,
        source=source,
        company=company,
        title=title,
        city=city or None,
        salary=salary or None,
        company_size=company_size or None,
        industry=industry or None,
        posted_at=posted_at or None,
    )
    if not created:
        return job, "duplicate"
    return job, "new" if job.has_jd else "incomplete"


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.deleted_at:
        raise JobNotFound(job_id)
    return job


def list_jobs(
    session: Session,
    *,
    stage: JobStage | None,
    category: JobCategory | None = None,
    q: str | None,
    sort: JobSort = "recent",
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    """sort：recent 最近添加的在前；score 最近一次评估的总分高的在前，没评估的排最后。"""
    conditions = [col(Job.deleted_at).is_(None)]
    if stage:
        conditions.append(Job.stage == stage)
    if category:
        stages = [stage for stage, owner in STAGE_CATEGORY.items() if owner == category]
        conditions.append(col(Job.stage).in_(stages))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(or_(col(Job.company).like(pattern), col(Job.title).like(pattern)))

    total = session.exec(select(func.count()).select_from(Job).where(*conditions)).one()
    recent = (col(Job.created_at).desc(), col(Job.id).desc())
    order = (LATEST_SCORE.is_(None), LATEST_SCORE.desc(), *recent) if sort == "score" else recent
    jobs = session.exec(
        select(Job).where(*conditions).order_by(*order).limit(limit).offset(offset)
    ).all()
    return list(jobs), total


def count_by_stage(session: Session) -> dict[JobStage, int]:
    """每个阶段有多少个岗位。没有岗位的阶段也在结果里，数量为 0。"""
    rows = session.exec(
        select(Job.stage, func.count()).where(col(Job.deleted_at).is_(None)).group_by(Job.stage)
    ).all()
    counts = dict(rows)
    return {stage: counts.get(stage, 0) for stage in JobStage}


def prep_of(job: Job, evaluated: bool) -> JobPrep:
    if evaluated:
        return JobPrep.evaluated
    return JobPrep.unevaluated if job.has_jd else JobPrep.incomplete


def list_events(session: Session, job_id: int) -> list[JobEvent]:
    """阶段记录按操作的先后排。每条的 happened_on 是这一步实际发生的日子，只是一项说明。"""
    statement = select(JobEvent).where(JobEvent.job_id == job_id).order_by(col(JobEvent.id))
    return list(session.exec(statement).all())


def update_job(session: Session, job_id: int, changes: dict) -> Job:
    """改岗位信息。链接改成空就是去掉链接；改成别的岗位已经在用的链接时抛 UrlTaken。"""
    job = get_job(session, job_id)
    if "url" in changes:
        changes["url"] = (changes["url"] or "").strip() or None
        if changes["url"]:
            other = session.exec(
                select(Job.id).where(Job.url == changes["url"], Job.id != job_id)
            ).first()
            if other is not None:
                raise UrlTaken(other)
    for field, value in changes.items():
        setattr(job, field, value)
    if "jd_text" in changes:
        job.jd_hash = jd_hash_of(job.jd_text)
    if changes.keys() & {"jd_text", "company", "title", "city"}:
        job.similar_to = find_similar(session, job)
    return _touch(session, job)


@serialized
def change_stage(
    session: Session,
    job_id: int,
    to_stage: JobStage,
    *,
    note: str = "",
    happened_on: date | None = None,
) -> Job:
    """用户手动改阶段。happened_on 是这一步发生的日期，不填是今天。
    重复设为当前阶段不产生新的变更记录。"""
    job = get_job(session, job_id)
    if job.stage != to_stage:
        set_stage(session, job, to_stage, note, happened_on)
    return _touch(session, job)


def soft_delete_job(session: Session, job_id: int) -> None:
    job = get_job(session, job_id)
    job.deleted_at = now_iso()
    _touch(session, job)
