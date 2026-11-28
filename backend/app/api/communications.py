from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator

from app.api.deps import SessionDep
from app.db import communications as communication_store
from app.db import jobs as job_store
from app.db.models import Speaker

router = APIRouter(prefix="/jobs/{job_id}/communications", tags=["communications"])


class CommunicationCreate(BaseModel):
    speaker: Speaker
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value


class CommunicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    speaker: Speaker
    text: str
    created_at: str


def require_job(session: SessionDep, job_id: int) -> None:
    try:
        job_store.get_job(session, job_id)
    except job_store.JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"岗位 {job_id} 不存在") from None


@router.post("", response_model=CommunicationRead, status_code=status.HTTP_201_CREATED)
def add_communication(
    job_id: int, body: CommunicationCreate, session: SessionDep, response: Response
):
    """保存一条招聘沟通内容。只是本地记录，不会发给任何人，也不会改变岗位阶段。

    `speaker`：`recruiter` 招聘方说的，`me` 我说的，`transcript` 原样粘贴的一整段聊天。
    同样的内容重复保存时返回 200 和已有记录。
    """
    require_job(session, job_id)
    record, created = communication_store.add_communication(
        session, job_id, body.speaker, body.text
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return record


@router.get("", response_model=list[CommunicationRead])
def list_communications(job_id: int, session: SessionDep):
    """这个岗位的全部沟通记录，按保存的先后排列。"""
    require_job(session, job_id)
    return communication_store.list_communications(session, job_id)


@router.delete("/{communication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_communication(job_id: int, communication_id: int, session: SessionDep):
    require_job(session, job_id)
    if not communication_store.delete_communication(session, job_id, communication_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"沟通记录 {communication_id} 不存在")
