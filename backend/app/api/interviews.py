"""模拟面试的 WebSocket 接口。

面试本身是一次运行，图在后台由 RunManager 执行，每一轮问完就停下来等回答。
这里只做传输：把面试官逐字输出的问题推给前端，把用户的回答交还给图。
所以断线不会丢进度，重新连上会收到当前的问题和此前的问答。
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.api.deps import ChatModelDep, RunManagerDep
from app.db import artifacts as artifact_store
from app.db.models import Run, RunKind, RunStatus
from app.llm.mock_interview import interview_answer

logger = logging.getLogger(__name__)
router = APIRouter(tags=["interviews"])


def _load(websocket: WebSocket, run_id: str) -> Run | None:
    with Session(websocket.app.state.engine) as session:
        return session.get(Run, run_id)


async def _read_reply(websocket: WebSocket) -> dict:
    """等用户的一条有效消息：回答，或者结束。不合格的消息告诉前端原因，接着等。"""
    while True:
        message = await websocket.receive_json()
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "end":
            return {"end": True}
        if kind != "answer":
            problem = "消息的 type 只能是 answer 或 end"
        else:
            try:
                text = message.get("text")
                return interview_answer(text if isinstance(text, str) else "")
            except ValueError as error:
                problem = str(error)
        await websocket.send_json({"type": "error", "message": problem})


@router.websocket("/runs/{run_id}/interview")
async def interview(
    websocket: WebSocket, run_id: str, manager: RunManagerDep, model: ChatModelDep
) -> None:
    """进行一场模拟面试。先用 `POST /api/runs`（kind 为 `mock_interview`）发起，再连到这里。

    服务端发来的消息：`status` 当前步骤；`token` 面试官正在说的话（逐段）；
    `question` 一轮问完，等你回答（带轮次、完整问题和此前的问答）；
    `review` 复盘报告；`done` 面试结束；`error` 你发来的消息有问题。

    你发送的消息：`{"type": "answer", "text": "..."}` 回答当前问题；
    `{"type": "end"}` 提前结束并复盘已经回答的部分。
    """
    await websocket.accept()
    run = _load(websocket, run_id)
    if run is None or run.kind != RunKind.mock_interview:
        await websocket.send_json({"type": "error", "message": f"没有编号为 {run_id} 的模拟面试"})
        await websocket.close(code=1008)
        return

    try:
        while True:
            events = manager.events(run_id)
            if events is not None:  # 图正在执行：转发进度和面试官的话
                async for event, data in events:
                    if event == "token":
                        await websocket.send_json({"type": "token", "text": data["token"]})
                    elif event == "node":
                        await websocket.send_json({"type": "status", "label": data["label"]})

            run = _load(websocket, run_id)
            if run.status != RunStatus.waiting_user:
                break
            pending = await manager.pending(run)
            await websocket.send_json({"type": "question", **pending})
            reply = await _read_reply(websocket)
            if problem := await manager.respond_to_interview(run_id, pending["turn"], reply, model):
                await websocket.send_json({"type": "error", "message": problem})

        for result in await manager.results(run):
            if result["artifact_id"]:
                with Session(websocket.app.state.engine) as session:
                    artifact = artifact_store.get_artifact(session, result["artifact_id"])
                if artifact is None:  # 复盘已经被删了
                    continue
                await websocket.send_json(
                    {
                        "type": "review",
                        "artifact_id": artifact.id,
                        "content_md": artifact.content_md,
                    }
                )
            elif result["reason"]:
                await websocket.send_json({"type": "status", "label": result["reason"]})
        await websocket.send_json({"type": "done", "status": run.status, "error": run.error})
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("模拟面试 %s 的连接断开，进度已保存，可以重新连接", run_id)
