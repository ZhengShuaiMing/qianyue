"""业务数据表。字段说明见 docs/architecture.md 的数据模型一节。"""

import json
from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4

from sqlmodel import Field, SQLModel


def now_iso() -> str:
    """带时区的 ISO 8601 时间字符串。SQLite 没有时区类型，统一存字符串避免丢时区。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class JobStage(StrEnum):
    """应聘进展：你在招聘方那边走到了哪一步。只由你改，系统不会自动推进。"""

    not_applied = "not_applied"  # 未投递
    applied = "applied"  # 已投递
    assessment = "assessment"  # 已笔试（含测评）
    interviewed = "interviewed"  # 已面试（不分轮次，等结果也在这里）
    offer = "offer"  # 已获 Offer
    rejected = "rejected"  # 未通过
    withdrawn = "withdrawn"  # 放弃：不考虑了、拒了 Offer


class JobCategory(StrEnum):
    not_started = "not_started"  # 未开始
    in_progress = "in_progress"  # 进行中
    closed = "closed"  # 已结束


class JobPrep(StrEnum):
    """处理进度：迁跃这边做到哪了。不存，按有没有 JD、有没有评估报告算出来。"""

    incomplete = "incomplete"  # 还没有 JD
    unevaluated = "unevaluated"  # 有 JD，还没有评估报告
    evaluated = "evaluated"  # 有评估报告


STAGE_CATEGORY = {
    JobStage.not_applied: JobCategory.not_started,
    JobStage.applied: JobCategory.in_progress,
    JobStage.assessment: JobCategory.in_progress,
    JobStage.interviewed: JobCategory.in_progress,
    JobStage.offer: JobCategory.closed,
    JobStage.rejected: JobCategory.closed,
    JobStage.withdrawn: JobCategory.closed,
}


STAGE_NAMES = {
    JobStage.not_applied: "未投递",
    JobStage.applied: "已投递",
    JobStage.assessment: "已笔试",
    JobStage.interviewed: "已面试",
    JobStage.offer: "已获 Offer",
    JobStage.rejected: "未通过",
    JobStage.withdrawn: "已放弃",
}
CATEGORY_NAMES = {
    JobCategory.not_started: "未开始",
    JobCategory.in_progress: "进行中",
    JobCategory.closed: "已结束",
}
PREP_NAMES = {
    JobPrep.incomplete: "JD 不完整",
    JobPrep.unevaluated: "待评估",
    JobPrep.evaluated: "已评估",
}


class Speaker(StrEnum):
    recruiter = "recruiter"  # 招聘方说的
    me = "me"  # 我说的
    transcript = "transcript"  # 原样粘贴的一整段聊天，里面两边的话都有


class RunStatus(StrEnum):
    running = "running"
    waiting_user = "waiting_user"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class DocumentKind(StrEnum):
    resume = "resume"  # 基础简历，评估必需
    supplement = "supplement"  # 补充资料：求职方向和期望、到岗时间、作品和证书，选填
    star = "star"  # STAR 经历：一份文档，用二级标题分隔每段经历，选填


DOCUMENT_KIND_NAMES = {
    DocumentKind.resume: "基础简历",
    DocumentKind.supplement: "补充资料",
    DocumentKind.star: "STAR 经历",
}


class RunKind(StrEnum):
    evaluation = "evaluation"
    tailoring = "tailoring"
    company_research = "company_research"
    communication = "communication"
    interview_prep = "interview_prep"
    mock_interview = "mock_interview"
    collection = "collection"  # 采集岗位。不针对已有岗位，输入是一组搜索
    profile = "profile"  # 能力画像：几个岗位汇总成一份，结论不属于某一个岗位
    screenshot = "screenshot"  # 识别截图：没有模型 Key 时由用户的 Agent 读截图，存成岗位或沟通


RUN_KIND_NAMES = {
    RunKind.evaluation: "岗位评估",
    RunKind.tailoring: "定制简历",
    RunKind.company_research: "公司研究",
    RunKind.communication: "沟通建议",
    RunKind.interview_prep: "面试准备",
    RunKind.mock_interview: "模拟面试",
    RunKind.collection: "岗位采集",
    RunKind.profile: "能力画像",
    RunKind.screenshot: "识别截图",
}


class ArtifactKind(StrEnum):
    evaluation = "evaluation"
    tailored_resume = "tailored_resume"
    company_research = "company_research"
    communication_advice = "communication_advice"
    interview_prep = "interview_prep"
    mock_interview = "mock_interview"  # 模拟面试的复盘
    profile = "profile"  # 能力画像，不属于某一个岗位


ARTIFACT_KIND_NAMES = {
    ArtifactKind.evaluation: "岗位评估",
    ArtifactKind.tailored_resume: "定制简历",
    ArtifactKind.company_research: "公司研究",
    ArtifactKind.communication_advice: "沟通建议",
    ArtifactKind.interview_prep: "面试准备",
    ArtifactKind.mock_interview: "模拟面试复盘",
    ArtifactKind.profile: "能力画像",
}


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    source: str = "manual"
    url: str | None = Field(default=None, unique=True)
    company: str = ""
    title: str = ""
    city: str | None = None
    salary: str | None = None
    # 采集时从平台列表上读到的；手动添加的岗位一般没有
    company_size: str | None = None
    industry: str | None = None
    posted_at: str | None = None  # 平台显示的发布日期（猎聘是刷新日期）
    jd_text: str = ""
    jd_hash: str | None = Field(default=None, index=True)
    stage: str = Field(default=JobStage.not_applied, index=True)
    note: str = ""
    # 看起来和这个更早的岗位是同一个（换了名字或在别的平台又发了一次）。只提示，不合并
    similar_to: int | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    deleted_at: str | None = None

    @property
    def has_jd(self) -> bool:
        return bool(self.jd_text.strip())


class JobEvent(SQLModel, table=True):
    __tablename__ = "job_events"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    from_stage: str | None = None
    to_stage: str
    # 这一步是哪天发生的（你填的，不填是改阶段那天）。created_at 是记下来的时间
    happened_on: str = Field(default_factory=lambda: date.today().isoformat())
    note: str = ""
    created_at: str = Field(default_factory=now_iso)


class Document(SQLModel, table=True):
    """用户自己的材料。每次保存是一个新版本，is_current 标记最新一版。"""

    __tablename__ = "documents"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    title: str = ""
    content_md: str = ""
    content_hash: str = ""
    is_current: bool = True
    created_at: str = Field(default_factory=now_iso)


class Run(SQLModel, table=True):
    """一次 AI 任务的运行。id 同时作为 LangGraph 的 thread_id。"""

    __tablename__ = "runs"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    kind: str = Field(index=True)
    job_id: int | None = Field(default=None, foreign_key="jobs.id", index=True)
    status: str = Field(default=RunStatus.running, index=True)
    step: str = ""
    error: str | None = None
    input_json: str = "{}"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None


class Artifact(SQLModel, table=True):
    """全部 AI 产出。正文在 content_md，结构化结果在 data_json。"""

    __tablename__ = "artifacts"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    job_id: int | None = Field(default=None, foreign_key="jobs.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="runs.id")
    title: str = ""
    content_md: str = ""
    data_json: str | None = None
    score: float | None = None
    input_hash: str | None = Field(default=None, index=True)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    deleted_at: str | None = None

    @property
    def data(self) -> dict:
        return json.loads(self.data_json) if self.data_json else {}


class Communication(SQLModel, table=True):
    __tablename__ = "communications"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    speaker: str
    text: str
    created_at: str = Field(default_factory=now_iso)


class AppSettings(SQLModel, table=True):
    """在设置页填的配置，只有一行（id 固定为 1）。填了的项覆盖 .env，没填的（None）沿用 .env。"""

    __tablename__ = "settings"

    id: int = Field(default=1, primary_key=True)
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None  # 和 .env 一样明文存在本机的 data/ 里；接口不回传
    search_provider: str | None = None
    search_api_key: str | None = None  # 同上
    hide_personal_info: bool = True
    privacy_terms: str = ""
    updated_at: str = Field(default_factory=now_iso)


class ConversationTitle(SQLModel, table=True):
    """你给助手对话改的名字。对话本身存在 Checkpoint 里；没改过名的对话这里没有记录，
    标题用第一句话。删除对话时这条一起删。"""

    __tablename__ = "conversations"

    id: str = Field(primary_key=True)  # 对话编号
    title: str
    updated_at: str = Field(default_factory=now_iso)


class SearchRecord(SQLModel, table=True):
    """检索页的检索历史。同一个查询和类别只留一条，再查一次就更新时间和结果数。"""

    __tablename__ = "search_history"

    id: int | None = Field(default=None, primary_key=True)
    query: str
    type: str | None = None  # 只在这一类里找；None 是全部
    hits: int = 0
    created_at: str = Field(default_factory=now_iso)
