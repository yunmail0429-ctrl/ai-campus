import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException

from . import (
    accounts,
    auth,
    embedding_admin,
    knowledge,
    model_admin,
    quotas,
    resource_tasks,
    retrieval,
    skills,
    statistics,
    workspace,
)
from .config import Settings
from .database import make_engine, make_sessions
from .errors import AppError, ErrorOutput

logger = logging.getLogger("xflj")


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="西附两江智能教育助手", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)

    def failure(request, status, code, message):
        return JSONResponse(
            {
                "code": code,
                "message": message,
                "data": None,
                "requestId": getattr(request.state, "request_id", str(uuid.uuid4())),
            },
            status_code=status,
        )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        # Never copy an untrusted client string into logs or response headers.
        request.state.request_id = str(uuid.uuid4())
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            if request.headers.get("origin") not in settings.allowed_origins:
                return failure(request, 403, "INVALID_ORIGIN", "请求来源无效，请刷新页面重试。")
            chunks, size = [], 0
            upload = request.headers.get("content-type", "").lower().startswith("multipart/form-data")
            limit = settings.max_upload_bytes + 64 * 1024 if upload else settings.max_request_bytes
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return failure(request, 413, "REQUEST_TOO_LARGE", "请求内容超过大小限制。")
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return failure(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return failure(request, 422, "VALIDATION_ERROR", "请求参数不符合要求，请检查填写内容。")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return failure(
            request,
            exc.status_code,
            "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
            "接口不存在。" if exc.status_code == 404 else "此请求不被支持。",
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        return failure(request, 409, "CONFLICT", "记录冲突，请刷新后重试。")

    @app.exception_handler(OperationalError)
    async def unavailable(request: Request, exc: OperationalError):
        logger.error("database unavailable request_id=%s", getattr(request.state, "request_id", "unknown"))
        return failure(request, 503, "DATABASE_UNAVAILABLE", "数据库暂时不可用，请联系管理员。")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logger.error(
            "unexpected error type=%s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", "unknown"),
        )
        return failure(request, 500, "INTERNAL_ERROR", "服务发生异常，请稍后重试。")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    for router in (
        auth.router,
        accounts.router,
        workspace.router,
        skills.router,
        quotas.router,
        model_admin.router,
        knowledge.router,
        embedding_admin.router,
        retrieval.router,
        statistics.router,
        resource_tasks.router,
    ):
        app.include_router(
            router,
            responses={
                code: {"model": ErrorOutput} for code in (400, 401, 403, 404, 409, 413, 422, 429, 503)
            },
        )
    return app


app = create_app()
