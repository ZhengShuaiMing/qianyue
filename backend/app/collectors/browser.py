"""专用浏览器：用系统里已安装的 Chrome，加一个单独的用户目录（data/browser/）。

它和你日常用的 Chrome 互不影响：不读你的登录状态、书签和历史。平台要求登录或验证时，
由你在这个专用窗口里自己完成，迁跃不处理验证码，也不保存或导出 Cookie。

直接用 CDP（Chrome 的调试协议）和要读的那一个标签页通信，不经过 Playwright 这类自动化库。
只用四个命令：打开网络监听、打开地址、读接口响应、在页面里执行一段脚本
（滚动加载的列表页多一个焦点仿真，见下）。
每次都是新开一个标签页 → 连上它 → 用完 → 断开并关掉它。读详情时一个标签页只读一个页面；
读搜索结果时一个搜索用一个标签页，翻页在页面里点「下一页」。
不采集的时候，浏览器上没有任何调试连接，你自己开着的标签页也从不被连接。

采集是静默的：标签页开在后台，不切换标签页，不把窗口带到前面，窗口最小化
着也照常读。浏览器没在运行时，启动它不弹窗口，只建一个最小化的窗口放这些标签页。
只有 show（请你登录或完成验证）才把浏览器带到前面。
后台的页面是「不可见」的：能加载、能读文字、能点击，但不响应滚动。靠滚动加载下一批的平台
（BOSS 直聘、智联），它们的列表页要开启调试协议的「焦点仿真」
（Emulation.setFocusEmulationEnabled）：页面仍然在后台，但像在前台一样渲染和响应滚动。只在这两个平台的列表页开启，详情页和别的平台不开。

为什么不用自动化库：
自动化库连上浏览器时会自动启用调试协议里的 Runtime 事件订阅（`Runtime.enable`），
BOSS 直聘发现后几秒内把页面清空。迁跃本来就用不到这些订阅，所以不启用。
除了上面说的焦点仿真，不做别的：不改浏览器指纹，不注入脚本改写页面，不模拟人的鼠标轨迹
和随机滚动，不主动向平台发请求。翻页用的是页面自己的翻页方式。
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.collectors.base import BrowserUnavailable, Captured, PageResult, Ready

logger = logging.getLogger(__name__)

CHROME_CANDIDATES = {
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # 只为当前用户安装的 Chrome 在这里
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ],
}
NAVIGATION_TIMEOUT = 30.0
COMMAND_TIMEOUT = 15.0
SETTLE_SECONDS = 20.0  # 页面打开后，最多再等这么久让内容加载出来
POLL_SECONDS = 0.5
MAX_BODY_CHARS = 2_000_000

# 读页面现在的样子。%s 处是平台适配器给的读取脚本（一个函数），没有就是 null。
SNAPSHOT = """(() => {
  let data = null;
  try { const read = %s; data = read ? read() : null; } catch (error) { data = null; }
  return {
    url: location.href,
    state: document.readyState,
    title: document.title,
    text: document.body ? document.body.innerText : '',
    data: data === undefined ? null : data,
  };
})()"""


def find_chrome(configured: Path | None) -> str:
    if configured:
        if not configured.exists():
            raise BrowserUnavailable(f"QIANYUE_CHROME_PATH 指向的文件不存在：{configured}")
        return str(configured)
    for candidate in CHROME_CANDIDATES.get(sys.platform, []):
        if Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if found := shutil.which(name):
            return found
    raise BrowserUnavailable(
        "没有找到 Chrome。请先安装 Chrome，或在 backend/.env 里用 QIANYUE_CHROME_PATH 指定它的位置"
    )


class CommandFailed(Exception):
    """浏览器拒绝了一条命令。多半是页面正在跳转，稍后再读就好。"""


class TabSession:
    """和一个标签页的调试连接：发命令、等回应，把页面的网络事件交给 on_event。"""

    def __init__(self, socket, on_event: Callable[[str, dict], None]) -> None:
        self._socket = socket
        self._on_event = on_event
        self._waiting: dict[int, asyncio.Future] = {}
        self._last_id = 0

    async def call(
        self, method: str, params: dict | None = None, timeout: float = COMMAND_TIMEOUT
    ) -> dict:
        self._last_id += 1
        number = self._last_id
        reply = asyncio.get_running_loop().create_future()
        self._waiting[number] = reply
        try:
            await self._socket.send(
                json.dumps({"id": number, "method": method, "params": params or {}})
            )
            message = await asyncio.wait_for(reply, timeout)
        except TimeoutError:
            raise BrowserUnavailable(f"专用浏览器 {timeout:.0f} 秒没有回应，没有继续") from None
        except (WebSocketException, OSError):
            raise BrowserUnavailable("专用浏览器里正在读的标签页被关掉了，没有继续") from None
        finally:
            self._waiting.pop(number, None)
        if "error" in message:
            raise CommandFailed(str(message["error"].get("message")))
        return message.get("result", {})

    async def listen(self) -> None:
        """一直读浏览器发来的消息，直到连接断开。"""
        try:
            async for raw in self._socket:
                message = json.loads(raw)
                reply = self._waiting.get(message.get("id"))
                if reply is not None:
                    if not reply.done():
                        reply.set_result(message)
                elif "method" in message:
                    self._on_event(message["method"], message.get("params", {}))
        except WebSocketException:
            pass
        finally:
            for reply in self._waiting.values():
                if not reply.done():
                    reply.set_exception(OSError("连接断开了"))


class ChromeTab:
    """一个连着的标签页。goto 打开地址，act 在页面里做一个动作，两者都返回页面之后的样子。"""

    def __init__(self, socket, capture: str | None = None) -> None:
        self._capture = capture
        self._session = TabSession(socket, self._on_event)
        self._frame: str | None = None
        self._documents: list[tuple[str, int]] = []  # 页面响应：(frame 编号, 状态码)
        self._wanted: dict[str, tuple[str, int]] = {}  # 要记下的接口响应：请求编号 → (地址, 状态码)
        self._captured: list[Captured] = []
        self._tasks: set[asyncio.Task] = set()

    async def start(self, *, focused: bool = False) -> None:
        self._spawn(self._session.listen())
        await self._session.call("Network.enable")
        if focused:
            await self._session.call("Emulation.setFocusEmulationEnabled", {"enabled": True})

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_event(self, method: str, params: dict) -> None:
        if method == "Network.responseReceived":
            response = params.get("response", {})
            if params.get("type") == "Document":
                self._documents.append((params.get("frameId", ""), response.get("status", 0)))
            if self._capture and self._capture in response.get("url", ""):
                self._wanted[params["requestId"]] = (response["url"], response.get("status", 0))
        elif method == "Network.loadingFinished" and params.get("requestId") in self._wanted:
            self._spawn(self._keep_body(params["requestId"]))

    async def _keep_body(self, request_id: str) -> None:
        address, status = self._wanted.pop(request_id)
        try:
            result = await self._session.call("Network.getResponseBody", {"requestId": request_id})
        except (CommandFailed, BrowserUnavailable):
            return  # 页面已经跳走或关闭，这个响应读不到了
        body = result.get("body", "")
        if result.get("base64Encoded"):
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        self._captured.append(Captured(address, status, body[:MAX_BODY_CHARS]))

    async def _evaluate(self, expression: str):
        try:
            result = await self._session.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        except CommandFailed:
            return None  # 页面正在跳转，稍后再读
        return result.get("result", {}).get("value")

    async def goto(
        self, url: str, *, script: str | None = None, ready: Ready | None = None
    ) -> PageResult:
        self._captured = []
        self._documents = []
        navigated = await self._session.call("Page.navigate", {"url": url}, NAVIGATION_TIMEOUT)
        if navigated.get("errorText"):
            raise BrowserUnavailable(f"页面打不开：{navigated['errorText']}")
        self._frame = navigated.get("frameId")
        return await self._read(PageResult(url=url), script, ready)

    async def act(
        self, action: str, *, script: str | None = None, ready: Ready | None = None
    ) -> PageResult | None:
        self._captured = []
        if not await self._evaluate(f"Boolean(({action})())"):
            return None
        return await self._read(None, script, ready)

    async def _read(
        self, result: PageResult | None, script: str | None, ready: Ready | None
    ) -> PageResult | None:
        """反复读页面，直到 ready 说可以了，或者等够了时间。"""
        expression = SNAPSHOT % (script or "null")
        waited = 0.0
        while True:
            value = await self._evaluate(expression)
            if isinstance(value, dict) and value.get("state") != "loading":
                # 页面本身的状态码：只看这个标签页自己的 frame，不看页面里嵌的其他 frame。
                statuses = [status for frame, status in self._documents if frame == self._frame]
                result = PageResult(
                    url=value["url"],
                    status=statuses[-1] if statuses else None,
                    title=value["title"],
                    text=value["text"],
                    captured=list(self._captured),
                    data=value["data"],
                )
                if ready is None or ready(result):
                    break
            if waited >= SETTLE_SECONDS:
                break
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        return result


class ChromeBrowser:
    def __init__(self, profile_dir: Path, port: int, chrome_path: Path | None = None) -> None:
        self.profile_dir = profile_dir
        self.port = port
        self.chrome_path = chrome_path
        self._process: subprocess.Popen | None = None
        self._starting = asyncio.Lock()

    @property
    def _endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _http(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._endpoint, trust_env=False, timeout=timeout)

    async def _reachable(self) -> bool:
        try:
            async with self._http(1.5) as client:
                return (await client.get("/json/version")).status_code == 200
        except httpx.HTTPError:
            return False

    async def _ensure_running(self) -> None:
        """专用浏览器没在运行就启动它。这一步不建立调试连接。"""
        async with self._starting:
            if await self._reachable():
                return
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._process = subprocess.Popen(  # noqa: ASYNC220  只是启动，不等它结束
                [
                    find_chrome(self.chrome_path),
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={self.profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--no-startup-window",  # 启动时不弹窗口，不抢到最前面
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("已启动专用浏览器，调试端口 %s", self.port)
            for _ in range(40):
                if await self._reachable():
                    return
                await asyncio.sleep(0.5)
            raise BrowserUnavailable("专用浏览器启动后一直连不上，请关掉它再试一次")

    async def _command(self, method: str, params: dict) -> dict:
        """给浏览器本身（不是某个页面）发一条命令，发完就断开。页面感觉不到这条连接。"""
        try:
            async with self._http(5) as client:
                address = (await client.get("/json/version")).json()["webSocketDebuggerUrl"]
            async with connect(address, max_size=None, proxy=None) as socket:
                session = TabSession(socket, lambda method, params: None)
                listening = asyncio.create_task(session.listen())
                try:
                    return await session.call(method, params)
                finally:
                    listening.cancel()
        except (httpx.HTTPError, WebSocketException, OSError, KeyError, ValueError) as error:
            raise BrowserUnavailable(f"连不上专用浏览器：{error}") from None
        except CommandFailed as error:
            raise BrowserUnavailable(f"专用浏览器没能打开标签页：{error}") from None

    async def _has_window(self) -> bool:
        try:
            async with self._http(5) as client:
                targets = (await client.get("/json/list")).json()
        except (httpx.HTTPError, ValueError) as error:
            raise BrowserUnavailable(f"连不上专用浏览器：{error}") from None
        return any(target.get("type") == "page" for target in targets)

    async def _background_tab(self) -> str:
        """在后台新开一个空白标签页，返回它的编号。不切换标签页，不把窗口带到前面。

        浏览器一个窗口都没有时（刚由迁跃启动，或者你把窗口都关了），先建一个最小化的窗口
        留着，标签页开在它里面。
        """
        if not await self._has_window():
            await self._command(
                "Target.createTarget",
                {
                    "url": "about:blank",
                    "background": True,
                    "newWindow": True,
                    "windowState": "minimized",
                },
            )
        created = await self._command(
            "Target.createTarget", {"url": "about:blank", "background": True}
        )
        return created["targetId"]

    async def _new_tab(self, url: str) -> dict:
        """用浏览器自带的接口新开一个标签页并切过去，会把窗口带到前面。不建立调试连接。"""
        try:
            async with self._http(10) as client:
                response = await client.put(f"/json/new?{quote(url, safe=':/?=&%')}")
                response.raise_for_status()
                tab = response.json()
                if not isinstance(tab, dict) or "id" not in tab:
                    raise ValueError("回应里没有标签页编号")
                return tab
        except (httpx.HTTPError, ValueError) as error:
            raise BrowserUnavailable(f"专用浏览器没能打开页面：{error}") from None

    async def _close_tab(self, tab_id: str) -> None:
        try:
            async with self._http(5) as client:
                await client.get(f"/json/close/{tab_id}")
        except httpx.HTTPError:
            pass  # 标签页已经不在了

    @asynccontextmanager
    async def tab(
        self, capture: str | None = None, *, focused: bool = False
    ) -> AsyncIterator[ChromeTab]:
        await self._ensure_running()
        tab_id = await self._background_tab()
        try:
            try:
                # proxy=None：这是连本机的浏览器，不走系统代理。
                address = f"ws://127.0.0.1:{self.port}/devtools/page/{tab_id}"
                socket = await connect(address, max_size=None, proxy=None)
            except (WebSocketException, OSError) as error:
                raise BrowserUnavailable(f"连不上专用浏览器的标签页：{error}") from None
            tab = ChromeTab(socket, capture)
            try:
                await tab.start(focused=focused)
                yield tab
            finally:
                tab.stop()
                await socket.close()
        finally:
            await self._close_tab(tab_id)

    async def open(
        self,
        url: str,
        *,
        capture: str | None = None,
        script: str | None = None,
        ready: Ready | None = None,
    ) -> PageResult:
        async with self.tab(capture) as tab:
            return await tab.goto(url, script=script, ready=ready)

    async def show(self, url: str) -> None:
        """打开一个标签页给用户自己用，并把浏览器带到前面。不建立调试连接。"""
        await self._ensure_running()
        tab = await self._new_tab(url)
        try:
            async with self._http(5) as client:
                await client.get(f"/json/activate/{tab['id']}")
        except httpx.HTTPError:
            pass  # 页面已经打开了，只是没能切到前面

    async def close(self) -> None:
        """后端停止时调用。浏览器是迁跃启动的就让它正常退出，登录状态留在用户目录里。"""
        if self._process and self._process.poll() is None:
            self._process.terminate()
        self._process = None
