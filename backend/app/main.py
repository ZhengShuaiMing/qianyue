import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.embeddings import Embeddings
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import (
    artifacts,
    chat,
    collection,
    communications,
    documents,
    health,
    interviews,
    jobs,
    knowledge,
    overview,
    runs,
    screenshots,
    search,
)
from app.api import settings as settings_api
from app.api.deps import resolve_mcp_model
from app.collectors.base import Browser
from app.collectors.browser import ChromeBrowser
from app.collectors.pacing import Pacer
from app.collectors.registry import load_platforms
from app.core import uploads
from app.core.config import Settings
from app.core.logging import setup_logging
from app.core.middleware import RequestContextMiddleware
from app.db import app_settings
from app.db.engine import create_db_engine
from app.graphs.runner import RunManager
from app.mcp_server.server import build_mcp_server
from app.rag.embeddings import LocalEmbeddings
from app.rag.knowledge import KnowledgeBase
from app.rag.rerank import LocalReranker, Reranker
from app.tools.operations import Workspace
from app.tools.web import LiveWebClient, WebClient, search_function

logger = logging.getLogger(__name__)
NOT_BUILT = (
    "前端还没有构建。在 frontend 目录运行 npm run build，"
    "或者用 ./scripts/start/start.sh 启动"
)


def prepare_models(models: list[LocalEmbeddings | LocalReranker]) -> None:
    """下载并加载检索模型（第一次约 1.1 GB）。在后台线程里跑，失败了第一次检索时会再试。"""
    for model in models:
        try:
            model.load()
            logger.info("检索模型已就绪：%s", model.model_name)
        except Exception:
            logger.warning(
                "检索模型没有准备好，第一次检索时会再试：%s", model.model_name, exc_info=True
            )


def create_app(
    settings: Settings | None = None,
    *,
    embeddings: Embeddings | None = None,
    reranker: Reranker | None = None,
    web: WebClient | None = None,
    browser: Browser | None = None,
    pacer: Pacer | None = None,
) -> FastAPI:
    """创建应用，可注入配置、检索模型与联网服务。"""
    settings = settings or Settings()
    load_platforms()
    models_dir = settings.data_dir / "models"
    # 这里只是记下用哪个模型。模型文件在启动后由后台线程下载和加载，不拖慢启动。
    embeddings = embeddings or LocalEmbeddings(settings.embedding_model, models_dir)
    reranker = reranker or LocalReranker(settings.rerank_model, models_dir)
    search_key = settings.search_api_key.get_secret_value() if settings.search_api_key else None
    web = web or LiveWebClient(search_fn=search_function(settings.search_provider, search_key))
    # 采集用的专用浏览器。这里只是记下配置，第一次采集时才启动 Chrome。
    browser = browser or ChromeBrowser(
        settings.data_dir / "browser", settings.browser_port, settings.chrome_path
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 有副作用的初始化都放在启动阶段，import 本模块不会碰磁盘。
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        uploads.clean(settings.data_dir)
        setup_logging(settings.data_dir / "logs")
        # 在后台预热本地检索模型，减少首次检索的等待。
        local_models = [
            m for m in (embeddings, reranker) if isinstance(m, LocalEmbeddings | LocalReranker)
        ]
        if local_models:
            threading.Thread(target=prepare_models, args=(local_models,), daemon=True).start()
        app.state.engine = create_db_engine(settings.data_dir / "qianyue.db")
        # 设置页保存过的模型配置覆盖 .env。base_settings 留着 .env 的原值，保存设置时在它上面叠加
        with Session(app.state.engine) as session:
            use_settings(app_settings.apply(settings, app_settings.load(session)))
        current = app.state.settings
        app.state.knowledge = KnowledgeBase(
            app.state.engine, embeddings, reranker, settings.data_dir / "vectors"
        )
        logger.info(
            "迁跃后端启动：model=%s base_url=%s llm_configured=%s search=%s data_dir=%s",
            current.llm_model,
            current.llm_base_url,
            current.llm_configured,
            current.search_provider,
            settings.data_dir,
        )
        # LangGraph 的断点数据单独存一个库，由框架管理。
        checkpoints = str(settings.data_dir / "checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(checkpoints) as checkpointer:
            app.state.runs = RunManager(
                app.state.engine, checkpointer, settings, app.state.knowledge, web, browser, pacer
            )
            # MCP 的会话管理器要在服务运行期间一直开着。
            async with mcp.session_manager.run():
                yield
            await app.state.runs.shutdown()
            await browser.close()
        app.state.knowledge.close()
        app.state.engine.dispose()

    def use_settings(current: Settings) -> None:
        """换上新的运行配置：模型、联网搜索都立即生效，不用重启。"""
        app.state.settings = current
        if isinstance(web, LiveWebClient):
            key = current.search_api_key.get_secret_value() if current.search_api_key else None
            web.use_search(search_function(current.search_provider, key))

    app = FastAPI(title="迁跃 Qianyue", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.base_settings = settings
    app.state.use_settings = use_settings
    app.state.chats_in_progress = set()  # 正在回复中的对话编号，同一段对话同时只处理一轮
    app.add_middleware(RequestContextMiddleware)
    # 后端只在本机监听。只认 127.0.0.1 和 localhost 这两个主机名：恶意网页把自己的域名临时解析到
    # 127.0.0.1（DNS 重绑定）时，请求头里的主机名是它的域名，会被拦下，读不到设置里的 Key
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(communications.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(interviews.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(collection.router, prefix="/api")
    app.include_router(settings_api.router, prefix="/api")
    app.include_router(overview.router, prefix="/api")
    app.include_router(screenshots.router, prefix="/api")
    app.include_router(search.router, prefix="/api")

    # MCP Server 和后端在同一个进程里，地址是 /mcp。它的工具直接使用后端的数据库和运行执行器。
    mcp = build_mcp_server(
        workspace=lambda: Workspace(app.state.engine, app.state.runs),
        model=lambda: resolve_mcp_model(app),
    )
    app.router.routes.extend(mcp.streamable_http_app(streamable_http_path="/mcp").routes)

    # 构建好的前端：找不到路由的 GET 请求（不在 /api、/mcp 下）由这里接住，文件存在就直接给，
    # 其余都给 index.html，由前端路由决定显示哪一页。用「找不到时兜底」而不是一条通配路由，
    # 这样它永远排在所有接口后面。
    dist = settings.frontend_dist.resolve()

    @app.exception_handler(StarletteHTTPException)
    async def frontend_or_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if (
            exc.status_code != 404
            or request.method != "GET"
            or path.startswith(("/api/", "/mcp"))
        ):
            return await http_exception_handler(request, exc)
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse(
                {"detail": NOT_BUILT},
                status_code=404,
            )
        file = (dist / path.lstrip("/")).resolve()
        if file.is_file() and file.is_relative_to(dist):
            return FileResponse(file)
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html or path == "/":
            return FileResponse(index)
        return await http_exception_handler(request, exc)

    return app


app = create_app()
