from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import SessionDep
from app.collectors.registry import PLATFORMS
from app.db import artifacts as artifact_store
from app.db import jobs as job_store
from app.db.models import STAGE_CATEGORY, Job, JobCategory, JobPrep, JobStage

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    url: str | None = None
    jd_text: str = ""
    company: str = ""
    title: str = ""
    city: str | None = None
    salary: str | None = None
    source: str = "manual"
    note: str = ""

    @model_validator(mode="after")
    def need_url_or_jd(self):
        if not (self.url or "").strip() and not self.jd_text.strip():
            raise ValueError("链接和 JD 文本至少填一个")
        return self


class JobUpdate(BaseModel):
    url: str | None = Field(default=None, description="岗位链接。传空字符串表示去掉链接")
    source: str | None = Field(
        default=None, description="来源：manual 或平台编号（boss、liepin 等）"
    )
    company: str | None = None
    title: str | None = None
    city: str | None = None
    salary: str | None = None
    jd_text: str | None = None
    note: str | None = None

    @field_validator("source")
    @classmethod
    def known_source(cls, value: str | None) -> str | None:
        if value is not None and value != "manual" and value not in PLATFORMS:
            raise ValueError(f"没有「{value}」这个来源")
        return value

    @field_validator("jd_text")
    @classmethod
    def jd_not_blank(cls, value: str | None) -> str | None:
        # 清空 JD 没有用处，还会让评估过的岗位留着一份对不上 JD 的报告。要换 JD 就直接改
        if value is not None and not value.strip():
            raise ValueError("JD 不能清空。要换一份 JD，直接粘贴新的")
        return value


class StageChange(BaseModel):
    stage: JobStage
    note: str = ""
    happened_on: date | None = Field(default=None, description="这一步是哪天发生的，不填是今天")

    @field_validator("happened_on")
    @classmethod
    def not_in_future(cls, value: date | None) -> date | None:
        if value and value > date.today():
            raise ValueError("日期不能晚于今天")
        return value


class EvaluationBrief(BaseModel):
    """最近一次评估的结论，列表上显示分数、按结论配色用。完整报告用 artifact_id 去取。"""

    artifact_id: int
    score: float | None
    verdict: str | None


class JobSummary(BaseModel):
    """列表用：不带 JD 全文。`stage` 是应聘进展（你改的）；`prep` 是处理进度（JD 不完整、
    待评估、已评估，按有没有 JD 和评估报告算的）；`evaluation` 是最近一次评估，没评估过是 null。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    url: str | None
    company: str
    title: str
    city: str | None
    salary: str | None
    stage: JobStage
    prep: JobPrep = JobPrep.incomplete
    has_jd: bool
    note: str
    created_at: str
    updated_at: str
    similar_to: int | None = Field(
        default=None,
        description="看起来和这个更早的岗位是同一个（只提示，不合并）。那个删了就是 null",
    )
    evaluation: EvaluationBrief | None = None
    stage_on: str | None = Field(
        default=None, description="走到现在这个阶段是哪天（YYYY-MM-DD）。没改过阶段是 null"
    )


class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_stage: JobStage | None
    to_stage: JobStage
    happened_on: str = Field(description="这一步是哪天发生的")
    note: str
    created_at: str


class JobDetail(JobSummary):
    jd_text: str
    company_size: str | None = Field(default=None, description="公司规模，采集时从平台列表读到")
    industry: str | None = Field(default=None, description="公司所在行业，采集时从平台列表读到")
    posted_at: str | None = Field(
        default=None, description="平台显示的发布日期（猎聘是刷新日期），YYYY-MM-DD"
    )
    events: list[JobEventRead]


class JobList(BaseModel):
    items: list[JobSummary]
    total: int


class JobOverview(BaseModel):
    """全部岗位的进展。`categories` 是三大类的数量，`stages` 是每个应聘阶段的数量。"""

    total: int
    categories: dict[JobCategory, int]
    stages: dict[JobStage, int]


def _brief(artifact) -> EvaluationBrief:
    return EvaluationBrief(
        artifact_id=artifact.id,
        score=artifact.score,
        verdict=artifact.data.get("scorecard", {}).get("verdict"),
    )


def summaries(session: SessionDep, jobs: list[Job]) -> list[JobSummary]:
    latest = artifact_store.latest_evaluations(session, [job.id for job in jobs])
    alive = job_store.alive_ids(session, {job.similar_to for job in jobs if job.similar_to})
    stage_on = job_store.stage_dates(session, jobs)
    return [
        JobSummary.model_validate(job).model_copy(
            update={
                "evaluation": _brief(latest[job.id]) if job.id in latest else None,
                "prep": job_store.prep_of(job, job.id in latest),
                "similar_to": job.similar_to if job.similar_to in alive else None,
                "stage_on": stage_on.get(job.id),
            }
        )
        for job in jobs
    ]


def _detail(session: SessionDep, job: Job) -> JobDetail:
    summary = summaries(session, [job])[0].model_dump()
    return JobDetail(
        **summary,
        jd_text=job.jd_text,
        company_size=job.company_size,
        industry=job.industry,
        posted_at=job.posted_at,
        events=job_store.list_events(session, job.id),
    )


def _not_found(job_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"岗位 {job_id} 不存在")


@router.post("", response_model=JobSummary, status_code=status.HTTP_201_CREATED)
def add_job(body: JobCreate, session: SessionDep, response: Response):
    """添加岗位。同一链接或同一份 JD 已存在时返回 200 和已有岗位，不重复新增。"""
    job, created = job_store.create_or_get_job(session, **body.model_dump())
    if not created:
        response.status_code = status.HTTP_200_OK
    return summaries(session, [job])[0]


@router.get("", response_model=JobList)
def list_jobs(
    session: SessionDep,
    stage: JobStage | None = None,
    category: Annotated[
        JobCategory | None, Query(description="按类别筛选：未开始、进行中、已结束")
    ] = None,
    q: Annotated[str | None, Query(description="按公司或岗位名称搜索")] = None,
    sort: Annotated[
        job_store.JobSort, Query(description="recent 最近添加的在前；score 评分高的在前")
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items, total = job_store.list_jobs(
        session,
        stage=stage,
        category=category,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return JobList(items=summaries(session, items), total=total)


@router.get("/overview", response_model=JobOverview)
def jobs_overview(session: SessionDep):
    """全部岗位的进展总览：未开始、进行中、已结束各有多少，每个阶段各有多少。"""
    stages = job_store.count_by_stage(session)
    categories = dict.fromkeys(JobCategory, 0)
    for stage, count in stages.items():
        categories[STAGE_CATEGORY[stage]] += count
    return JobOverview(total=sum(stages.values()), categories=categories, stages=stages)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: SessionDep):
    try:
        return _detail(session, job_store.get_job(session, job_id))
    except job_store.JobNotFound:
        raise _not_found(job_id) from None


@router.patch("/{job_id}", response_model=JobDetail)
def update_job(job_id: int, body: JobUpdate, session: SessionDep):
    """修改岗位信息。补上 JD 后，处理进度从「JD 不完整」变成「待评估」。"""
    try:
        job = job_store.update_job(session, job_id, body.model_dump(exclude_none=True))
    except job_store.JobNotFound:
        raise _not_found(job_id) from None
    except job_store.UrlTaken as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return _detail(session, job)


@router.post("/{job_id}/stage", response_model=JobDetail)
def change_stage(job_id: int, body: StageChange, session: SessionDep):
    try:
        job = job_store.change_stage(
            session, job_id, body.stage, note=body.note, happened_on=body.happened_on
        )
    except job_store.JobNotFound:
        raise _not_found(job_id) from None
    return _detail(session, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, session: SessionDep):
    """软删除：只打删除标记，列表不再显示。重新添加同一岗位会恢复。"""
    try:
        job_store.soft_delete_job(session, job_id)
    except job_store.JobNotFound:
        raise _not_found(job_id) from None
