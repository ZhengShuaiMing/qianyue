"""运行执行器：在后台跑各种图，把进度写进 runs 表，并推给正在看进度的人。

接口收到请求后立刻返回运行编号，真正的工作在这里的后台任务里完成。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import Engine
from sqlmodel import Session

from app.collectors.base import Browser
from app.collectors.pacing import Pacer
from app.collectors.registry import PLATFORMS
from app.core.config import Settings
from app.core.context import run_id_var
from app.db import runs as run_store
from app.db.models import Run, RunKind, RunStatus
from app.graphs import (
    collection,
    communication,
    evaluation,
    interview_prep,
    mock_interview,
    profile,
    research,
    screenshot,
    tailoring,
)
from app.graphs.collection import build_collection_graph
from app.graphs.communication import build_communication_graph
from app.graphs.evaluation import build_evaluation_graph
from app.graphs.interview_prep import build_interview_prep_graph
from app.graphs.mock_interview import build_mock_interview_graph
from app.graphs.profile import build_profile_graph
from app.graphs.research import build_research_graph
from app.graphs.screenshot import build_screenshot_graph
from app.graphs.tailoring import build_tailoring_graph
from app.llm.agent_model import AgentBroker, AgentChatModel, AgentNoReply, ModelRequest
from app.rag.knowledge import KnowledgeBase
from app.tools.web import WebClient

logger = logging.getLogger(__name__)

NODE_LABELS = {
    RunKind.evaluation: evaluation.NODE_LABELS,
    RunKind.tailoring: tailoring.NODE_LABELS,
    RunKind.company_research: research.NODE_LABELS,
    RunKind.communication: communication.NODE_LABELS,
    RunKind.interview_prep: interview_prep.NODE_LABELS,
    RunKind.mock_interview: mock_interview.NODE_LABELS,
    RunKind.collection: collection.NODE_LABELS,
    RunKind.profile: profile.NODE_LABELS,
    RunKind.screenshot: screenshot.NODE_LABELS,
}
TOOL_LABELS = {
    "web_search": "联网搜索",
    "read_page": "读取网页",
    "search_materials": "检索我的材料",
    "read_report": "读取已有报告",
}


class EventLog:
    """一次运行的事件记录。订阅者总是从第一条读起，晚一点连上也不会漏掉已经发生的节点。"""

    def __init__(self) -> None:
        self._items: list[tuple[str, dict]] = []
        self._closed = False
        self._changed = asyncio.Condition()

    async def publish(self, event: str, data: dict) -> None:
        async with self._changed:
            self._items.append((event, data))
            self._changed.notify_all()

    async def close(self) -> None:
        async with self._changed:
            self._closed = True
            self._changed.notify_all()

    async def stream(self) -> AsyncIterator[tuple[str, dict]]:
        sent = 0
        while True:
            async with self._changed:
                while len(self._items) == sent and not self._closed:
                    await self._changed.wait()
                batch = self._items[sent:]
            for item in batch:
                yield item
            sent += len(batch)
            if self._closed and sent == len(self._items):
                return


def unit_count(run_input: dict) -> int:
    """一次运行里有几件事要做：采集按搜索算，能力画像只有一件，其余按岗位算。"""
    if run_input.get("combined"):
        return 1
    return len(run_input.get("searches") or run_input["job_ids"])


def unit_key(result: dict):
    """一条结论对应哪件事：采集是搜索的名字，能力画像只有一条，其余是岗位编号。"""
    return result.get("search") or result["job_id"] or "combined"


def unit_name(result: dict) -> str:
    if result.get("search"):
        return result["search"]
    return f"岗位 {result['job_id']}" if result["job_id"] else "能力画像"


def summarize(results: list[dict], total: int) -> tuple[RunStatus, str | None]:
    """根据各项的结论给出运行的最终状态。有一项没做成，运行就算失败，并写明原因。"""
    unfinished = [result for result in results if result["status"] in ("blocked", "failed")]
    if not unfinished:
        return RunStatus.succeeded, None
    if total == 1:
        return RunStatus.failed, unfinished[0]["reason"]
    details = "；".join(f"{unit_name(result)}：{result['reason']}" for result in unfinished)
    unit = "搜索" if "search" in unfinished[0] else "岗位"
    return RunStatus.failed, f"{total} 个{unit}中有 {len(unfinished)} 个没有完成。{details}"


class CollectionBusy(Exception):
    """已经有一次不同的采集在进行。专用浏览器只有一个，采集一次只做一批。"""


class RunManager:
    def __init__(
        self,
        engine: Engine,
        checkpointer: BaseCheckpointSaver,
        settings: Settings,
        knowledge: KnowledgeBase,
        web: WebClient,
        browser: Browser | None = None,
        pacer: Pacer | None = None,
    ):
        self.engine = engine
        self.checkpointer = checkpointer
        self.settings = settings
        self.knowledge = knowledge
        self.web = web
        self.browser = browser
        self.pacer = pacer or Pacer()
        self._tasks: dict[str, asyncio.Task] = {}
        self._logs: dict[str, EventLog] = {}
        self._live: dict[str, list[dict]] = {}  # 正在执行的运行里，已经有结论的岗位
        self._interview_reply_lock = asyncio.Lock()
        self._stopping = False
        # 没有模型 Key 时，经 MCP 发起的运行由用户的 Agent 代答模型调用（app/llm/agent_model.py）
        self.agent = AgentBroker(self._on_agent_request)
        with Session(engine) as session:
            interrupted = run_store.fail_interrupted(session)
        if interrupted:
            logger.info("上次停止时有 %s 个运行没有结束，已标为失败，可以恢复", interrupted)

    def _graph(self, kind: str, model: BaseChatModel | None) -> CompiledStateGraph:
        """每种运行对应一张图。"""
        if kind == RunKind.collection:
            return build_collection_graph(
                engine=self.engine,
                browser=self.browser,
                pacer=self.pacer,
                platforms=PLATFORMS,
                checkpointer=self.checkpointer,
                model=model,
            )
        common = {"engine": self.engine, "model": model, "checkpointer": self.checkpointer}
        budget = self.settings.llm_input_token_budget
        if kind == RunKind.tailoring:
            return build_tailoring_graph(**common, token_budget=budget, knowledge=self.knowledge)
        if kind == RunKind.company_research:
            return build_research_graph(**common, web=self.web)
        if kind == RunKind.communication:
            return build_communication_graph(**common, knowledge=self.knowledge)
        if kind == RunKind.interview_prep:
            return build_interview_prep_graph(
                **common, token_budget=budget, knowledge=self.knowledge, web=self.web
            )
        if kind == RunKind.profile:
            return build_profile_graph(**common)
        if kind == RunKind.mock_interview:
            return build_mock_interview_graph(**common, token_budget=budget)
        if kind == RunKind.screenshot:
            return build_screenshot_graph(**common, data_dir=self.settings.data_dir)
        return build_evaluation_graph(**common, token_budget=budget)

    @staticmethod
    def _fresh_input(run: Run) -> dict:
        """从头开始时交给图的输入。"""
        run_input = json.loads(run.input_json)
        if run.kind == RunKind.evaluation:
            return {key: run_input[key] for key in ("job_ids", "conditions", "force")}
        if run.kind == RunKind.profile:
            return {"job_ids": run_input["job_ids"], "force": run_input["force"]}
        if run.kind == RunKind.collection:
            return {
                "searches": run_input["searches"],
                "filters": run_input.get("filters") or {},
            }
        if run.kind == RunKind.screenshot:
            return {key: run_input[key] for key in ("upload", "target", "job_ids", "fields")}
        # 其余类型一次只针对一个岗位。
        single = {"job_id": run_input["job_ids"][0], "force": run_input["force"]}
        if run.kind != RunKind.tailoring:
            single["conditions"] = run_input["conditions"]
        return single

    def _config(self, run_id: str, model: BaseChatModel | None = None) -> dict:
        # Agent 代答时一次只问一件事：对面是一个对话，并行的请求只会让它来回切换
        agent = isinstance(model, AgentChatModel)
        return {
            # 运行编号同时是 LangGraph 的线程编号，Checkpoint 按它存取。
            "configurable": {"thread_id": run_id},
            # 一次运行里同时评估的岗位数上限。
            "max_concurrency": 1 if agent else self.settings.max_concurrent_jobs,
            # 图最多走多少步。采集每个搜索算一步，默认的 25 步不够。
            "recursion_limit": 100,
        }

    def submit(
        self,
        kind: RunKind,
        *,
        job_ids: list[int],
        conditions: str,
        force: bool,
        model: BaseChatModel | None,
        extra: dict | None = None,
    ) -> tuple[Run, bool]:
        """发起一次运行，返回 (运行, 是否新发起)。必须在事件循环里调用。

        同样的任务已经在进行或在等用户处理时，返回那一次，不重复发起（幂等）。
        force 表示用户明确要重做，总是新开一次。
        extra 是这种运行特有的输入：采集的搜索列表、数量、筛选条件。采集可以没有模型
        （model 传 None）：那样就不做相关性筛选，也不能采完自动评估。
        """
        run_input = {"job_ids": job_ids, "conditions": conditions, "force": force, **(extra or {})}
        if kind in (RunKind.profile, RunKind.screenshot):
            run_input["combined"] = True  # 几个岗位只有一条结论；识别截图时岗位是读完才有的
        with Session(self.engine) as session:
            existing = None if force else run_store.find_active(session, kind, run_input)
            if existing:
                return existing, False
            if kind == RunKind.collection and run_store.has_active(session, kind):
                raise CollectionBusy("已经有一次采集在进行，等它结束或取消后再发起")
            run = run_store.create_run(session, kind, run_input)
        self.start(run, model)
        return run, True

    def start(self, run: Run, model: BaseChatModel | None) -> None:
        """在后台从头开始执行，立刻返回。必须在事件循环里调用。"""
        self._launch(run, model, self._fresh_input(run))

    def _launch(self, run: Run, model: BaseChatModel | None, graph_input) -> None:
        self._logs[run.id] = EventLog()
        self._tasks[run.id] = asyncio.create_task(self._execute(run, model, graph_input))

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    async def resume(self, run: Run, model: BaseChatModel | None) -> bool:
        """继续一个没跑完的运行。图已经走完、没有可继续的内容时返回 False。"""
        state = await self._graph(run.kind, None).aget_state(self._config(run.id))
        if self.is_active(run.id):
            return False  # 读断点期间，另一个请求可能已经开始恢复。
        total = unit_count(json.loads(run.input_json))
        if not state.next and len(state.values.get("results", [])) == total:
            return False
        self._update(run.id, status=RunStatus.running, error=None)
        # 有断点就从断点继续：输入传 None，LangGraph 读出 Checkpoint，只执行没完成的部分。
        # 刚开始就被打断、还没存下任何断点时，从头开始。
        self._launch(run, model, None if state.next else self._fresh_input(run))
        return True

    async def pending(self, run: Run) -> dict | None:
        """运行停下来等用户处理的内容（interrupt 交出来的值）。没有在等就是 None。"""
        state = await self._graph(run.kind, None).aget_state(self._config(run.id))
        for task in state.tasks:
            if task.interrupts:
                return task.interrupts[0].value
        return None

    async def values(self, run: Run) -> dict:
        """运行停下时 State 里的内容（例如定制简历的修改方案和基础简历）。"""
        state = await self._graph(run.kind, None).aget_state(self._config(run.id))
        return state.values

    def respond(self, run: Run, answer: dict, model: BaseChatModel | None = None) -> bool:
        """把用户的答复交还给图，从停下的节点继续。已经在执行（别的请求刚交过答复）时返回 False。

        调用方检查「在等用户」之后还要读断点，期间网页和 MCP 可能同时提交，所以在这里再拦一次；
        这里到启动之间没有 await，不会被插队。
        定制简历在确认之后不再调用模型，不用传 model；模拟面试还要接着提问，需要传。
        """
        if self.is_active(run.id):
            return False
        self._update(run.id, status=RunStatus.running, error=None)
        self._launch(run, model, Command(resume=answer))
        return True

    async def respond_to_interview(
        self, run_id: str, turn: int, reply: dict, model: BaseChatModel
    ) -> str | None:
        """核对收到回答的题号；MCP 和 WebSocket 不能同时提交同一题。"""
        async with self._interview_reply_lock:
            with Session(self.engine) as session:
                run = session.get(Run, run_id)
                if run is None or run.kind != RunKind.mock_interview:
                    return f"没有编号为 {run_id} 的模拟面试"
                if run.status != RunStatus.waiting_user or self.is_active(run_id):
                    return "这场面试当前没有在等回答"
                pending = await self.pending(run)
                session.refresh(run)  # 查断点期间，这场面试可能被取消。
                if run.status != RunStatus.waiting_user:
                    return "这场面试当前没有在等回答"
                if not pending or pending["turn"] != turn:
                    return "这不是当前的问题，请先查看最新题目"
                self.respond(run, reply, model)
        return None

    async def cancel(self, run_id: str) -> bool:
        """取消并等它停下来。不在执行时返回 False。"""
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.wait([task])
        return True

    def events(self, run_id: str) -> AsyncIterator[tuple[str, dict]] | None:
        """正在执行的运行返回它的事件流；不在执行返回 None。"""
        log = self._logs.get(run_id)
        return log.stream() if log else None

    def done_count(self, run_id: str) -> int:
        """正在执行的运行里已经有结论的项数。刚发起、还没开始执行时是 0。"""
        return len(self._live.get(run_id, []))

    async def results(self, run: Run) -> list[dict]:
        """各岗位的结论。执行中读内存里的，其余时候读 Checkpoint。

        Checkpoint 要等一批岗位全部结束（或运行被打断）才看得到其中已完成的结论，
        所以执行期间以内存为准。
        """
        if run.id in self._live:
            return list(self._live[run.id])
        return await self._saved_results(run)

    async def _saved_results(self, run: Run) -> list[dict]:
        state = await self._graph(run.kind, None).aget_state(self._config(run.id))
        return list(state.values.get("results", []))

    async def shutdown(self) -> None:
        """服务停止：打断还在跑的运行。它们的状态留给下次启动时处理。"""
        self._stopping = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, run: Run, model: BaseChatModel | None, graph_input) -> None:
        run_id = run.id
        run_id_var.set(run_id)  # 这个后台任务里的每条日志都带上运行编号
        log = self._logs[run_id]
        labels = NODE_LABELS[run.kind]
        # 由用户的 Agent 代答时，任务列表里的步骤注明这一点，用户知道要去 Agent 那边看
        by_agent = "（由你的 Agent 代答）" if isinstance(model, AgentChatModel) else ""
        total = unit_count(json.loads(run.input_json))
        waiting = False
        try:
            # 中断前已经有结论的岗位。恢复时 LangGraph 会把它们的结果再报一遍，要跳过。
            results = self._live[run_id] = await self._saved_results(run)
            done = {unit_key(result) for result in results}
            logger.info("运行开始：%s，共 %s 项，此前已完成 %s 项", run.kind, total, len(done))
            async for namespace, mode, chunk in self._graph(run.kind, model).astream(
                graph_input,
                self._config(run_id, model),
                stream_mode=["custom", "updates"],
                subgraphs=True,
            ):
                if mode == "custom" and "token" in chunk:  # 模型正在逐字输出（模拟面试的提问）
                    await log.publish("token", chunk)
                elif mode == "custom" and "progress" in chunk:  # 一句话的进度（采集）
                    self._update(run_id, step=chunk["progress"])
                    await log.publish("progress", chunk)
                elif mode == "custom" and "tool" in chunk:  # Agent 调用了一个工具
                    await log.publish("tool", chunk | {"label": TOOL_LABELS[chunk["tool"]]})
                elif mode == "custom":  # 某个岗位进入了某个节点
                    label = labels[chunk["node"]] + by_agent
                    if total == 1:
                        self._update(run_id, step=label)
                    await log.publish("node", chunk | {"label": label})
                elif namespace:  # 子图内部节点的输出，不用处理
                    continue
                elif "__interrupt__" in chunk:  # 图停下来等用户
                    waiting = True
                else:
                    for update in chunk.values():  # 顶层节点的输出里带着岗位的结论
                        for result in (update or {}).get("results", []):
                            if unit_key(result) in done:
                                continue
                            done.add(unit_key(result))
                            results.append(result)
                            if total > 1:
                                self._update(run_id, step=f"已完成 {len(done)}/{total}")
                            await log.publish("job", result)
            if waiting:
                status, error = RunStatus.waiting_user, None
            else:
                status, error = summarize(await self._saved_results(run), total)
        except asyncio.CancelledError:
            if self._stopping:
                await log.close()
                raise
            status, error = RunStatus.cancelled, None
        except AgentNoReply as exc:
            status, error = RunStatus.failed, str(exc)
        except Exception as exc:  # noqa: BLE001  后台任务没有调用方，错误只能记进 runs 表
            logger.exception("运行出错")
            status, error = RunStatus.failed, f"{type(exc).__name__}: {exc}"

        # 收尾出错也要关掉事件流、清掉记录，不然这次运行会一直显示「进行中」
        try:
            self._update(run_id, status=status, error=error)
            logger.info("运行结束：status=%s error=%s", status.value, error)
            follow_up = self._evaluate_collected(run, model, status)
            if follow_up:
                await log.publish("run", follow_up)
            await log.publish("done", {"status": status.value, "error": error})
        finally:
            await log.close()
            del self._tasks[run_id], self._logs[run_id]
            self._live.pop(run_id, None)
        # 等用户回答后还会接着问 Agent，留着上一段对话，之后只发增量
        if status != RunStatus.waiting_user:
            self.agent.forget(run_id)

    async def _on_agent_request(self, request: ModelRequest) -> None:
        """图在等 Agent 代答：正在跟进度的 MCP 工具据此把请求交给 Agent。"""
        log = self._logs.get(request.run_id)
        if log:
            await log.publish("model_request", {"request_id": request.id})

    def _evaluate_collected(self, run: Run, model, status: RunStatus) -> dict | None:
        """采集时选了「采完就评估」：把这次新增的岗位交给评估，返回新发起的那次运行。

        采集被取消时不评估。某个平台停下了，其他平台采到的照常评估。
        """
        run_input = json.loads(run.input_json)
        if run.kind != RunKind.collection or not run_input.get("evaluate") or model is None:
            return None
        if status == RunStatus.cancelled:
            return None
        saved = [job_id for result in self._live.get(run.id, []) for job_id in result["saved"]]
        if not saved:
            return None
        evaluation, _ = self.submit(
            RunKind.evaluation,
            job_ids=saved,
            conditions="",
            force=False,
            model=model,
            extra={"after": run.id},  # 记下是接着哪次采集发起的
        )
        logger.info("采集结束，自动评估新增的 %s 个岗位", len(saved))
        return {
            "run_id": evaluation.id,
            "kind": RunKind.evaluation.value,
            "name": "岗位评估",
            "job_ids": saved,
        }

    def _update(self, run_id: str, **changes) -> None:
        with Session(self.engine) as session:
            run_store.update_run(session, run_id, **changes)
