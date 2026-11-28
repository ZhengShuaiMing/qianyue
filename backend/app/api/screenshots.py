"""截图录入：添加岗位、添加沟通时带上截图，保存时一次交给模型读（模型要支持图片输入），
读出的文字和你在截图下面打的字一起存下来。没有单独的「先读一遍」。

没有配置模型 Key 时，截图先存进迁跃，发起一次「识别截图」运行（返回 202 和运行），由你的 Agent
经 MCP 读图交回，校验和保存与有 Key 时相同（app/graphs/screenshot.py）。
助手发的截图在没有 Key 时也先存进来（POST /uploads），由你的 Agent 用「看截图」工具读。"""

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, field_validator

from app.api.communications import CommunicationRead, require_job
from app.api.deps import RunManagerDep, SessionDep, resolve_mcp_model
from app.api.jobs import JobSummary, summaries
from app.api.runs import RunRead, read_run
from app.core import uploads
from app.db.models import RunKind, Speaker
from app.graphs.screenshot import save_communication, save_job
from app.llm.agent_model import AgentChatModel
from app.llm.parsing import InvalidOutput
from app.llm.screenshot import ImagesRejected, check_images, read_screenshots

router = APIRouter(tags=["截图录入"])

IMAGES = "截图，data:image/png;base64,... 的形式，按顺序。张数和大小不限，模型收不了时会说明"


class JobFromScreenshots(BaseModel):
    images: list[str] = Field(min_length=1, description=IMAGES)
    jd_text: str = Field(default="", description="你在截图下面打的字，接在截图读出的 JD 后面")
    url: str | None = None
    company: str = Field(default="", description="填了就用你填的，没填用截图里读出的")
    title: str = ""
    city: str | None = None
    salary: str | None = None

    @field_validator("images")
    @classmethod
    def real_images(cls, images: list[str]) -> list[str]:
        return check_images(images)


class CommunicationFromScreenshots(BaseModel):
    images: list[str] = Field(min_length=1, description=IMAGES)
    text: str = Field(default="", description="你在截图下面打的字，接在聊天截图后面")
    speaker: Speaker = Field(
        default=Speaker.transcript,
        description="打的字是谁说的。transcript 表示原样接在后面",
    )

    @field_validator("images")
    @classmethod
    def real_images(cls, images: list[str]) -> list[str]:
        return check_images(images)


class Upload(BaseModel):
    images: list[str] = Field(min_length=1, description=IMAGES)

    @field_validator("images")
    @classmethod
    def real_images(cls, images: list[str]) -> list[str]:
        return check_images(images)


class Uploaded(BaseModel):
    id: str = Field(description="截图编号。你的 Agent 用迁跃的「看截图」工具按这个编号读图")
    count: int


AGENT_RUN = {
    202: {"model": RunRead, "description": "没有模型 Key：截图已存下，等你的 Agent 读图后保存"}
}


def _agent_run(
    request: Request, manager, target: str, images: list[str], job_ids: list[int], fields: dict
) -> JSONResponse | None:
    """没有 Key 时把截图存下，发起识别截图运行，返回 202；有 Key 返回 None，照常当场读。"""
    model = resolve_mcp_model(request.app)
    if not isinstance(model, AgentChatModel):
        return None
    upload = uploads.save(request.app.state.settings.data_dir, images)
    run, _ = manager.submit(
        RunKind.screenshot,
        job_ids=job_ids,
        conditions="",
        force=True,  # 每次上传的截图都不同，不和别的运行合并
        model=model,
        extra={"upload": upload, "target": target, "fields": fields},
    )
    body = read_run(run, manager).model_dump(mode="json")
    return JSONResponse(body, status_code=status.HTTP_202_ACCEPTED)


@router.post("/uploads", response_model=Uploaded, status_code=201)
async def upload_screenshots(body: Upload, request: Request):
    """存下几张截图，返回编号，7 天后自动删除。没有模型 Key 时助手发的截图走这里，
    交给你的 Agent 去看。"""
    upload_id = uploads.save(request.app.state.settings.data_dir, body.images)
    return Uploaded(id=upload_id, count=len(body.images))


async def read(model: BaseChatModel, task: str, images: list[str]):
    """读截图；模型不收图片、没有回应、连续两次答不成格式时，转成接口错误。"""
    try:
        return await read_screenshots(model, task, images)
    except ImagesRejected as error:
        code = (
            status.HTTP_422_UNPROCESSABLE_CONTENT
            if error.unsupported
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(code, str(error)) from None
    except InvalidOutput as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None


@router.post(
    "/jobs/from-screenshots", response_model=JobSummary, status_code=201, responses=AGENT_RUN
)
async def add_job_from_screenshots(
    body: JobFromScreenshots,
    request: Request,
    session: SessionDep,
    manager: RunManagerDep,
    response: Response,
):
    """带截图添加岗位：模型读出公司、职位、城市、薪资和 JD，你填了的项以你填的为准，
    截图下面打的字接在 JD 后面。同一链接或同一份 JD 已存在时返回 200 和已有岗位。
    没有模型 Key 时返回 202 和一次识别截图运行，由你的 Agent 读图后保存。"""
    fields = body.model_dump(exclude={"images"})
    handed = _agent_run(request, manager, "job", body.images, [], fields)
    if handed:
        return handed
    job = await read(resolve_mcp_model(request.app), "job", body.images)
    try:
        saved, created = save_job(session, job, fields)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if not created:
        response.status_code = status.HTTP_200_OK
    return summaries(session, [saved])[0]


@router.post(
    "/jobs/{job_id}/communications/from-screenshots",
    response_model=CommunicationRead,
    status_code=201,
    responses=AGENT_RUN,
)
async def add_communication_from_screenshots(
    job_id: int,
    body: CommunicationFromScreenshots,
    request: Request,
    session: SessionDep,
    manager: RunManagerDep,
    response: Response,
):
    """带聊天截图添加沟通记录：截图里只有一条消息就按谁说的存，几条存成一整段聊天；
    截图下面打的字接在后面。同样的内容重复保存时返回 200 和已有记录。
    没有模型 Key 时返回 202 和一次识别截图运行，由你的 Agent 读图后保存。"""
    require_job(session, job_id)
    fields = body.model_dump(mode="json", exclude={"images"})
    handed = _agent_run(request, manager, "communication", body.images, [job_id], fields)
    if handed:
        return handed
    chat = await read(resolve_mcp_model(request.app), "communication", body.images)
    try:
        record, created = save_communication(session, job_id, chat, fields)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if not created:
        response.status_code = status.HTTP_200_OK
    return record
