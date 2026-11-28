import io
import json
import zipfile
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.db import artifacts as artifact_store
from app.db.models import ARTIFACT_KIND_NAMES, Artifact, ArtifactKind
from app.llm.evaluation import DIMENSIONS, HOW_TO_SCORE

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class ArtifactSummary(BaseModel):
    """列表用：不带正文。"""

    id: int
    kind: ArtifactKind
    job_id: int | None
    run_id: str | None
    title: str
    score: float | None
    created_at: str
    updated_at: str


class DimensionInfo(BaseModel):
    key: str
    name: str
    weight: int = Field(description="权重，百分比")
    how_to_score: str | None = Field(
        description="这一维不可评估时，补上什么信息就能计分；只有部分维度有"
    )


class ArtifactDetail(ArtifactSummary):
    content_md: str
    data: dict | None
    dimensions: list[DimensionInfo] | None = Field(
        default=None, description="评估报告才有：八个维度的名称和权重，顺序和 data 里的一致"
    )


class ArtifactList(BaseModel):
    items: list[ArtifactSummary]
    total: int


def _get(session: SessionDep, artifact_id: int) -> Artifact:
    artifact = artifact_store.get_artifact(session, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"成果 {artifact_id} 不存在")
    return artifact


@router.get("", response_model=ArtifactList)
def list_artifacts(
    session: SessionDep,
    kind: ArtifactKind | None = None,
    job_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """全部 AI 产出，最近更新的在前。可以按类型和岗位筛选。"""
    items, total = artifact_store.list_artifacts(
        session, kind=kind, job_id=job_id, limit=limit, offset=offset
    )
    return ArtifactList(items=[ArtifactSummary(**item.model_dump()) for item in items], total=total)


@router.get("/export")
def export_artifacts(
    session: SessionDep,
    kind: Annotated[ArtifactKind | None, Query(description="只导出这一类")] = None,
    job_id: Annotated[int | None, Query(description="只导出这个岗位的成果")] = None,
) -> Response:
    """把成果一次导出成一个 zip，里面每份成果一个 Markdown 文件，按类型分文件夹。"""
    artifacts, total = artifact_store.list_artifacts(
        session, kind=kind, job_id=job_id, limit=100_000, offset=0
    )
    if not total:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有可以导出的成果")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            folder = ARTIFACT_KIND_NAMES.get(artifact.kind, artifact.kind)
            name = (artifact.title or "成果").replace("/", "-")
            archive.writestr(f"{folder}/{artifact.id}-{name}.md", artifact.content_md)
    label = ARTIFACT_KIND_NAMES.get(kind, "全部成果") if kind else "全部成果"
    if job_id is not None:
        label = f"岗位{job_id}-{label}"
    filename = quote(f"迁跃-{label}-{date.today().isoformat()}.zip")
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: int, session: SessionDep):
    """成果详情。`content_md` 是给人看的正文，`data` 是结构化结果（评估报告含各维得分）。"""
    artifact = _get(session, artifact_id)
    data = json.loads(artifact.data_json) if artifact.data_json else None
    dimensions = (
        [
            DimensionInfo(
                key=item.key,
                name=item.name,
                weight=item.weight,
                how_to_score=HOW_TO_SCORE.get(item.key),
            )
            for item in DIMENSIONS
        ]
        if artifact.kind == ArtifactKind.evaluation
        else None
    )
    return ArtifactDetail(**artifact.model_dump(), data=data, dimensions=dimensions)


@router.get("/{artifact_id}/export")
def export_artifact(artifact_id: int, session: SessionDep) -> Response:
    """把正文导出为 Markdown 文件。"""
    artifact = _get(session, artifact_id)
    filename = quote(f"{artifact.title or f'成果-{artifact.id}'}.md".replace("/", "-"))
    return Response(
        artifact.content_md,
        media_type="text/markdown; charset=utf-8",
        # 文件名含中文，按 RFC 5987 编码后浏览器才能正确显示。
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artifact(artifact_id: int, session: SessionDep):
    """删除一份成果。删除后列表和详情里都看不到，也不会再被复用：
    例如删掉评估报告后再评估这个岗位，会重新评一次。"""
    artifact_store.soft_delete_artifact(session, _get(session, artifact_id))
