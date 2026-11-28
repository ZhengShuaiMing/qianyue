import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.api.deps import RunManagerDep, SessionDep, resolve_chat_model
from app.api.sse import SSE_HEADERS, sse
from app.collectors.scope import CollectionRequest
from app.db import jobs as job_store
from app.db import runs as run_store
from app.db.models import RUN_KIND_NAMES as KIND_NAMES
from app.db.models import Run, RunKind, RunStatus
from app.graphs import tailoring
from app.graphs.runner import CollectionBusy, RunManager, unit_count
from app.llm.factory import LLMNotConfigured

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreate(BaseModel):
    kind: RunKind = Field(
        default=RunKind.evaluation,
        description="`evaluation` 岗位评估，`tailoring` 简历定制，`company_research` 公司研究，"
        "`communication` 沟通建议，`interview_prep` 面试准备，`mock_interview` 模拟面试，"
        "`collection` 采集岗位",
    )
    job_ids: list[int] = Field(
        default_factory=list, description="岗位编号。评估可以一次提交多个，其余一次一个；采集不填"
    )
    collection: CollectionRequest | None = Field(
        default=None, description="采集的范围。只有 kind 为 `collection` 时填"
    )
    conditions: str = Field(
        default="",
        description="本次的补充要求。评估：特别在意的条件，例如城市、薪资下限；"
        "公司研究：特别想了解的方面；沟通建议：渠道、语气、想达到的目的；"
        "面试准备：第几轮、面试官是谁、哪天面；模拟面试：想让面试官扮演谁、重点练什么。"
        "定制不使用",
    )
    force: bool = Field(default=False, description="输入没变也重新做，覆盖已有成果")

    @model_validator(mode="after")
    def check_targets(self):
        if self.kind == RunKind.screenshot:
            raise ValueError("识别截图在添加岗位、添加沟通时带上截图发起")
        if self.kind == RunKind.collection:
            if self.collection is None or self.job_ids:
                raise ValueError("采集要填 collection（平台、关键词、城市），不填 job_ids")
            return self
        if self.collection is not None:
            raise ValueError("collection 只有采集岗位时才填")
        if not self.job_ids:
            raise ValueError("请给出岗位编号 job_ids")
        if self.kind == RunKind.profile:
            if not 2 <= len(set(self.job_ids)) <= 15:
                raise ValueError("能力画像要汇总 2 到 15 个岗位")
            return self
        if self.kind != RunKind.evaluation and len(set(self.job_ids)) != 1:
            raise ValueError(f"{KIND_NAMES[self.kind]}一次只能针对一个岗位")
        return self


class RunRead(BaseModel):
    id: str
    kind: RunKind
    job_ids: list[int]
    status: RunStatus
    step: str
    error: str | None
    created_at: str
    updated_at: str
    finished_at: str | None
    total: int = Field(description="一共几项：采集按搜索算，能力画像算一项，其余按岗位算")
    done: int | None = Field(description="正在进行时已经有结论的项数；不在进行时为空")
    agent: Literal["waiting", "working"] | None = Field(
        default=None,
        description="没有模型 Key、由你的 Agent 代答时：waiting 等 Agent 接手，working Agent 在做",
    )


class JobResult(BaseModel):
    """一项的结论。采集时一项是一个搜索（`search` 是它的名字，`saved` 是新增的岗位编号），
    其余时候是一个岗位。`done` 已生成成果，`reused` 复用已有成果，`blocked` 前置检查未通过，
    `failed` 模型输出始终不合格，`no_changes` 模型认为简历不需要修改，
    `declined` 你没有接受任何修改，`empty` 模拟面试一题都没答、没有复盘。"""

    job_id: int | None
    status: str
    artifact_id: int | None
    reason: str
    search: str | None = None
    saved: list[int] = Field(default_factory=list)


class RunDetail(RunRead):
    conditions: str
    force: bool
    collection: dict | None = Field(
        description="采集的范围：searches（每个搜索带着自己的数量 count）、"
        "filters（筛选条件）、evaluate。其他运行为空"
    )
    results: list[JobResult]
    pending: dict | None = Field(
        description="状态为 waiting_user 时，等你处理的内容。定制简历时是修改方案和预览，"
        "模拟面试时是当前的问题和此前的问答"
    )


class RunResponse(BaseModel):
    accepted: list[int] = Field(
        description="接受的修改编号（方案里每条修改的 number）。空列表表示全部不接受"
    )
    overrides: dict[int, str] = Field(
        default_factory=dict,
        description="你自己改过的新内容：修改编号 → 你写的文字。只能是接受了的替换、插入修改。"
        "你写的文字按原样用，不再核对材料",
    )


class TailoringPreview(BaseModel):
    preview_md: str


class RunList(BaseModel):
    items: list[RunRead]
    total: int


def read_run(run: Run, manager: RunManager) -> RunRead:
    run_input = json.loads(run.input_json)
    return RunRead(
        **run.model_dump(),
        job_ids=run_input["job_ids"],
        total=unit_count(run_input),
        done=manager.done_count(run.id) if run.status == RunStatus.running else None,
        agent=manager.agent.state(run.id) if run.status == RunStatus.running else None,
    )


def _model(request: Request):
    try:
        return resolve_chat_model(request.app)
    except LLMNotConfigured as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None


def _optional_model(request: Request):
    """没有配置 Key 时返回 None，不报错。"""
    try:
        return resolve_chat_model(request.app)
    except LLMNotConfigured:
        return None


def _get(session: SessionDep, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"任务 {run_id} 不存在")
    return run


@router.post("", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: RunCreate, request: Request, session: SessionDep, manager: RunManagerDep):
    """发起一次运行，立即返回运行编号。工作在后台进行，用 `GET /api/runs/{id}/events` 看进度。
    同样的任务（同类型、同岗位、同要求）还在进行或在等你处理时，返回那一次，不重复发起；
    加 `force` 才会新开。定制简历会在生成修改方案后停下（状态 `waiting_user`），等你用
    `respond` 确认。
    模拟面试在每个问题后停下，用 WebSocket `/api/runs/{id}/interview` 作答。"""
    job_ids = list(dict.fromkeys(body.job_ids))  # 去重，保持顺序
    for job_id in job_ids:
        try:
            job_store.get_job(session, job_id)
        except job_store.JobNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"岗位 {job_id} 不存在") from None

    if body.kind == RunKind.collection:
        # 采集本身不调用模型，没有配置 Key 也能用。有 Key 时多两样：跳过和关键词明显无关的岗位，
        # 以及采完自动评估。
        model = _optional_model(request)
        if body.collection.evaluate and model is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "采集完成后自动评估需要模型 Key。请先在设置页填写，"
                "或取消勾选「采集完成后自动评估新增的岗位」",
            )
        try:
            run, _ = manager.submit(
                body.kind,
                job_ids=[],
                conditions="",
                force=False,
                model=model,
                extra=body.collection.run_input(),
            )
        except CollectionBusy as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
        return read_run(run, manager)

    run, _ = manager.submit(
        body.kind,
        job_ids=job_ids,
        conditions=body.conditions,
        force=body.force,
        model=_model(request),
    )
    return read_run(run, manager)


@router.get("", response_model=RunList)
def list_runs(
    session: SessionDep,
    manager: RunManagerDep,
    status: RunStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """运行列表，最新的在前。「任务」面板的四类（进行中、等你处理、已完成、失败）按 status 筛选。"""
    runs, total = run_store.list_runs(session, status=status, limit=limit, offset=offset)
    return RunList(items=[read_run(run, manager) for run in runs], total=total)


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: SessionDep, manager: RunManagerDep):
    """运行的状态，以及每个岗位的结论。"""
    run = _get(session, run_id)
    run_input = json.loads(run.input_json)
    return RunDetail(
        **read_run(run, manager).model_dump(),
        conditions=run_input["conditions"],
        force=run_input["force"],
        collection={key: run_input.get(key) for key in ("searches", "filters", "evaluate")}
        if run.kind == RunKind.collection
        else None,
        results=await manager.results(run),
        pending=await manager.pending(run) if run.status == RunStatus.waiting_user else None,
    )


@router.get("/{run_id}/events")
async def run_events(run_id: str, session: SessionDep, manager: RunManagerDep) -> StreamingResponse:
    """运行进度。返回 SSE：`node` 某个岗位进入某个节点，`tool` Agent 调用了一个工具
    （例如搜索了什么），`token` 模型逐字输出（模拟面试的提问），`progress` 一句话的进度
    （采集读到第几页、保存了几个），`job` 某个岗位或某个搜索有了结论，
    `done` 这一段执行结束（带运行状态，`waiting_user` 表示停下来等你处理）。

    连接时会先补发已经发生的事件。运行不在执行时只有一条 `done`。
    """
    run = _get(session, run_id)
    events = manager.events(run_id)

    async def stream() -> AsyncIterator[str]:
        if events is None:
            yield sse("done", {"status": run.status, "error": run.error})
            return
        async for event, data in events:
            yield sse(event, data)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(run_id: str, session: SessionDep, manager: RunManagerDep):
    """取消正在进行的运行。已经评完的岗位保留结果。"""
    run = _get(session, run_id)
    if run.status == RunStatus.waiting_user:  # 停着等确认的运行没有后台任务，直接改状态
        return read_run(run_store.update_run(session, run_id, status=RunStatus.cancelled), manager)
    if not await manager.cancel(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务已不在进行中，无法取消")
    session.refresh(run)
    return read_run(run, manager)


@router.post("/{run_id}/resume", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def resume_run(run_id: str, request: Request, session: SessionDep, manager: RunManagerDep):
    """从断点继续一个失败、被取消或被服务重启打断的运行。已经评完的岗位不会再调用模型。"""
    run = _get(session, run_id)
    if manager.is_active(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务正在进行中")
    if run.status == RunStatus.waiting_user:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务在等你确认，请先在页面上确认")
    model = _optional_model(request) if run.kind == RunKind.collection else _model(request)
    if not await manager.resume(run, model):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务没有可以继续的断点，请重新发起")
    session.refresh(run)
    return read_run(run, manager)


@router.post("/{run_id}/respond", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def respond_to_run(
    run_id: str, body: RunResponse, session: SessionDep, manager: RunManagerDep
):
    """确认一个等你处理的运行。定制简历时提交你接受的修改编号（可以带上你改过的新内容），
    系统只应用这些修改，生成定制简历。基础简历不会被改动。"""
    run = _get(session, run_id)
    if run.kind != RunKind.tailoring:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{KIND_NAMES[RunKind(run.kind)]}不用这个接口确认。"
            "模拟面试请用 WebSocket /api/runs/{id}/interview 作答",
        )
    if run.status != RunStatus.waiting_user or manager.is_active(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务没有在等你确认")
    pending = await manager.pending(run)
    kinds = {edit["number"]: edit["kind"] for edit in pending["edits"]}
    try:
        answer = tailoring.check_choice(body.accepted, body.overrides, kinds)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if not manager.respond(run, answer):
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务没有在等你确认")
    session.refresh(run)
    return read_run(run, manager)


@router.post("/{run_id}/preview", response_model=TailoringPreview)
async def preview_tailoring(
    run_id: str, body: RunResponse, session: SessionDep, manager: RunManagerDep
):
    """定制简历确认前的预览：只应用你勾选的这几条修改后，简历会是什么样。不保存任何东西。"""
    run = _get(session, run_id)
    if run.kind != RunKind.tailoring or run.status != RunStatus.waiting_user:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个任务没有在等你确认定制修改")
    values = await manager.values(run)
    try:
        preview = tailoring.preview_choice(values, body.accepted, body.overrides)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    return TailoringPreview(preview_md=preview)
