from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator

from app.api.deps import SessionDep
from app.db import documents as document_store
from app.db.models import DOCUMENT_KIND_NAMES, DocumentKind

router = APIRouter(prefix="/documents", tags=["documents"])

class DocumentSave(BaseModel):
    content_md: str
    title: str = ""

    @field_validator("content_md")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value


class DocumentVersion(BaseModel):
    """版本列表用：不带正文。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: DocumentKind
    title: str
    content_hash: str
    is_current: bool
    created_at: str


class DocumentRead(DocumentVersion):
    content_md: str


@router.put("/{kind}", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def save_document(kind: DocumentKind, body: DocumentSave, session: SessionDep, response: Response):
    """保存材料。每次保存生成一个新版本；内容没变时返回 200 和当前版本，不新增。

    `kind` 取 `resume`（基础简历，评估必需）、`supplement`（补充资料，选填：求职方向、
    到岗时间、作品和证书）或 `star`（STAR 经历，选填：用二级标题分隔每段经历，定制简历时
    会从里面检索证据）。
    """
    document, created = document_store.save_document(session, kind, body.content_md, body.title)
    if not created:
        response.status_code = status.HTTP_200_OK
    return document


@router.get("/{kind}", response_model=DocumentRead)
def get_current_document(kind: DocumentKind, session: SessionDep):
    document = document_store.get_current(session, kind)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"还没有保存{DOCUMENT_KIND_NAMES[kind]}")
    return document


@router.get("/{kind}/versions", response_model=list[DocumentVersion])
def list_document_versions(kind: DocumentKind, session: SessionDep):
    """全部历史版本，最新的在前。"""
    return document_store.list_versions(session, kind)


@router.get("/{kind}/versions/{document_id}", response_model=DocumentRead)
def get_document_version(kind: DocumentKind, document_id: int, session: SessionDep):
    document = document_store.get_version(session, kind, document_id)
    if document is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{DOCUMENT_KIND_NAMES[kind]}没有版本 {document_id}"
        )
    return document
