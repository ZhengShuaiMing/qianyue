"""AI 产出（成果）的读写。正文在 content_md，结构化结果在 data_json。"""

import json

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.db.models import Artifact, ArtifactKind, now_iso


def find_by_input(
    session: Session, kind: ArtifactKind, job_id: int | None, input_hash: str
) -> Artifact | None:
    """同一岗位、同样输入的成果。有就不必再生成一次（幂等）。"""
    statement = select(Artifact).where(
        Artifact.kind == kind,
        Artifact.job_id == job_id,
        Artifact.input_hash == input_hash,
        col(Artifact.deleted_at).is_(None),
    )
    return session.exec(statement).first()


def latest_artifact(session: Session, kind: ArtifactKind, job_id: int) -> Artifact | None:
    """这个岗位最近一份某种类型的成果。"""
    statement = (
        select(Artifact)
        .where(
            Artifact.kind == kind,
            Artifact.job_id == job_id,
            col(Artifact.deleted_at).is_(None),
        )
        .order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc())
    )
    return session.exec(statement).first()


def latest_evaluation(session: Session, job_id: int) -> Artifact | None:
    """这个岗位最近一次的评估报告。"""
    return latest_artifact(session, ArtifactKind.evaluation, job_id)


def latest_evaluations(session: Session, job_ids: list[int]) -> dict[int, Artifact]:
    """一批岗位各自最近一次的评估报告，按岗位编号查。没评估过的岗位不在结果里。"""
    if not job_ids:
        return {}
    statement = (
        select(Artifact)
        .where(
            Artifact.kind == ArtifactKind.evaluation,
            col(Artifact.job_id).in_(job_ids),
            col(Artifact.deleted_at).is_(None),
        )
        .order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc())
    )
    latest: dict[int, Artifact] = {}
    for artifact in session.exec(statement):
        latest.setdefault(artifact.job_id, artifact)
    return latest


def _upsert(
    session: Session,
    *,
    kind: ArtifactKind,
    job_id: int | None,
    run_id: str,
    input_hash: str,
    title: str,
    content_md: str,
    data: dict,
    score: float | None = None,
) -> Artifact:
    """写入成果但不提交。同样输入已有成果时覆盖它（重做），所以重复执行不会多出一份。"""
    artifact = find_by_input(session, kind, job_id, input_hash) or Artifact(
        kind=kind, job_id=job_id, input_hash=input_hash
    )
    artifact.run_id = run_id
    artifact.title = title
    artifact.content_md = content_md
    artifact.data_json = json.dumps(data, ensure_ascii=False)
    artifact.score = score
    artifact.updated_at = now_iso()
    session.add(artifact)
    return artifact


def save_artifact(session: Session, **fields) -> Artifact:
    artifact = _upsert(session, **fields)
    session.commit()
    session.refresh(artifact)
    return artifact




def list_artifacts(
    session: Session,
    *,
    kind: ArtifactKind | None,
    job_id: int | None,
    limit: int,
    offset: int,
) -> tuple[list[Artifact], int]:
    """成果列表，最近更新的在前。"""
    conditions = [col(Artifact.deleted_at).is_(None)]
    if kind:
        conditions.append(Artifact.kind == kind)
    if job_id is not None:
        conditions.append(Artifact.job_id == job_id)

    total = session.exec(select(func.count()).select_from(Artifact).where(*conditions)).one()
    artifacts = session.exec(
        select(Artifact)
        .where(*conditions)
        .order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(artifacts), total


def soft_delete_artifact(session: Session, artifact: Artifact) -> None:
    artifact.deleted_at = now_iso()
    session.add(artifact)
    session.commit()


def get_artifact(session: Session, artifact_id: int) -> Artifact | None:
    artifact = session.get(Artifact, artifact_id)
    return artifact if artifact and not artifact.deleted_at else None
