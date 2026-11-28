"""总控 Agent 的工具：查看岗位和成果、写入用户提供的内容、把任务交给专员。

真正的读写在 app/tools/operations.py，MCP Server 也用它。这里多做两件只有对话里才有的事：

- 写入的正文不让模型转述。JD 和聊天内容必须能在用户的消息里逐字找到，程序保存的是
  用户消息里的原文，不是模型抄写的版本。用户发了截图时，截图里的字程序对不了，
  这时收下模型照截图抄的完整内容（不收「开头……结尾」的省略写法）。
- 交接给专员后，把专员的进度推给正在看对话的人。
- 把用户讲的经历整理成 STAR 时，数字和技术词必须出自用户在对话里说过的话，保存前等用户确认。
"""

import asyncio
import logging
from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from sqlalchemy import Engine

from app.db.models import DOCUMENT_KIND_NAMES, RUN_KIND_NAMES, DocumentKind, RunKind
from app.graphs.runner import RunManager
from app.llm.factcheck import Item, check_wording
from app.llm.facts import unsupported
from app.llm.parsing import InvalidOutput
from app.llm.screenshot import ImagesRejected, images_in, read_screenshots
from app.llm.star import PARTS, render_entry
from app.tools.operations import Workspace

logger = logging.getLogger(__name__)
ELLIPSIS = "……"

Specialist = Literal[
    "evaluation", "tailoring", "company_research", "communication", "interview_prep", "profile"
]
SettableStage = Literal[
    "not_applied",
    "applied",
    "assessment",
    "interviewed",
    "offer",
    "rejected",
    "withdrawn",
]


def _is_summary(message) -> bool:
    """这条消息是不是较早的对话压缩成的摘要。摘要是模型写的，不是用户的原话。"""
    return message.additional_kwargs.get("lc_source") == "summarization"


def find_original(messages: list, quoted: str) -> str | None:
    """在用户的消息里找到 quoted 对应的原文。找不到返回 None。

    quoted 可以是逐字的原文，也可以是「开头一句……结尾一句」：这时返回用户消息里从开头到
    结尾的整段原文。比较时忽略空白的差异。最近的消息优先。
    """
    head, _, tail = quoted.partition(ELLIPSIS)
    head, tail = "".join(head.split()), "".join(tail.split())
    if not head:
        return None
    for message in reversed(messages):
        if message.type != "human" or _is_summary(message):
            continue
        text = message.text
        positions = [index for index, char in enumerate(text) if not char.isspace()]
        packed = "".join(text[index] for index in positions)
        start = packed.find(head)
        if start < 0:
            continue
        end = start + len(head)
        if tail:
            found = packed.find(tail, end)
            if found < 0:
                continue
            end = found + len(tail)
        return text[positions[start] : positions[end - 1] + 1]
    return None


# 整理经历时已经核对过措辞的内容：排好的文字 → 提醒。用户确认是另一次请求，工具会重新执行，
# 所以放在进程里而不是某一次请求里。服务重启后最多重新核对一次。
_WORDING_CHECKED: dict[str, list[str]] = {}


def _sent_images(messages: list) -> bool:
    """用户在这段对话里发过截图（摘要之后还留着的）。"""
    return bool(_latest_images(messages))


def _latest_images(messages: list) -> list[str]:
    """用户最近一次发的那几张截图。没发过是空列表。"""
    for message in reversed(messages):
        if message.type == "human" and (images := images_in(message)):
            return images
    return []


UNCHECKED = (
    "\n\n这些数字和技术词在你说过的话里找不到，可能出自你发的截图，程序没法核对，"
    "请对照截图看一遍：{terms}"
)


def quoted_or_copied(messages: list, quoted: str) -> str | None:
    """用户消息里的原文；找不到时，如果用户发过截图，收下模型照截图抄的完整内容。"""
    found = find_original(messages, quoted)
    if found is None and _sent_images(messages) and ELLIPSIS not in quoted:
        return quoted.strip()
    return found


NOT_QUOTED = (
    "没有保存：{what}必须是用户消息里的原文，程序在用户的消息里找不到你给的这段文字。"
    "请逐字照抄，不要改写、总结或补全；原文较长时写「开头一句……结尾一句」。"
    "内容在用户发的截图里时，照截图把完整内容抄出来，不能用省略写法。"
    "如果用户还没有提供{what}，请先向用户要。"
)


def build_workspace_tools(
    engine: Engine, manager: RunManager, model: BaseChatModel
) -> list[BaseTool]:
    """model 是专员用的模型：交接工具发起运行时把它交给专员的图。"""
    workspace = Workspace(engine, manager)

    # ---------- 读取 ----------

    @tool
    def list_jobs(
        keyword: str = "",
        category: Literal["", "not_started", "in_progress", "closed"] = "",
    ) -> str:
        """查看岗位列表和整体进展。keyword 按公司或职位名筛选；category 按类别筛选：
        not_started 未开始（还没投），in_progress 进行中（投了还没结果），closed 已结束。
        都不填就是全部。
        返回每个岗位的编号、公司、职位、应聘阶段、处理进度（JD 不完整、待评估、已评估和评分），
        以及正在进行或等用户处理的任务。"""
        return workspace.jobs_overview(keyword, category)

    @tool
    def get_job(job_id: int) -> str:
        """查看一个岗位的详情：基本信息、当前阶段、JD、已有的成果、沟通记录条数和相关任务。
        要回答关于某个岗位的问题，或者在交给专员之前确认岗位情况时用它。"""
        return workspace.job_detail(job_id)

    @tool
    def read_artifact(artifact_id: int) -> str:
        """读取一份成果的正文（评估报告、公司研究、沟通建议、定制简历、面试准备、模拟面试复盘）。
        成果编号从 get_job 或 hand_off 的结果里拿。"""
        return workspace.artifact_text(artifact_id)

    @tool
    async def search_workspace(
        query: str,
        types: list[Literal["material", "job", "communication", "artifact", "chat"]] | None = None,
    ) -> str:
        """在迁跃存的全部内容里检索：用户的材料、岗位（含 JD）、沟通记录、全部成果（评估、定制简历、
        公司研究、沟通建议、面试准备、模拟面试复盘、能力画像），以及以前和助手的对话（chat）。
        用户问「哪些岗位要 Kafka」
        「上次研究的那家做 RAG 的公司是哪个」「面试准备里讲过什么系统设计」这类要翻内容才能回答的
        问题时用；只按公司名、职位名找岗位用 list_jobs。query 写要找的意思或关键词；
        types 只查某几类，不填查全部。按意思和字面一起找，返回片段和出处。"""
        return await asyncio.to_thread(workspace.search_workspace, query, types)

    # ---------- 写入 ----------

    @tool
    async def add_job(
        runtime: ToolRuntime,
        jd_text: str = "",
        url: str = "",
        company: str = "",
        title: str = "",
        city: str = "",
        salary: str = "",
    ) -> str:
        """把用户提供的岗位保存到岗位列表。jd_text 是用户粘贴的 JD 原文：逐字照抄，
        原文较长时只写「开头一句……结尾一句」，程序会从用户的消息里取出完整原文。
        JD 在用户发的截图里时，照截图逐字抄出完整的 JD，不能用省略写法。
        url 是用户给的岗位链接。两者至少有一个。company、title、city、salary 从 JD 或截图里
        能直接读到就填（招聘页的公司名常在职位名下面那几行），读不到留空，不要猜。
        同一份 JD 或同一个链接重复保存只会有一条。"""
        messages = runtime.state["messages"]
        jd = ""
        if jd_text.strip():
            jd = quoted_or_copied(messages, jd_text)
            if jd is None:
                return NOT_QUOTED.format(what=" JD ")
        if url.strip() and quoted_or_copied(messages, url.strip()) is None:
            return "没有保存：链接必须是用户消息里给出的，不要自己编。"
        # 从截图存岗位时，公司名、职位名容易漏。用添加岗位页同一套读图把空着的几项补上
        images = _latest_images(messages)
        if images and not (company.strip() and title.strip()):
            try:
                read = await read_screenshots(model, "job", images)
            except (ImagesRejected, InvalidOutput) as error:
                logger.warning("从截图补岗位信息没有成功：%s", error)
            else:
                company = company.strip() or read.company.strip()
                title = title.strip() or read.title.strip()
                city = city.strip() or read.city.strip()
                salary = salary.strip() or read.salary.strip()
        return workspace.add_job(
            jd_text=jd, url=url, company=company, title=title, city=city, salary=salary
        )

    @tool
    def save_communication(
        runtime: ToolRuntime,
        job_id: int,
        speaker: Literal["recruiter", "me", "transcript"],
        text: str,
    ) -> str:
        """把用户粘贴的招聘聊天内容保存为这个岗位的沟通记录。speaker：recruiter 是招聘方
        说的一条，me 是用户自己说的一条，transcript 是原样粘贴的一整段对话。text 逐字照抄
        用户粘贴的内容，较长时只写「开头一句……结尾一句」；内容在截图里时，照截图逐字抄出
        完整内容，每条前面写「招聘方：」或「我：」。同样的内容重复保存只会有一条。"""
        original = quoted_or_copied(runtime.state["messages"], text)
        if original is None:
            return NOT_QUOTED.format(what="聊天内容")
        return workspace.save_communication(job_id, speaker, original)

    @tool
    def change_stage(
        job_id: int, stage: SettableStage, note: str = "", happened_on: str = ""
    ) -> str:
        """修改岗位的应聘阶段。只有用户明确说了进展（例如「我投了」「面完了」「被拒了」）
        才调用。执行前系统会让用户确认。stage：not_applied 未投递，applied 已投递，
        assessment 已笔试（含测评），interviewed 已面试（等结果也算），offer 已获 Offer，
        rejected 未通过（对方说不合适），withdrawn 放弃（用户不考虑了、拒了 Offer）。
        note 用一句话写明依据。happened_on 是这一步发生的日期（YYYY-MM-DD）：用户说了哪天
        （例如「昨天投的」）就按今天的日期推算出来填上；没说就留空，按今天记。
        「待评估」「已评估」不是阶段，是按有没有评估报告算出来的，不用改。"""
        return workspace.change_stage(job_id, stage, note, happened_on)

    # ---------- 整理经历 ----------

    @tool
    async def save_star(
        runtime: ToolRuntime,
        title: str,
        situation: str,
        task: str,
        action: str,
        result: str,
    ) -> str:
        """把用户在对话里讲的一段经历整理成 STAR，存进经历库。title 是这段经历的名字。
        situation 情境：当时的背景。task 任务：用户自己要解决什么。action 行动：用户自己做了什么。
        result 结果：最后怎么样。四个部分都要用户讲过才调用，缺哪个先问用户，不要自己补。
        只能用用户说过的事实，照他的说法写：他说「参与」就写参与，不要写成负责；他没说结果的
        数字就不写数字。执行前系统会把整理好的内容给用户看，等他确认。同名的经历会被替换。"""
        parts = {"situation": situation, "task": task, "action": action, "result": result}
        missing = [name for key, name in PARTS if len(parts[key].strip()) < 4]
        if not title.strip() or missing:
            lacking = "、".join((["名字"] if not title.strip() else []) + missing)
            return f"没有保存：缺少{lacking}。请先向用户问清楚，不要自己补。"
        entry = render_entry(title, parts)
        said = "\n".join(
            message.text
            for message in runtime.state["messages"]
            if message.type == "human" and not _is_summary(message)
        )
        invented = unsupported(entry, said)
        unchecked = invented if _sent_images(runtime.state["messages"]) else []
        if invented and not unchecked:
            return (
                f"没有保存：整理的内容里出现了用户没有说过的数字或技术词：{'、'.join(invented)}。"
                "只能用用户在这段对话里说过的；确实需要的话先问用户。"
            )
        # 措辞有没有超出用户的原话（例如把「参与」写成「负责」），让模型再对照一次，
        # 发现的问题和内容一起给用户看，由他决定。用户答复后这个工具会从头再执行一遍，
        # 所以记下核对结果，不重复调用模型。
        if entry not in _WORDING_CHECKED:
            if len(_WORDING_CHECKED) >= 50:
                _WORDING_CHECKED.clear()
            [found] = await check_wording(
                model, material="（没有）", items=[Item(basis=said, text=entry)], label="整理经历"
            )
            _WORDING_CHECKED[entry] = found
        concerns = _WORDING_CHECKED[entry]
        description = f"把下面这段经历存进经历库：\n\n{entry}"
        if concerns:
            description += "\n\n措辞提醒（这些说法和你的原话不完全一样）：\n" + "\n".join(
                f"- {concern}" for concern in concerns
            )
        if unchecked:
            description += UNCHECKED.format(terms="、".join(unchecked))
        answer = interrupt({"name": "save_star", "description": description})
        if answer.get("type") != "approve":
            return "用户没有确认，这段经历没有保存。问问他哪里要改，不要原样再存一次。"
        return workspace.save_star(title.strip(), entry)

    # ---------- 整理材料 ----------

    @tool
    def read_material(kind: Literal["resume", "supplement", "star"]) -> str:
        """读用户材料的当前版本全文。resume 基础简历，supplement 补充资料（求职方向、期望城市
        和薪资、能否出差、到岗时间、作品和证书），star STAR 经历库。
        和用户一起改材料之前先读一遍。"""
        return workspace.read_material(kind)

    @tool
    async def save_material(
        runtime: ToolRuntime, kind: Literal["resume", "supplement"], content_md: str
    ) -> str:
        """把和用户一起整理好的基础简历（resume）或补充资料（supplement）存成新版本，整份替换。
        content_md 是完整的 Markdown：先用 read_material 读现在的版本，在它上面改，用户没让动的
        内容原样保留。只能写用户说过的事实和原来材料里有的内容，他没说的经历、数字、技能不要补。
        执行前系统会把全文给用户看，等他确认。旧版本会留着，可以在材料页恢复。"""
        if len(content_md.strip()) < 20:
            return "没有保存：内容太少了。先和用户把内容聊完整。"
        said = "\n".join(
            message.text
            for message in runtime.state["messages"]
            if message.type == "human" and not _is_summary(message)
        )
        invented = unsupported(content_md, said + "\n" + workspace.current_material(kind))
        # 对话里有截图时（例如发了简历截图），对不上的词可能出自截图：不拒绝，交给用户核对
        unchecked = invented if _sent_images(runtime.state["messages"]) else []
        if invented and not unchecked:
            return (
                f"没有保存：内容里出现了用户没有说过、原来材料里也没有的数字或技术词："
                f"{'、'.join(invented)}。只能用用户说过的；确实需要的话先问用户。"
            )
        name = DOCUMENT_KIND_NAMES[DocumentKind(kind)]
        description = f"把{name}存成新版本：\n\n{content_md.strip()}"
        if unchecked:
            description += UNCHECKED.format(terms="、".join(unchecked))
        answer = interrupt({"name": "save_material", "description": description})
        if answer.get("type") != "approve":
            return f"用户没有确认，{name}没有保存。问问他哪里要改，不要原样再存一次。"
        return workspace.save_material(kind, content_md.strip())

    # ---------- 交接给专员 ----------

    @tool(response_format="content_and_artifact")
    async def hand_off(
        runtime: ToolRuntime,
        specialist: Specialist,
        job_ids: list[int],
        request: str = "",
        redo: bool = False,
    ) -> tuple[str, list[dict]]:
        """把任务交给专员去做，等它做完，拿回结论。专员有六个：
        evaluation 岗位评估（对照简历做八维评分，可以一次给多个岗位）；
        tailoring 定制简历（生成逐条修改方案，然后停下来等用户确认）；
        company_research 公司研究（联网查这家公司，一两分钟）；
        communication 沟通建议（分析已保存的沟通记录并起草消息；没有记录时写首次招呼）；
        interview_prep 面试准备（预测问题并整理回答要点）；
        profile 能力画像（把 2–15 个岗位的 JD 汇总成「这类岗位普遍要什么、用户差在哪」，
        只产出一份成果，不属于某一个岗位）。
        评估可以给多个岗位，能力画像要给 2–15 个，其余一次只能给一个岗位。
        request 写用户这次的特别要求，没有就留空。
        输入没变时专员会直接返回已有的成果；只有用户明确要求重做才把 redo 设为 true。"""
        kind = RunKind(specialist)
        run, created, problem = workspace.start_task(kind, job_ids, request, redo, model)
        if run is None:
            return problem.replace("没有发起", "没有交接"), []
        writer = runtime.stream_writer
        writer({"type": "run", "run_id": run.id, "kind": kind.value, "name": RUN_KIND_NAMES[kind]})
        events = manager.events(run.id)
        if events is not None:  # 运行在后台执行，这里只是跟着看进度，等它结束或停下
            async for event, data in events:
                if event == "node":
                    writer({"type": "progress", "run_id": run.id, "label": data["label"]})
                elif event == "tool":
                    label = f"{data['label']}：{data['detail']}"
                    writer({"type": "progress", "run_id": run.id, "label": label})
        outcome = await workspace.task_outcome(run.id, created=created)
        if "等用户逐条确认" in outcome:
            outcome += "\n你不能替用户确认。请告诉用户去确认修改，确认后才会生成定制简历。"
        # 成果的结论由程序原样展示给用户，不经过模型转述。
        conclusions = await workspace.task_conclusions(run.id)
        for conclusion in conclusions:
            writer({"type": "result", **conclusion})
        if conclusions:
            outcome += (
                "\n\n上面这些成果的开头已经原样显示给用户了。你的回答里不要复述、概括或改写其中的"
                "结论、评分和建议，只说做完了、成果编号，以及需要用户接下来做什么。"
            )
        # 结论同时存进这条工具结果里，对话历史接口靠它在刷新页面后重新显示。
        return outcome, conclusions

    # ---------- 采集 ----------

    @tool
    async def collect_jobs(
        runtime: ToolRuntime,
        platforms: list[Literal["51job", "liepin", "zhaopin", "boss"]],
        queries: list[str],
        cities: list[str],
        per_search: int = 10,
        per_platform: dict[str, int] | None = None,
        min_salary: int | None = None,
        years: int | None = None,
        exclude: list[str] | None = None,
        evaluate: bool = False,
        list_only: bool = False,
    ) -> str:
        """到招聘平台上搜索岗位并保存到岗位列表，等它做完，拿回结果。平台、关键词、城市都要
        用户明确说过，不要替用户决定。platforms：51job 前程无忧，liepin 猎聘，zhaopin 智联招聘，
        boss BOSS 直聘。queries 是搜索关键词，cities 是城市的中文名。每一种「平台 × 关键词 × 城市」
        是一个搜索，per_search 是每个搜索新增多少个岗位（1–20，默认 10），一次总共不超过 100 个。
        采集在后台进行。读取完整 JD 时，BOSS 直聘和猎聘需要逐条打开详情页。
        已经保存过的岗位不会重复读取。每个平台 24 小时最多新增 200 个。
        各平台想要的数量不同时用 per_platform，例如 {"boss": 15, "51job": 20}，没写到的平台
        用 per_search。
        筛选条件只在用户说了才填：min_salary 最低月薪（单位千元，「20K 以上」填 20），
        years 用户的工作年限（岗位要求明显更高的会跳过），exclude 职位名里不要出现的词
        （例如「实习」「外包」）。和关键词明显无关的岗位会自动跳过。
        evaluate：用户说了「找到就评估」时设为 true，采完会自动评估新增的岗位。
        平台要求登录或验证时会停下，并把那个页面在专用浏览器里打开，让用户自己去处理。
        list_only：用户明确说不想登录、只要先存下列表时设为 true，这时不读详情，
        没有职位描述的岗位会标为「JD 不完整」，要告诉用户补上 JD 后才能评估。"""
        run, created, problem = workspace.start_collection(
            platforms,
            queries,
            cities,
            per_search,
            model=model,
            per_platform=per_platform or {},
            min_salary=min_salary,
            years=years,
            exclude=exclude or [],
            evaluate=evaluate,
            list_only=list_only,
        )
        if run is None:
            return problem
        writer = runtime.stream_writer
        name = RUN_KIND_NAMES[RunKind.collection]
        writer({"type": "run", "run_id": run.id, "kind": RunKind.collection.value, "name": name})
        events = manager.events(run.id)
        if events is not None:
            async for event, data in events:
                if event == "progress":
                    writer({"type": "progress", "run_id": run.id, "label": data["progress"]})
                elif event == "run":  # 采完自动发起的评估
                    writer({"type": "run", **data})
        return await workspace.task_outcome(run.id, created=created)

    @tool
    async def open_platform_browser(platform: Literal["51job", "liepin", "zhaopin", "boss"]) -> str:
        """在专用浏览器里打开一个招聘平台并带到前面，让用户提前登录。采集因为登录或验证停下时
        页面已经自动打开，不用再调用。你不能替用户登录，也不能处理验证码。"""
        return await workspace.open_browser(platform)

    return [
        list_jobs,
        search_workspace,
        get_job,
        read_artifact,
        add_job,
        save_communication,
        change_stage,
        save_star,
        read_material,
        save_material,
        hand_off,
        collect_jobs,
        open_platform_browser,
    ]
