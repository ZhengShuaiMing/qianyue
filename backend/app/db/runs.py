"""运行（一次 AI 任务）的读写。进度、失败、取消都从 runs 表读。"""

import json

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.db.models import Run, RunKind, RunStatus, now_iso

FINISHED = (RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled)
# 这些输入都一样，就算同一件事。后四个只有采集有。
SAME_TASK_KEYS = ("job_ids", "conditions", "searches", "filters", "evaluate")


def create_run(session: Session, kind: RunKind, run_input: dict) -> Run:
    """登记一次运行。只针对一个岗位时写上 job_id，方便按岗位查。"""
    job_ids = run_input["job_ids"]
    run = Run(
        kind=kind,
        job_id=job_ids[0] if len(job_ids) == 1 else None,
        input_json=json.dumps(run_input, ensure_ascii=False),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def find_active(session: Session, kind: RunKind, run_input: dict) -> Run | None:
    """同样的任务（同类型、同岗位、同要求）里，还在进行或在等用户处理的那一次。

    发起运行前先查它：重复点击、Agent 重复调用工具，都不会让同一件事同时跑两遍。
    """
    statement = (
        select(Run)
        .where(Run.kind == kind, col(Run.status).in_([RunStatus.running, RunStatus.waiting_user]))
        .order_by(col(Run.created_at).desc())
    )
    for run in session.exec(statement).all():
        existing = json.loads(run.input_json)
        if all(existing.get(key) == run_input.get(key) for key in SAME_TASK_KEYS):
            return run
    return None


def find_follow_up(session: Session, run_id: str) -> Run | None:
    """接着这次运行自动发起的那一次（采集选了「采完就评估」时的评估）。没有就是 None。"""
    statement = (
        select(Run)
        .where(Run.kind == RunKind.evaluation)
        .order_by(col(Run.created_at).desc(), col(Run.id).desc())
        .limit(50)
    )
    for run in session.exec(statement).all():
        if json.loads(run.input_json).get("after") == run_id:
            return run
    return None


def has_active(session: Session, kind: RunKind) -> bool:
    """这种运行现在有没有在进行的。"""
    statement = select(Run).where(
        Run.kind == kind, col(Run.status).in_([RunStatus.running, RunStatus.waiting_user])
    )
    return session.exec(statement).first() is not None


def list_runs(
    session: Session,
    *,
    status: RunStatus | None,
    limit: int,
    offset: int,
    job_id: int | None = None,
) -> tuple[list[Run], int]:
    conditions = [Run.status == status] if status else []
    if job_id is not None:
        conditions.append(Run.job_id == job_id)
    total = session.exec(select(func.count()).select_from(Run).where(*conditions)).one()
    runs = session.exec(
        select(Run)
        .where(*conditions)
        .order_by(col(Run.created_at).desc(), col(Run.id).desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(runs), total


def update_run(session: Session, run_id: str, **changes) -> Run:
    run = session.get(Run, run_id)
    for field, value in changes.items():
        setattr(run, field, value)
    run.updated_at = now_iso()
    run.finished_at = run.updated_at if run.status in FINISHED else None
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


INTERRUPTED = "服务停止时这个运行还没有结束，可以从断点继续"


def fail_interrupted(session: Session) -> int:
    """服务启动时调用：上次没跑完的运行不会自己继续，标为失败并提示可以恢复。返回处理的数量。"""
    runs = session.exec(select(Run).where(Run.status == RunStatus.running)).all()
    for run in runs:
        update_run(session, run.id, status=RunStatus.failed, error=INTERRUPTED)
    return len(runs)
