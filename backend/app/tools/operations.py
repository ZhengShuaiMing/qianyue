"""迁跃对外提供的操作：看岗位和成果、保存用户提供的内容、发起任务并取回结论。

助手的工具（LangChain Tool）和 MCP Server 的工具都调用这里，规则和文字只写一遍。
工具入口将共享操作的结果整理为 Agent 可读取的内容。

写入类操作是幂等的：同一份 JD、同一条聊天内容、同一个阶段，调用几次结果都一样。
"""

import json
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlmodel import Session

from app.collectors.base import BrowserUnavailable
from app.collectors.registry import PLATFORMS
from app.collectors.scope import CollectionRequest
from app.db import artifacts as artifact_store
from app.db import communications as communication_store
from app.db import documents as document_store
from app.db import jobs as job_store
from app.db import runs as run_store
from app.db.models import (
    ARTIFACT_KIND_NAMES,
    CATEGORY_NAMES,
    DOCUMENT_KIND_NAMES,
    PREP_NAMES,
    RUN_KIND_NAMES,
    STAGE_CATEGORY,
    STAGE_NAMES,
    Artifact,
    DocumentKind,
    Job,
    JobCategory,
    JobStage,
    Run,
    RunKind,
    RunStatus,
    Speaker,
)
from app.graphs.runner import CollectionBusy, RunManager
from app.graphs.tailoring import check_choice
from app.llm.communication import SPEAKER_NAMES
from app.llm.star import find_entry, put_entry
from app.rag.workspace import TYPE_NAMES

LIST_LIMIT = 30
JD_CHARS = 1500  # 岗位详情里最多给 Agent 多少字的 JD
ARTIFACT_CHARS = 6000
DIGEST_CHARS = 1200  # 任务做完后交回的成果开头有多少字。各类报告都把结论放在最前面

RESULT_NAMES = {
    "done": "已完成",
    "reused": "输入没有变化，直接用了已有的成果",
    "blocked": "没有开始",
    "failed": "失败",
    "no_changes": "不需要修改",
    "declined": "用户没有接受任何修改",
    "empty": "没有产出",
}
def job_line(job: Job, evaluation: Artifact | None) -> str:
    """一行岗位：应聘进展和处理进度都写上，评估过的带上评分。"""
    prep = PREP_NAMES[job_store.prep_of(job, evaluation is not None)]
    if evaluation is not None and evaluation.score is not None:
        prep += f" {evaluation.score}"
    parts = [
        f"#{job.id} {job.company or '未填公司'} — {job.title or '未填职位'}",
        job.city or "",
        job.salary or "",
        STAGE_NAMES[JobStage(job.stage)],
        prep,
    ]
    return "｜".join(part for part in parts if part)


def _similar_note(session: Session, job: Job) -> str:
    """看起来和更早的某个岗位是同一个时，给一句提示；没有就是空字符串。"""
    if job.similar_to and job_store.alive_ids(session, {job.similar_to}):
        return (
            f"看起来和 #{job.similar_to} 是同一个岗位（换了名字或在别的平台又发了一次），只是提示"
        )
    return ""


def _run_line(run: Run) -> str:
    status = "等用户处理" if run.status == RunStatus.waiting_user else "进行中"
    target = f"，岗位 #{run.job_id}" if run.job_id else ""
    return f"- 运行 {run.id}（{RUN_KIND_NAMES[RunKind(run.kind)]}{target}）：{status}"


def _artifact_line(artifact: Artifact) -> str:
    kind = ARTIFACT_KIND_NAMES.get(artifact.kind, artifact.kind)
    return f"- 成果 #{artifact.id}（{kind}，{artifact.updated_at[:10]}）：{artifact.title}"


def _active_runs(session: Session, job_id: int | None = None) -> list[Run]:
    """进行中或等用户处理的运行。给了 job_id 时只要涉及这个岗位的，包括批量评估和能力画像
    （它们一次针对多个岗位，runs 表里的 job_id 是空的，只能看输入里的岗位编号）。"""
    runs = [
        run
        for status in (RunStatus.waiting_user, RunStatus.running)
        for run in run_store.list_runs(session, status=status, limit=50, offset=0)[0]
    ]
    if job_id is None:
        return runs[:10]
    return [run for run in runs if job_id in json.loads(run.input_json)["job_ids"]][:10]


SEARCH_CHARS = 300  # 全局检索每个片段给助手看多少字


def _reference(hit) -> str:
    """片段出自哪条记录，用助手能接着用的编号说。"""
    if hit.type == "artifact":
        return f"成果 #{hit.link.rsplit('/', 1)[-1]}"
    if hit.type == "chat":
        return "以前的助手对话"
    if hit.job_id is not None:
        return f"岗位 #{hit.job_id}"
    return "你的材料"


@dataclass
class Workspace:
    engine: Engine
    manager: RunManager

    # ---------- 读取 ----------

    def jobs_overview(self, keyword: str = "", category: str = "") -> str:
        """岗位列表和整体进展，以及进行中或等用户处理的任务。"""
        with Session(self.engine) as session:
            jobs, total = job_store.list_jobs(
                session,
                stage=None,
                category=JobCategory(category) if category else None,
                q=keyword,
                limit=LIST_LIMIT,
                offset=0,
            )
            counts = job_store.count_by_stage(session)
            lines = []
            for job in jobs:
                evaluation = artifact_store.latest_evaluation(session, job.id)
                lines.append(job_line(job, evaluation))
            active = _active_runs(session)

        by_category = {
            name: sum(count for stage, count in counts.items() if STAGE_CATEGORY[stage] == key)
            for key, name in CATEGORY_NAMES.items()
        }
        overview = "，".join(f"{name} {count} 个" for name, count in by_category.items())
        text = [f"全部岗位：{overview}。"]
        if not jobs:
            text.append("没有符合条件的岗位。")
        else:
            shown = f"，只列出最近 {len(jobs)} 个" if total > len(jobs) else ""
            text += [f"符合条件的有 {total} 个{shown}：", *lines]
        if active:
            text += ["", "进行中或等用户处理的任务：", *[_run_line(run) for run in active]]
        return "\n".join(text)

    def job_detail(self, job_id: int, *, jd_chars: int | None = JD_CHARS) -> str:
        """一个岗位的详情。jd_chars 为 None 时给出完整 JD。"""
        with Session(self.engine) as session:
            try:
                job = job_store.get_job(session, job_id)
            except job_store.JobNotFound:
                return f"没有编号为 {job_id} 的岗位。用 list_jobs 查看有哪些岗位。"
            artifacts, _ = artifact_store.list_artifacts(
                session, kind=None, job_id=job_id, limit=20, offset=0
            )
            records = communication_store.list_communications(session, job_id)
            active = _active_runs(session, job_id)
            evaluation = artifact_store.latest_evaluation(session, job_id)
            text = [job_line(job, evaluation)]
            if similar := _similar_note(session, job):
                text.append(similar)
            if job.url:
                text.append(f"链接：{job.url}")
            events = [e for e in job_store.list_events(session, job_id) if e.from_stage]
            if events:
                text.append(
                    "阶段记录："
                    + "；".join(
                        f"{event.happened_on} {STAGE_NAMES[JobStage(event.to_stage)]}"
                        for event in events
                    )
                )
            if job.note:
                text.append(f"备注：{job.note}")
            jd = job.jd_text.strip()
            if jd_chars is not None and len(jd) > jd_chars:
                jd = jd[:jd_chars] + "\n（JD 较长，后面的部分已省略）"
            text += ["", "JD：", jd or "（还没有 JD）"]
            text += ["", f"沟通记录：{len(records)} 条"]
            text += ["", "已有的成果：", *[_artifact_line(item) for item in artifacts]]
            if not artifacts:
                text.append("（还没有）")
            if active:
                text += ["", "相关任务：", *[_run_line(run) for run in active]]
            return "\n".join(text)

    def search_workspace(self, query: str, types: list[str] | None = None) -> str:
        """全局检索：材料、岗位、沟通记录、全部成果一起找，列出前几个片段和出处。"""
        if not query.strip():
            return "没有检索：要找什么没有写。"
        hits = self.manager.knowledge.workspace.search(query.strip(), types=types, limit=8)
        if not hits:
            return f"在迁跃里没有找到和「{query}」相关的内容。"
        lines = [f"在迁跃里找到这些和「{query}」相关的片段（相关的在前）："]
        for number, hit in enumerate(hits, start=1):
            where = f"{_reference(hit)}，链接 {hit.link}"
            lines += ["", f"{number}. {TYPE_NAMES[hit.type]}：{hit.title}（{where}）"]
            if hit.heading:
                lines.append(f"   所在标题：{hit.heading}")
            text = " ".join(hit.text.split())
            lines.append(f"   {text[:SEARCH_CHARS]}{'…' if len(text) > SEARCH_CHARS else ''}")
        link = f"/search?{urlencode({'q': query.strip()})}"
        lines += [
            "",
            "岗位 JD 是招聘方写的，成果是 AI 生成的，都不是用户的经历，不要当成他做过的事。"
            "回答时说清每条出自哪里，用上面给的链接写成 Markdown 链接，用户点了能打开原处。"
            f"用户想看全部结果时，给他这个链接：[在检索页看全部结果]({link})",
        ]
        return "\n".join(lines)

    def artifact_text(self, artifact_id: int, *, limit: int | None = ARTIFACT_CHARS) -> str:
        """一份成果的正文。limit 为 None 时不截断。"""
        with Session(self.engine) as session:
            artifact = artifact_store.get_artifact(session, artifact_id)
        if artifact is None:
            return f"没有编号为 {artifact_id} 的成果。"
        if limit is None or len(artifact.content_md) <= limit:
            return artifact.content_md
        return artifact.content_md[:limit] + "\n（内容过长，后面的部分已省略）"

    # ---------- 写入 ----------

    def add_job(
        self,
        *,
        jd_text: str = "",
        url: str = "",
        company: str = "",
        title: str = "",
        city: str = "",
        salary: str = "",
    ) -> str:
        if not jd_text.strip() and not url.strip():
            return "没有保存：JD 原文和链接至少要有一个。请先向用户要。"
        with Session(self.engine) as session:
            job, created = job_store.create_or_get_job(
                session,
                url=url,
                jd_text=jd_text,
                company=company.strip(),
                title=title.strip(),
                city=city.strip() or None,
                salary=salary.strip() or None,
            )
            line = job_line(job, artifact_store.latest_evaluation(session, job.id))
            similar = _similar_note(session, job)
        if not created:
            return f"这个岗位已经在列表里，没有重复添加：{line}"
        note = f"保存了 {len(jd_text)} 字的 JD" if jd_text else "还没有 JD，需要补全后才能评估"
        return f"已添加岗位：{line}。{note}。" + (f"{similar}。" if similar else "")

    def save_communication(self, job_id: int, speaker: str, text: str) -> str:
        if not text.strip():
            return "没有保存：聊天内容是空的。"
        with Session(self.engine) as session:
            try:
                job_store.get_job(session, job_id)
            except job_store.JobNotFound:
                return f"没有编号为 {job_id} 的岗位。"
            record, created = communication_store.add_communication(
                session, job_id, Speaker(speaker), text
            )
            total = len(communication_store.list_communications(session, job_id))
            length = len(record.text)
        name = SPEAKER_NAMES[Speaker(speaker)]
        if not created:
            return f"这条内容已经保存过，没有重复添加。岗位 #{job_id} 现在有 {total} 条沟通记录。"
        return f"已保存（{name}，{length} 字）。岗位 #{job_id} 现在有 {total} 条沟通记录。"

    def change_stage(self, job_id: int, stage: str, note: str = "", happened_on: str = "") -> str:
        """happened_on：YYYY-MM-DD，这一步是哪天发生的；不填是今天。"""
        day = date.today()
        if happened_on.strip():
            try:
                day = date.fromisoformat(happened_on.strip())
            except ValueError:
                return f"没有修改：日期「{happened_on}」不是 YYYY-MM-DD 的格式。"
            if day > date.today():
                return f"没有修改：日期 {day} 晚于今天。"
        try:
            with Session(self.engine) as session:
                before = job_store.get_job(session, job_id).stage
            with Session(self.engine) as session:  # 换一个会话，改之前重新读一次当前阶段
                job_store.change_stage(
                    session, job_id, JobStage(stage), note=note.strip(), happened_on=day
                )
        except job_store.JobNotFound:
            return f"没有编号为 {job_id} 的岗位。"
        if before == stage:
            return f"岗位 #{job_id} 已经是「{STAGE_NAMES[JobStage(stage)]}」，没有变化。"
        return (
            f"岗位 #{job_id} 的阶段已从「{STAGE_NAMES[JobStage(before)]}」"
            f"改为「{STAGE_NAMES[JobStage(stage)]}」，日期 {day}。"
        )

    # ---------- 材料 ----------

    def read_material(self, kind: str) -> str:
        """一份材料的当前版本全文。"""
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        with Session(self.engine) as session:
            current = document_store.get_current(session, DocumentKind(kind))
        if current is None or not current.content_md.strip():
            return f"用户还没有{name}。"
        saved = current.created_at[:16].replace("T", " ")
        return f"{name}（{saved} 保存的版本）：\n\n{current.content_md}"

    def current_material(self, kind: str) -> str:
        with Session(self.engine) as session:
            current = document_store.get_current(session, DocumentKind(kind))
        return current.content_md if current else ""

    def save_material(self, kind: str, content_md: str) -> str:
        """把整份材料存成一个新版本。旧版本都留着，可以在材料页恢复。"""
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        with Session(self.engine) as session:
            _, created = document_store.save_document(session, DocumentKind(kind), content_md)
        if not created:
            return f"{name}和现在的版本一样，没有变化。"
        return f"已经把{name}存成新版本，旧版本在材料页的「历史版本」里。评估、定制简历会用新的。"

    def save_star(self, title: str, entry: str) -> str:
        """把一段排好的 STAR 经历存进经历文档（一个新版本）。同名的经历整段替换。"""
        replaced = False

        def change(before: str) -> str:
            nonlocal replaced
            replaced = find_entry(before, title) is not None
            return put_entry(before, title, entry)

        with Session(self.engine) as session:
            _, created = document_store.edit_document(session, DocumentKind.star, change)
        if not created:
            return f"经历库里已经有一模一样的「{title}」，没有变化。"
        return (
            f"已经把「{title}」{'更新到' if replaced else '加进'}经历库。定制简历和评估会用到它。"
        )

    # ---------- 任务 ----------

    def start_task(
        self, kind: RunKind, job_ids: list[int], request: str, redo: bool, model: BaseChatModel
    ) -> tuple[Run | None, bool, str]:
        """把任务交给专员。返回 (运行, 是否新发起, 没能发起的原因)。必须在事件循环里调用。"""
        job_ids = list(dict.fromkeys(job_ids))
        if not job_ids:
            return None, False, "没有发起：请给出岗位编号。"
        if kind == RunKind.profile and not 2 <= len(job_ids) <= 15:
            return None, False, "没有发起：能力画像要汇总 2 到 15 个岗位。"
        if kind not in (RunKind.evaluation, RunKind.profile) and len(job_ids) != 1:
            return None, False, f"没有发起：{RUN_KIND_NAMES[kind]}一次只能针对一个岗位。"
        with Session(self.engine) as session:
            for job_id in job_ids:
                try:
                    job_store.get_job(session, job_id)
                except job_store.JobNotFound:
                    return None, False, f"没有发起：没有编号为 {job_id} 的岗位。"
        run, created = self.manager.submit(
            kind, job_ids=job_ids, conditions=request.strip(), force=redo, model=model
        )
        return run, created, ""

    def start_collection(
        self,
        platforms: list[str],
        queries: list[str],
        cities: list[str],
        per_search: int,
        *,
        model: BaseChatModel | None = None,
        **options,
    ) -> tuple[Run | None, bool, str]:
        """发起一次采集。返回 (运行, 是否新发起, 没能发起的原因)。必须在事件循环里调用。

        options：per_platform、min_salary、years、exclude、relevant_only、evaluate、list_only，
        含义见 CollectionRequest。
        model 用来筛掉无关的岗位和采完自动评估；没有就两样都不做。
        """
        try:
            scope = CollectionRequest(
                platforms=platforms,
                queries=queries,
                cities=cities,
                per_search=per_search,
                **options,
            )
        except ValidationError as error:
            problems = "；".join(
                item["msg"].removeprefix("Value error, ")
                for item in error.errors(include_input=False, include_url=False)
            )
            return None, False, f"没有发起：{problems}"
        if scope.evaluate and model is None:
            return None, False, "没有发起：采完自动评估需要模型 Key，现在没有配置。"
        try:
            run, created = self.manager.submit(
                RunKind.collection,
                job_ids=[],
                conditions="",
                force=False,
                model=model,
                extra=scope.run_input(),
            )
        except CollectionBusy as error:
            return None, False, f"没有发起：{error}"
        return run, created, ""

    async def open_browser(self, platform: str) -> str:
        """在专用浏览器里打开一个平台的首页，让用户自己登录或完成验证。"""
        if platform not in PLATFORMS:
            return f"不支持的平台：{platform}。可选：{'、'.join(PLATFORMS)}"
        try:
            await self.manager.browser.show(PLATFORMS[platform].home_url)
        except BrowserUnavailable as error:
            return f"没有打开：{error}"
        name = PLATFORMS[platform].name
        return f"已在专用浏览器里打开{name}。请用户自己登录或完成验证，做完后重新发起采集。"

    async def cancel_task(self, run_id: str) -> str:
        """取消一次还没结束的运行。已经做完的部分（评完的岗位、存好的岗位）都保留。"""
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            if run is None:
                return f"没有编号为 {run_id} 的运行。"
            name = RUN_KIND_NAMES[run.kind]
            if run.status == RunStatus.waiting_user:  # 停着等确认的运行没有后台任务
                run_store.update_run(session, run_id, status=RunStatus.cancelled)
                return f"已经取消这次{name}。"
        if not await self.manager.cancel(run_id):
            return f"这次{name}不在进行中，不用取消。"
        return f"已经取消这次{name}。做完的部分都还在。"

    async def task_outcome(
        self, run_id: str, *, created: bool = True, show_edits: bool = False
    ) -> str:
        """一次运行现在的情况：还在做、等用户确认，还是做完了（带成果的开头）。

        show_edits：定制简历在等确认时，把每一条修改也列出来。
        """
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            if run is None:
                return f"没有编号为 {run_id} 的运行。"
            name = RUN_KIND_NAMES[RunKind(run.kind)]
            results = await self.manager.results(run)
            lines = [] if created else [f"同样的{name}任务已经在进行，没有重复发起。"]
            if run.status == RunStatus.running:
                step = f"，当前步骤：{run.step}" if run.step else ""
                lines.append(f"{name}还在进行（运行编号 {run.id}）{step}。")
            elif run.status == RunStatus.waiting_user and run.kind == RunKind.tailoring:
                pending = await self.manager.pending(run) or {}
                lines += [
                    f"{name}已经生成修改方案，共 {len(pending.get('edits', []))} 条修改，"
                    f"正在等用户逐条确认（运行编号 {run.id}）。",
                    f"思路：{pending.get('summary', '')}",
                ]
                if show_edits:
                    lines += [_edit_text(edit) for edit in pending.get("edits", [])]
            elif run.status == RunStatus.waiting_user:
                lines.append(f"{name}正在等用户处理（运行编号 {run.id}）。")
            elif run.status == RunStatus.cancelled:
                lines.append(f"{name}被取消了。")
            for result in results:
                if result.get("search"):  # 采集：一个搜索的结论
                    status = "已完成" if result["status"] == "done" else "没有完成"
                    line = f"{result['search']}：{status}。{result['reason']}"
                    if result["saved"]:
                        saved = "、".join(f"#{job_id}" for job_id in result["saved"])
                        line += f"。新增的岗位编号：{saved}"
                    lines.append(line)
                    continue
                target = f"岗位 #{result['job_id']}" if result["job_id"] else name
                line = f"{target}：{RESULT_NAMES[result['status']]}"
                if result["reason"]:
                    line += f"。{result['reason']}"
                lines.append(line)
                artifact = (
                    artifact_store.get_artifact(session, result["artifact_id"])
                    if result["artifact_id"]
                    else None
                )
                if artifact:
                    omitted = "……" if len(artifact.content_md) > DIGEST_CHARS else ""
                    lines += [
                        f"成果 #{artifact.id}《{artifact.title}》的开头"
                        f"（全文用 read_artifact 读取）：",
                        artifact.content_md[:DIGEST_CHARS] + omitted,
                    ]
            if not results and run.status == RunStatus.failed:
                lines.append(f"{name}失败：{run.error}")
            follow_up = run_store.find_follow_up(session, run.id)
            if follow_up:
                jobs = "、".join(
                    f"#{job_id}" for job_id in json.loads(follow_up.input_json)["job_ids"]
                )
                lines.append(
                    f"已经自动发起对新增岗位（{jobs}）的评估，运行编号 {follow_up.id}。"
                    "评估在后台进行，要一两分钟，做完后在岗位列表里能看到评分。"
                )
        return "\n".join(lines)

    async def task_conclusions(self, run_id: str) -> list[dict]:
        """一次运行产出的成果的开头部分，一字不改。各类报告都把结论放在最前面。

        对话里用它把专员的结论原样展示给用户：助手转述时可能走样，程序查不出来。
        """
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            if run is None:
                return []
            conclusions = []
            for result in await self.manager.results(run):
                artifact = (
                    artifact_store.get_artifact(session, result["artifact_id"])
                    if result.get("artifact_id")
                    else None
                )
                if artifact:
                    conclusions.append(
                        {
                            "run_id": run.id,
                            "job_id": artifact.job_id,
                            "artifact_id": artifact.id,
                            "title": artifact.title,
                            "text": artifact.content_md[:DIGEST_CHARS],
                            "truncated": len(artifact.content_md) > DIGEST_CHARS,
                        }
                    )
            return conclusions

    async def confirm_tailoring(
        self, run_id: str, accepted: list[int], overrides: dict[int, str] | None = None
    ) -> str:
        """提交用户接受的定制修改。成功返回空字符串，否则返回原因。必须在事件循环里调用。"""
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
        if run is None or run.kind != RunKind.tailoring:
            return f"没有编号为 {run_id} 的定制简历运行。"
        if run.status != RunStatus.waiting_user or self.manager.is_active(run_id):
            return "这次定制简历没有在等确认。"
        pending = await self.manager.pending(run)
        kinds = {edit["number"]: edit["kind"] for edit in pending["edits"]}
        try:
            answer = check_choice(accepted, overrides or {}, kinds)
        except ValueError as error:
            return f"没有提交：{error}。"
        if not self.manager.respond(run, answer):
            return "这次定制简历没有在等确认。"
        return ""


EDIT_KIND_NAMES = {"replace": "替换", "delete": "删除", "insert": "插入", "move": "移动"}


def _edit_text(edit: dict) -> str:
    """一条定制修改，给用户决定要不要接受时看。"""
    lines = [f"修改 {edit['number']}（{EDIT_KIND_NAMES[edit['kind']]}）：{edit['reason']}"]
    if edit["original"]:
        lines.append(f"  原文：{edit['original']}")
    if edit["new_text"]:
        lines.append(f"  改为：{edit['new_text']}")
    if edit["kind"] == "move":
        lines.append(f"  挪到第 {edit['after']} 行后面")
    if edit.get("fact_check") == "needs_confirmation":
        unverified = "、".join(edit.get("unverified", []))
        lines.append("  需要用户确认属实" + (f"，材料里找不到：{unverified}" if unverified else ""))
    for concern in edit.get("concerns", []):
        lines.append(f"  措辞核对提醒：{concern}")
    return "\n".join(lines)
