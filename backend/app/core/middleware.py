import logging
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import request_id_var

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """为每个 HTTP 请求生成编号：放进 ContextVar、写回响应头、记录一行访问日志。

    用纯 ASGI 写法而不是 BaseHTTPMiddleware，流式响应（SSE）期间编号也不会丢。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("%s %s -> %s %.0fms", scope["method"], scope["path"], status, elapsed_ms)
            request_id_var.reset(token)
