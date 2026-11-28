"""迁跃的 MCP Server：把迁跃的能力按标准协议交给用户自己的 Agent（Claude Code、Cursor 等）。

MCP 里有两类东西：
- Tool：Agent 可以调用的动作，例如保存岗位、发起评估。
- Resource：Agent 可以按地址读取的数据，例如 qianyue://jobs/3 是 3 号岗位的详情。

这里的工具和助手的工具调用同一批操作（app/tools/operations.py）。区别是对面的 Agent 不归
迁跃管：它传来的 JD 和聊天内容没法和用户的原话核对，改阶段前要不要问用户也由它的宿主决定。
所以工具说明里写清楚了这些要求，并给每个工具标了「只读」「幂等」，方便宿主决定要不要先问用户。

改材料（基础简历、补充资料、STAR 经历）不交给对面的 Agent 自己决定：保存前迁跃通过 MCP 的
elicitation（服务端请客户端弹出确认框）把全文直接给用户看，用户点了接受才保存。客户端不支持
elicitation 时不保存，告诉用户换到迁跃的助手里做。确认框用 SDK 的 Resolve：新旧两版协议
（2026-07-28 起改成「先返回要用户回答的问题，客户端带着答案重新调用」）都由 SDK 处理。
"""

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlencode

from langchain_core.language_models import BaseChatModel
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from sqlmodel import Session
from starlette.requests import Request

from app.api.artifacts import ArtifactList, ArtifactSummary
from app.api.communications import CommunicationRead
from app.api.jobs import JobUpdate
from app.api.overview import Overview
from app.api.runs import RunList, read_run
from app.core import uploads
from app.db import artifacts as artifact_store
from app.db import communications as communication_store
from app.db import documents as document_store
from app.db import jobs as job_store
from app.db import runs as run_store
from app.db.models import (
    DOCUMENT_KIND_NAMES,
    RUN_KIND_NAMES,
    ArtifactKind,
    DocumentKind,
    Run,
    RunKind,
    RunStatus,
)
from app.db.overview import build_overview
from app.graphs.tailoring import preview_choice
from app.llm.agent_model import AgentChatModel
from app.llm.facts import unsupported
from app.llm.mock_interview import interview_answer
from app.llm.star import PARTS, render_entry
from app.mcp_server.handoff import Reply, image_files, request_reply
from app.tools.operations import Workspace

INSTRUCTIONS = """迁跃是用户本地的求职工具。通过这些工具可以查看用户的岗位和成果、
保存岗位和招聘沟通内容、发起岗位评估等任务并取回结论，还可以到招聘平台上搜索岗位并保存。

使用时请注意：
- 岗位信息、评分和报告结论以工具返回的内容为准，不要补充工具没有返回的内容。
- 保存 JD 和聊天内容时传用户提供的原文，不要改写或总结。
- 只有用户明确说了进展（投了、面完了、被拒了）才修改岗位阶段，改之前先向用户确认。
- 迁跃不替用户投递，也不替用户发消息。沟通建议里的草稿由用户自己发送。
- 定制简历的修改要由用户逐条决定，不要替用户确认。
- 模拟面试每次原样展示迁跃返回的一题，等用户回答后提交原话，不替用户作答或结束面试。
- 和用户一起改简历、补充资料或整理 STAR 经历时，只写用户说过的事实和原来材料里有的内容。
  保存时迁跃会直接请用户确认全文。
- 采集岗位的平台、关键词和城市要用户明确说过。
- 平台要求登录或验证时，让用户自己在专用浏览器里处理。

用户没有给迁跃配置模型 Key 时，评估、定制简历、公司研究等任务由你来替迁跃的模型作答：
发起任务后返回的是「迁跃请你代答」的请求，照要求写好用 answer_model_request 交回，迁跃校验、
保存后返回下一个请求或最终结论。一直循环到最终结论，中间不用问用户，也不用转述请求内容。
迁跃会把结果存好，用户在迁跃里也能看到。

用户从迁跃复制来的话里有运行编号时（例如识别他上传的岗位或聊天截图），用 get_task 接手；
写了截图编号时，用 view_screenshots 看图。"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
STARTS_WORK = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

Task = Literal[
    "evaluation", "tailoring", "company_research", "communication", "interview_prep", "profile"
]
Stage = Literal[
    "not_applied",
    "applied",
    "assessment",
    "interviewed",
    "offer",
    "rejected",
    "withdrawn",
]
Platform = Literal["51job", "liepin", "zhaopin", "boss"]
MAX_WAIT_SECONDS = 600


class Confirm(BaseModel):
    """确认框只要用户点接受或拒绝，不用填东西。"""


class InterviewState(BaseModel):
    run_id: str | None = None
    status: RunStatus | None = None
    pending: dict | None = None  # 当前的 turn、question、total 和此前的 transcript
    artifact_id: int | None = None
    reason: str | None = None
    error: str | None = None
    # 没有模型 Key 时，面试官的提问和复盘由你代答：这里是迁跃请你完成的一步，照做后用
    # answer_model_request 交回
    model_request: str | None = None


class ToolCall(BaseModel):
    """代答时请迁跃执行的一个工具。"""

    name: str
    args: dict = Field(default_factory=dict)


class Communications(BaseModel):
    items: list[CommunicationRead] = Field(default_factory=list)
    error: str | None = None


def build_mcp_server(
    *, workspace: Callable[[], Workspace], model: Callable[[], BaseChatModel]
) -> MCPServer:
    """workspace 和 model 在每次调用时才取：后端启动完成后它们才存在。"""
    # log_level：SDK 创建时会设置全局日志级别。默认的 INFO 会让其他库（例如每次调用模型的
    # 请求记录）都打到终端，这里只要警告以上的。
    mcp = MCPServer("qianyue", title="迁跃 Qianyue", instructions=INSTRUCTIONS, log_level="WARNING")

    # ---------- Resource：按地址读取的数据 ----------

    @mcp.resource(
        "qianyue://overview",
        name="进展总览",
        description="全部岗位的列表、各阶段的数量，以及进行中或等用户处理的任务",
        mime_type="text/plain",
    )
    def overview() -> str:
        return workspace().jobs_overview()

    @mcp.resource(
        "qianyue://jobs/{job_id}",
        name="岗位",
        description="一个岗位的详情：基本信息、当前阶段、完整 JD、已有的成果和相关任务",
        mime_type="text/plain",
    )
    def job(job_id: int) -> str:
        return workspace().job_detail(job_id, jd_chars=None)

    @mcp.resource(
        "qianyue://artifacts/{artifact_id}",
        name="成果",
        description="一份成果的完整正文：评估报告、公司研究、沟通建议、定制简历、面试准备或模拟面试复盘",
        mime_type="text/markdown",
    )
    def artifact(artifact_id: int) -> str:
        return workspace().artifact_text(artifact_id, limit=None)

    @mcp.resource(
        "qianyue://documents/{kind}",
        name="我的材料",
        description="用户当前的材料。kind：resume 基础简历，supplement 补充资料，star STAR 经历",
        mime_type="text/markdown",
    )
    def document(kind: str) -> str:
        if kind not in DocumentKind.__members__:
            return "kind 只能是 resume、supplement 或 star。"
        with Session(workspace().engine) as session:
            current = document_store.get_current(session, DocumentKind(kind))
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        return current.content_md if current else f"用户还没有保存{name}。"

    # ---------- Tool：读取 ----------

    @mcp.tool(title="全局检索", annotations=READ_ONLY)
    def search_workspace(
        query: str,
        types: list[Literal["material", "job", "communication", "artifact", "chat"]] | None = None,
    ) -> str:
        """在迁跃存的全部内容里检索：用户的材料、岗位（含 JD）、沟通记录、全部成果、助手对话。
        按意思和字面一起找，返回片段和出处（岗位编号、成果编号）。types 只查某几类，不填查全部。
        岗位 JD 和成果不是用户的经历，不要当成他做过的事。"""
        return workspace().search_workspace(query, types)

    @mcp.tool(title="看岗位列表", annotations=READ_ONLY)
    def list_jobs(
        keyword: str = "",
        category: Literal["", "not_started", "in_progress", "closed"] = "",
    ) -> str:
        """查看岗位列表和整体进展。keyword 按公司或职位名筛选；category 按类别筛选：
        not_started 未开始（还没投），in_progress 进行中（投了还没结果），closed 已结束。
        都不填就是全部。
        返回每个岗位的编号、公司、职位、应聘阶段、处理进度（JD 不完整、待评估、已评估和评分），
        以及正在进行或等用户处理的任务。"""
        return workspace().jobs_overview(keyword, category)

    @mcp.tool(title="看一个岗位", annotations=READ_ONLY)
    def get_job(job_id: int) -> str:
        """查看一个岗位的详情：基本信息、当前阶段、完整 JD、已有的成果、沟通记录条数和相关任务。"""
        return workspace().job_detail(job_id, jd_chars=None)

    @mcp.tool(title="读取岗位沟通记录", annotations=READ_ONLY)
    def list_communications(job_id: int) -> Communications:
        """读取一个岗位的完整沟通原文及记录时间，按保存顺序排列。"""
        with Session(workspace().engine) as session:
            try:
                job_store.get_job(session, job_id)
            except job_store.JobNotFound:
                return Communications(error=f"没有编号为 {job_id} 的岗位。")
            records = communication_store.list_communications(session, job_id)
            items = [CommunicationRead.model_validate(item) for item in records]
            return Communications(items=items)

    @mcp.tool(title="读成果", annotations=READ_ONLY)
    def read_artifact(artifact_id: int) -> str:
        """读取一份成果的完整正文（评估报告、公司研究、沟通建议、定制简历、面试准备、模拟面试复盘）。
        成果编号从 get_job、run_task 或 get_task 的结果里拿。"""
        return workspace().artifact_text(artifact_id, limit=None)

    @mcp.tool(title="筛选成果", annotations=READ_ONLY)
    def list_artifacts(
        kind: ArtifactKind | None = None,
        job_id: int | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> ArtifactList:
        """分页查看成果，最近更新的在前。可按类型和岗位筛选；正文用 read_artifact 读取。"""
        with Session(workspace().engine) as session:
            items, total = artifact_store.list_artifacts(
                session, kind=kind, job_id=job_id, limit=limit, offset=offset
            )
            return ArtifactList(
                items=[ArtifactSummary(**item.model_dump()) for item in items], total=total
            )

    @mcp.tool(title="批量导出成果", annotations=READ_ONLY)
    def export_artifacts(
        ctx: Context, kind: ArtifactKind | None = None, job_id: int | None = None
    ) -> str:
        """返回本机 ZIP 下载链接，可按类型和岗位筛选。给用户打开，ZIP 中每份成果是一个 Markdown。"""
        filters = {k: v for k, v in (("kind", kind), ("job_id", job_id)) if v is not None}
        request = ctx.request_context.request
        base = str(request.base_url).rstrip("/") if isinstance(request, Request) else ""
        query = f"?{urlencode(filters)}" if filters else ""
        return f"{base}/api/artifacts/export{query}"

    @mcp.tool(title="查看工作台总览统计", annotations=READ_ONLY)
    def get_overview() -> Overview:
        """读取与工作台同口径的统计：应聘漏斗、投递和面试趋势（天、周、月）、来源对比与评分分布。"""
        with Session(workspace().engine) as session:
            return Overview.model_validate(build_overview(session, date.today()))

    # ---------- Tool：写入（都是幂等的） ----------

    @mcp.tool(title="保存岗位", annotations=IDEMPOTENT_WRITE)
    def add_job(
        jd_text: str = "",
        url: str = "",
        company: str = "",
        title: str = "",
        city: str = "",
        salary: str = "",
    ) -> str:
        """把用户提供的岗位保存到岗位列表。jd_text 传用户提供的 JD 原文，不要改写或总结；
        url 是岗位链接。两者至少有一个。company、title、city、salary 能从 JD 里直接读到就填，
        读不到留空，不要猜。同一份 JD 或同一个链接重复保存只会有一条。"""
        return workspace().add_job(
            jd_text=jd_text.strip(), url=url, company=company, title=title, city=city, salary=salary
        )

    @mcp.tool(title="编辑岗位信息", annotations=IDEMPOTENT_WRITE)
    def update_job(job_id: int, changes: JobUpdate) -> str:
        """编辑已有岗位的基本信息或补录 JD。不传的字段保持原样；JD 要传用户提供的完整原文。
        url 传空字符串会移除链接。应聘进展请用 change_stage。"""
        current = workspace()
        with Session(current.engine) as session:
            try:
                job_store.update_job(session, job_id, changes.model_dump(exclude_none=True))
            except job_store.JobNotFound:
                return f"没有编号为 {job_id} 的岗位。"
            except job_store.UrlTaken as error:
                return str(error)
        return f"已保存岗位修改。\n{current.job_detail(job_id, jd_chars=None)}"

    @mcp.tool(title="保存沟通记录", annotations=IDEMPOTENT_WRITE)
    def save_communication(
        job_id: int, speaker: Literal["recruiter", "me", "transcript"], text: str
    ) -> str:
        """把招聘聊天内容保存为这个岗位的沟通记录，沟通建议会用到。speaker：recruiter 是招聘方
        说的一条，me 是用户自己说的一条，transcript 是原样粘贴的一整段对话。text 传原文，
        不要改写。同样的内容重复保存只会有一条。"""
        return workspace().save_communication(job_id, speaker, text)

    @mcp.tool(title="修改岗位阶段", annotations=IDEMPOTENT_WRITE)
    def change_stage(job_id: int, stage: Stage, note: str = "", happened_on: str = "") -> str:
        """修改岗位的应聘阶段。只有用户明确说了进展才调用，调用前先向用户确认。
        stage：not_applied 未投递，applied 已投递，assessment 已笔试（含测评），
        interviewed 已面试（等结果也算），offer 已获 Offer，rejected 未通过（对方说不合适），
        withdrawn 放弃（用户不考虑了、拒了 Offer）。note 用一句话写明依据。
        happened_on 是这一步发生的日期（YYYY-MM-DD），用户没说就留空，按今天记。
        重复设为同一个阶段不会多记一次变更。
        「待评估」「已评估」不是阶段，是按有没有评估报告算出来的，不用改。"""
        return workspace().change_stage(job_id, stage, note, happened_on)

    # ---------- Tool：材料 ----------

    @mcp.tool(title="读材料", annotations=READ_ONLY)
    def read_material(kind: Literal["resume", "supplement", "star"]) -> str:
        """读用户材料的当前版本全文。resume 基础简历，supplement 补充资料（求职方向、期望城市
        和薪资、能否出差、到岗时间、作品和证书），star STAR 经历库。
        和用户一起改材料之前先读一遍。"""
        return workspace().read_material(kind)

    async def ask_material(ctx: Context, kind: str, content_md: str) -> Elicit[Confirm] | str:
        """保存材料前的确认框。条件不够时不问用户，直接返回不保存的原因。"""
        if len(content_md.strip()) < 20:
            return "没有保存：内容太少了。先和用户把内容聊完整。"
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        new = unsupported(content_md, workspace().current_material(kind))
        return _confirm(
            ctx, f"迁跃：把{name}存成新版本，旧版本会留着。\n\n{content_md.strip()}", new
        )

    @mcp.tool(title="保存材料", annotations=IDEMPOTENT_WRITE)
    async def save_material(
        kind: Literal["resume", "supplement"],
        content_md: str,
        answer: Annotated[ElicitationResult[Confirm], Resolve(ask_material)],
    ) -> str:
        """把和用户一起整理好的基础简历（resume）或补充资料（supplement）存成新版本，整份替换。
        content_md 是完整的 Markdown：先用 read_material 读现在的版本，在它上面改，用户没让动的
        内容原样保留。只能写用户说过的事实和原来材料里有的内容，他没说的经历、数字、技能不要补。
        保存前迁跃会直接请用户确认全文，用户拒绝就不保存。旧版本会留着，可以在材料页恢复。"""
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        refused = _refusal(answer)
        if refused is not None:
            return refused or f"用户没有确认，{name}没有保存。问问用户哪里要改，不要原样再存一次。"
        return workspace().save_material(kind, content_md.strip())

    async def ask_star(
        ctx: Context, title: str, situation: str, task: str, action: str, result: str
    ) -> Elicit[Confirm] | str:
        """保存 STAR 经历前的确认框。缺了哪部分时不问用户，直接返回原因。"""
        parts = {"situation": situation, "task": task, "action": action, "result": result}
        missing = [name for key, name in PARTS if len(parts[key].strip()) < 4]
        if not title.strip() or missing:
            lacking = "、".join((["名字"] if not title.strip() else []) + missing)
            return f"没有保存：缺少{lacking}。请先向用户问清楚，不要自己补。"
        entry = render_entry(title, parts)
        current = workspace()
        materials = "\n".join(current.current_material(kind) for kind in DocumentKind)
        return _confirm(
            ctx, f"迁跃：把这段经历存进经历库。\n\n{entry}", unsupported(entry, materials)
        )

    @mcp.tool(title="保存 STAR 经历", annotations=IDEMPOTENT_WRITE)
    async def save_star(
        title: str,
        situation: str,
        task: str,
        action: str,
        result: str,
        answer: Annotated[ElicitationResult[Confirm], Resolve(ask_star)],
    ) -> str:
        """把用户讲的一段经历整理成 STAR，存进经历库。title 是这段经历的名字。
        situation 情境：当时的背景。task 任务：用户自己要解决什么。action 行动：用户自己做了什么。
        result 结果：最后怎么样。四个部分都要用户讲过才调用，缺哪个先问用户，不要自己补。
        只能用用户说过的事实，照他的说法写：他说「参与」就写参与，不要写成负责；他没说结果的
        数字就不写数字。保存前迁跃会直接请用户确认，用户拒绝就不保存。同名的经历会被替换。"""
        refused = _refusal(answer)
        if refused is not None:
            return refused or "用户没有确认，这段经历没有保存。问问用户哪里要改，不要原样再存一次。"
        parts = {"situation": situation, "task": task, "action": action, "result": result}
        return workspace().save_star(title.strip(), render_entry(title, parts))

    # ---------- Tool：任务 ----------

    @mcp.tool(title="发起任务", annotations=STARTS_WORK)
    async def run_task(
        ctx: Context,
        task: Task,
        job_ids: list[int],
        request: str = "",
        redo: bool = False,
        wait_seconds: int = 180,
    ) -> Reply:
        """让迁跃做一件事，并等它做完。task：
        evaluation 岗位评估（对照用户简历做八维评分，可以一次给多个岗位，每个约 40 秒）；
        tailoring 定制简历（生成逐条修改方案，然后停下来等用户确认，约 1 分钟）；
        company_research 公司研究（联网查这家公司，1–2 分钟）；
        communication 沟通建议（分析已保存的沟通记录并起草消息，约 20 秒）；
        interview_prep 面试准备（预测问题并整理回答要点，1–2 分钟）；
        profile 能力画像（把 2–15 个岗位的 JD 汇总成「这类岗位普遍要什么、用户差在哪」，
        约 1 分钟，只产出一份成果）。
        评估可以给多个岗位，能力画像要给 2–15 个，其余一次只能给一个岗位。
        request 写用户这次的特别要求。
        输入没变时直接返回已有的成果；只有用户明确要求重做才把 redo 设为 true。
        最多等 wait_seconds 秒。到时间还没做完会返回运行编号，之后用 get_task 查结果；
        任务在迁跃后台继续，不需要重新发起。同样的任务重复发起拿到的是同一个运行。
        用户没配模型 Key 时，返回的是迁跃请你代答的一步，照做后用 answer_model_request 交回。"""
        current = workspace()
        run, created, problem = current.start_task(RunKind(task), job_ids, request, redo, model())
        if run is None:
            return problem
        await _follow(current, run.id, ctx, min(max(wait_seconds, 0), MAX_WAIT_SECONDS))
        return _handoff(current, run.id) or await _final(current, run.id, created=created)

    @mcp.tool(title="查任务", annotations=READ_ONLY)
    async def get_task(run_id: str) -> Reply:
        """查看一次任务现在的情况：还在做、等用户确认，还是已经做完（带成果的开头和成果编号）。
        定制简历在等确认时，这里会列出每一条修改，供用户逐条决定。
        用户从迁跃复制来一句带运行编号的话（例如让你识别他上传的截图）时，用它接手：
        任务在等你代答时，返回的就是要你完成的那一步。"""
        current = workspace()
        return _handoff(current, run_id) or await _final(current, run_id)

    @mcp.tool(title="列出任务历史", annotations=READ_ONLY)
    def list_tasks(
        status: RunStatus | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> RunList:
        """分页读取任务历史，最新的在前；可按状态筛选。返回任务编号、岗位编号、状态、进度和总数。"""
        current = workspace()
        with Session(current.engine) as session:
            runs, total = run_store.list_runs(session, status=status, limit=limit, offset=offset)
            return RunList(items=[read_run(run, current.manager) for run in runs], total=total)

    @mcp.tool(title="从断点继续任务", annotations=STARTS_WORK)
    async def resume_task(ctx: Context, run_id: str, wait_seconds: int = 180) -> Reply:
        """继续一个失败或取消的任务（含服务停止时未跑完的任务）。已做完的部分保留。
        等用户回答或确认的任务请走对应的面试或定制工具。"""
        current = workspace()
        with Session(current.engine) as session:
            run = session.get(Run, run_id)
        if run is None:
            return f"没有编号为 {run_id} 的运行。"
        if run.status not in (RunStatus.failed, RunStatus.cancelled):
            return "只有失败或取消的任务可以从断点继续。用 get_task 查看当前状态。"
        chat_model = None if run.kind == RunKind.collection else model()
        if not await current.manager.resume(run, chat_model):
            return "这个任务没有可以继续的断点，请重新发起。"
        await _follow(current, run_id, ctx, min(max(wait_seconds, 0), MAX_WAIT_SECONDS))
        return _handoff(current, run_id) or await _final(current, run_id)

    @mcp.tool(title="取消任务", annotations=IDEMPOTENT_WRITE)
    async def cancel_task(run_id: str) -> str:
        """取消一次还在进行或在等用户确认的任务（包括采集）。已经做完的部分保留：评完的岗位、
        存好的岗位都还在。用户明确说要停下时才调用。"""
        return await workspace().cancel_task(run_id)

    @mcp.tool(title="预览定制简历", annotations=READ_ONLY)
    async def preview_tailoring(
        run_id: str, accepted: list[int], overrides: dict[int, str] | None = None
    ) -> str:
        """按接受的修改编号预览定制简历，不保存。overrides 是修改编号到用户自己写的新文字的映射，
        只用于接受了的替换、插入项。用户确认预览后，再用相同参数提交给 confirm_tailoring。"""
        current = workspace()
        with Session(current.engine) as session:
            run = session.get(Run, run_id)
        if run is None or run.kind != RunKind.tailoring or run.status != RunStatus.waiting_user:
            return "这次定制简历没有在等确认。"
        values = await current.manager.values(run)
        try:
            return preview_choice(values, accepted, overrides or {})
        except ValueError as error:
            return f"没有预览：{error}。"

    @mcp.tool(title="确认定制修改", annotations=STARTS_WORK)
    async def confirm_tailoring(
        ctx: Context, run_id: str, accepted: list[int], overrides: dict[int, str] | None = None
    ) -> Reply:
        """提交用户接受的定制简历修改，生成定制简历。accepted 是用户接受的修改编号；
        空列表表示全部不接受。必须先用 get_task 把每条修改给用户看过，由用户自己决定，
        不要替用户选。overrides 只传用户自己写的新文字，提交前可用 preview_tailoring 预览。
        基础简历不会被改动。"""
        current = workspace()
        problem = await current.confirm_tailoring(run_id, accepted, overrides)
        if problem:
            return problem
        await _follow(current, run_id, ctx, 60)
        return _handoff(current, run_id) or await current.task_outcome(run_id)

    # ---------- Tool：没有模型 Key 时由你代答 ----------

    @mcp.tool(title="交回代答结果", annotations=STARTS_WORK)
    async def answer_model_request(
        ctx: Context,
        request_id: str,
        text: str = "",
        tool_calls: list[ToolCall] | None = None,
        wait_seconds: int = 180,
    ) -> Reply:
        """交回你替迁跃的模型完成的一步（来自「迁跃请你代答」的请求）。text 是你的回复，要求 JSON
        时只写 JSON；请迁跃执行工具时 text 留空，在 tool_calls 里写工具名和参数。
        迁跃校验并继续运行，返回下一个要你完成的请求，或者任务的最终结论。
        最多等 wait_seconds 秒，没做完会返回运行编号，之后用 get_task 查。"""
        current = workspace()
        agent = current.manager.agent
        request = agent.get(request_id)
        if request is None:
            return (
                f"没有在等编号为 {request_id} 的请求：它可能已经交回，或者任务已结束、被取消。"
                "用 get_model_request 看看现在有没有要你完成的请求。"
            )
        problem = agent.answer(request_id, text, [call.model_dump() for call in tool_calls or []])
        if problem:
            return problem
        seconds = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        last = await _follow(current, request.run_id, ctx, seconds, follow_up=True)
        return _handoff(current, last) or await _final(current, last)

    @mcp.tool(title="取代答请求", annotations=READ_ONLY)
    def get_model_request(request_id: str = "") -> Reply:
        """取一个等你代答的请求的全文。不给 request_id 时取最早的一个。
        上下文里找不到之前请求的内容时用它。"""
        current = workspace()
        agent = current.manager.agent
        request = agent.get(request_id) if request_id else agent.next_pending()
        if request is None:
            return "现在没有要你代答的请求。"
        with Session(current.engine) as session:
            run = session.get(Run, request.run_id)
        agent.take(request)
        return request_reply(request, run, current.manager.settings.data_dir, full=True)

    @mcp.tool(title="看截图", annotations=READ_ONLY)
    def view_screenshots(upload_id: str) -> Reply:
        """看用户在迁跃里上传的截图。用户从迁跃复制来的话里写了「截图编号」时用它，
        返回图片本身和它们在本机的路径。截图只保留 7 天。"""
        data_dir = workspace().manager.settings.data_dir
        try:
            files = uploads.paths(data_dir, upload_id.strip())
        except uploads.UploadMissing as error:
            return str(error)
        lines = [f"截图 {upload_id} 一共 {len(files)} 张，按用户上传的顺序附在后面。"]
        lines.append("看不到附图时，打开本机的这些文件：")
        lines += [f"- 图片 {number}：{path}" for number, path in enumerate(files, start=1)]
        lines.append(
            "都看不了时不要猜图里的内容，告诉用户你看不了图片，请他把截图里的文字粘贴过来。"
        )
        return ["\n".join(lines), *image_files(files)]

    # ---------- Tool：模拟面试 ----------

    @mcp.tool(title="开始模拟面试", annotations=STARTS_WORK)
    async def start_mock_interview(
        ctx: Context, job_id: int, request: str = "", wait_seconds: int = 60
    ) -> InterviewState:
        """为一个岗位开始模拟面试。request 可写用户想练的方向或面试官角色。
        同岗位同要求正在面试时返回原运行。保存 run_id；如果还在生成，用 get_mock_interview 查题。
        pending.question 是迁跃面试官的完整问题，原样给用户看，等用户本人回答。
        有 model_request 时（用户没配模型 Key），先替面试官完成这一步，
        用 answer_model_request 交回。"""
        current = workspace()
        run, _, problem = current.start_task(
            RunKind.mock_interview, [job_id], request, False, model()
        )
        if run is None:
            return InterviewState(error=problem)
        await _follow(current, run.id, ctx, min(max(wait_seconds, 0), MAX_WAIT_SECONDS))
        return await _interview_state(current, run.id)

    @mcp.tool(title="查看模拟面试", annotations=READ_ONLY)
    async def get_mock_interview(
        ctx: Context, run_id: str, wait_seconds: int = 60
    ) -> InterviewState:
        """用同一个 run_id 查看或接着面试。等用户回答时返回 pending（题号 turn、问题 question、
        总轮次 total、此前问答 transcript）；结束后用 artifact_id 读取复盘。如果还在生成，最多等
        wait_seconds 秒。答题或结束用 respond_mock_interview。"""
        current = workspace()
        await _follow(current, run_id, ctx, min(max(wait_seconds, 0), MAX_WAIT_SECONDS))
        return await _interview_state(current, run_id)

    @mcp.tool(title="回答或结束模拟面试", annotations=STARTS_WORK)
    async def respond_mock_interview(
        ctx: Context,
        run_id: str,
        turn: int,
        action: Literal["answer", "skip", "end"],
        answer: str = "",
        wait_seconds: int = 60,
    ) -> InterviewState:
        """用最新的 pending.turn 提交当前题。answer 动作必须传用户的原话；用户要求跳过用 skip；
        用户要求提前结束并复盘用 end。不要替用户选择。返回下一题或复盘编号；如果仍在运行，
        用 get_mock_interview 查询，不要重复提交。"""
        current = workspace()
        try:
            if action == "end":
                reply = {"end": True}
            else:
                reply = interview_answer("跳过" if action == "skip" else answer)
        except ValueError as error:
            return await _interview_state(current, run_id, error=str(error))
        problem = await current.manager.respond_to_interview(run_id, turn, reply, model())
        if problem is None:
            await _follow(current, run_id, ctx, min(max(wait_seconds, 0), MAX_WAIT_SECONDS))
        return await _interview_state(current, run_id, error=problem)

    # ---------- Tool：采集 ----------

    @mcp.tool(title="采集岗位", annotations=STARTS_WORK)
    async def collect_jobs(
        ctx: Context,
        platforms: list[Platform],
        queries: list[str],
        cities: list[str],
        per_search: int = 10,
        per_platform: dict[str, int] | None = None,
        min_salary: int | None = None,
        years: int | None = None,
        exclude: list[str] | None = None,
        evaluate: bool = False,
        list_only: bool = False,
        wait_seconds: int = 180,
    ) -> Reply:
        """到招聘平台上搜索岗位并保存到迁跃的岗位列表。平台、关键词、城市都要用户明确说过。
        platforms：51job 前程无忧，liepin 猎聘，zhaopin 智联招聘，boss BOSS 直聘。queries 是搜索
        关键词，cities 是城市的中文名。每一种「平台 × 关键词 × 城市」是一个搜索，per_search 是每个
        搜索新增多少个岗位（1–20），一次总共不超过 100 个，每个平台 24 小时最多新增 200 个。
        各平台想要的数量不同时用 per_platform，例如 {"boss": 15, "51job": 20}，
        没写到的平台用 per_search。
        采集在专用 Chrome 中后台进行。获取完整 JD 时，猎聘和 BOSS 直聘需要逐条打开详情页。
        已经保存过的岗位不会重复读取。平台要求登录或验证时会停下并说明原因，
        同时把那个页面在专用浏览器里打开，由用户自己处理，处理好再重新发起。
        筛选条件只在用户说了才填：min_salary 最低月薪（单位千元），years 用户的工作年限，
        exclude 职位名里不要出现的词。和关键词明显无关的岗位会自动跳过。
        evaluate 为 true 时采完自动评估新增的岗位，结果里会给出评估的运行编号。
        list_only 为 true 时不读详情页（用户不想登录平台时用），没有职位描述的岗位标为
        「JD 不完整」，补上 JD 后才能评估。
        最多等 wait_seconds 秒，没做完会返回运行编号，之后用 get_task 查。
        同一时间只能有一次采集。"""
        current = workspace()
        chat_model = model()
        run, created, problem = current.start_collection(
            platforms,
            queries,
            cities,
            per_search,
            model=chat_model,
            per_platform=per_platform or {},
            min_salary=min_salary,
            years=years,
            exclude=exclude or [],
            evaluate=evaluate,
            list_only=list_only,
        )
        if run is None:
            return problem
        # 由你代答时，采完自动评估的那次运行也要你答，接着跟下去
        seconds = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        agent = isinstance(chat_model, AgentChatModel)
        last = await _follow(current, run.id, ctx, seconds, follow_up=agent)
        return _handoff(current, last) or await _final(current, last, created=created)

    @mcp.tool(title="打开专用浏览器", annotations=STARTS_WORK)
    async def open_platform_browser(platform: Platform) -> str:
        """在用户电脑上的专用浏览器里打开一个招聘平台并带到前面，让用户提前登录。
        采集因为登录或验证停下时页面已经自动打开，不用再调用。不能替用户登录，也不能处理验证码。"""
        return await workspace().open_browser(platform)

    return mcp


def _confirm(ctx: Context, message: str, new_terms: list[str]) -> Elicit[Confirm] | str:
    """请客户端弹出确认框，把要保存的全文直接给用户看。客户端不支持确认框时返回不保存的原因。

    对面的 Agent 看得到用户说过什么，迁跃看不到，所以材料里没有的数字和技术词不直接拒绝，
    而是在确认框里列出来，请用户看一眼是不是他说过的。
    """
    capabilities = ctx.client_capabilities
    if capabilities is None or capabilities.elicitation is None:
        return (
            "没有保存：你用的客户端不支持 MCP 的确认框（elicitation），迁跃没法直接请用户确认。"
            "请告诉用户：可以把内容贴到迁跃的「材料」页，或在迁跃的助手里一起改"
        )
    if new_terms:
        message += "\n\n请留意：这些数字或技术词在你现在的材料里没有，确认是你说过的：" + "、".join(
            new_terms
        )
    return Elicit(message, Confirm)


def _refusal(answer: ElicitationResult[Confirm]) -> str | None:
    """用户点了接受：None。没有问用户（条件不够）：原因。用户拒绝或关掉了：空字符串。"""
    if isinstance(answer, AcceptedElicitation):
        return answer.data if isinstance(answer.data, str) else None
    return ""


async def _interview_state(
    workspace: Workspace, run_id: str, *, error: str | None = None
) -> InterviewState:
    with Session(workspace.engine) as session:
        run = session.get(Run, run_id)
    if run is None or run.kind != RunKind.mock_interview:
        return InterviewState(run_id=run_id, error=f"没有编号为 {run_id} 的模拟面试")
    state = InterviewState(run_id=run_id, status=run.status, error=error or run.error)
    reply = _handoff(workspace, run_id)
    if isinstance(reply, list):  # 在等的是别的运行里带图的一步，图用 get_model_request 取
        reply = f"{reply[0]}\n\n附图这里放不下，调用 get_model_request 取带图的全文。"
    state.model_request = reply
    if run.status == RunStatus.waiting_user:
        state.pending = await workspace.manager.pending(run)
    elif run.status == RunStatus.succeeded:
        results = await workspace.manager.results(run)
        if results:
            state.artifact_id = results[0]["artifact_id"]
            state.reason = results[0]["reason"] or None
    return state


async def _follow(
    workspace: Workspace, run_id: str, ctx: Context, seconds: float, *, follow_up: bool = False
) -> str:
    """跟着一次运行看进度，直到它结束、停下来等用户、要 Agent 代答，或者等够了时间。

    follow_up：运行结束时如果接着发起了另一次（采完自动评估），继续跟那一次。
    返回最后跟着的运行编号。
    """
    agent = workspace.manager.agent
    step = 0
    try:
        async with asyncio.timeout(seconds):
            while True:
                events = workspace.manager.events(run_id)
                if events is None or agent.waiting_on(run_id):
                    return run_id
                next_run = None
                async for event, data in events:
                    if event in ("node", "tool"):
                        step += 1
                        detail = f"：{data['detail']}" if event == "tool" else ""
                        await ctx.report_progress(step, None, f"{data['label']}{detail}")
                    elif event == "progress":
                        step += 1
                        await ctx.report_progress(step, None, data["progress"])
                    elif event == "model_request" and agent.waiting_on(run_id):
                        return run_id  # 事件会从头重放，只有确实还在等的才算
                    elif event == "run":
                        next_run = data["run_id"]
                if not (follow_up and next_run):
                    return run_id
                run_id = next_run
    except TimeoutError:
        return run_id


def _handoff(workspace: Workspace, run_id: str) -> Reply | None:
    """这个运行（或者别的运行）在等 Agent 代答时，返回给 Agent 的请求正文（有图片时带上图片）。"""
    agent = workspace.manager.agent
    request = agent.next_pending(run_id)
    if request is None:
        return None
    with Session(workspace.engine) as session:
        run = session.get(Run, request.run_id)
    agent.take(request)
    return request_reply(request, run, workspace.manager.settings.data_dir)


async def _final(workspace: Workspace, run_id: str, *, created: bool = True) -> str:
    """运行现在的结论。模拟面试写成下一题或复盘编号，其余用 task_outcome。"""
    with Session(workspace.engine) as session:
        run = session.get(Run, run_id)
    if run is not None and run.kind == RunKind.mock_interview:
        state = await _interview_state(workspace, run_id)
        if state.pending:
            pending = state.pending
            return (
                f"模拟面试第 {pending['turn']}/{pending['total']} 题（运行编号 {run_id}）：\n"
                f"{pending['question']}\n\n把这道题原样给用户看，等用户本人回答后，用 "
                "respond_mock_interview 提交用户的原话。"
            )
        if state.artifact_id:
            return (
                f"模拟面试结束，复盘是成果 #{state.artifact_id}，用 read_artifact 读取后告诉用户。"
            )
    outcome = await workspace.task_outcome(run_id, created=created, show_edits=True)
    return outcome + _tailoring_note(workspace, run_id)


def _tailoring_note(workspace: Workspace, run_id: str) -> str:
    """定制简历在等确认时，附上怎么确认。"""
    with Session(workspace.engine) as session:
        run = session.get(Run, run_id)
    if run is None or run.kind != RunKind.tailoring or run.status != RunStatus.waiting_user:
        return ""
    name = RUN_KIND_NAMES[RunKind.tailoring]
    return (
        f"\n\n{name}要由用户逐条决定。把上面的修改给用户看，用户选好后调用 "
        "confirm_tailoring 提交他接受的编号。不要替用户选。"
    )
