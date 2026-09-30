import time

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from .adapters import ChatAdapter, ChatSpec, connection_for
from .auth import ADMIN
from .direct_providers import DIRECT_PROVIDERS
from .errors import AppError, Envelope, Page, ok
from .model_connections import encrypt_key, model_connection, validate_url
from .models import ModelConfig, ModelPrice, ModelRoute, Tenant
from .repository import Repository
from .schemas import (
    ModelCheckOutput,
    ModelInput,
    ModelOutput,
    ModelStateInput,
    ModelUpdateInput,
    ProviderOutput,
)

router = APIRouter(prefix="/api/v1/admin", tags=["models"])


def serialize(row, settings, default_id=None):
    try:
        model_connection(settings, row)
        ready = True
    except AppError:
        ready = False
    return {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "model": row.model,
        "apiBaseUrl": row.api_base_url,
        "keyConfigured": bool(row.encrypted_api_key),
        "active": row.active,
        "contextTokens": row.context_tokens,
        "maxOutputTokens": row.max_output_tokens,
        "timeoutSeconds": row.timeout_seconds,
        "inputPrice": row.input_price,
        "outputPrice": row.output_price,
        "revision": row.revision,
        "default": row.id == default_id,
        "connectionReady": ready,
    }


def values(data):
    return {
        "name": data.name,
        "provider": data.provider,
        "model": data.model,
        "active": data.active,
        "context_tokens": data.contextTokens,
        "max_output_tokens": data.maxOutputTokens,
        "timeout_seconds": data.timeoutSeconds,
        "input_price": data.inputPrice,
        "output_price": data.outputPrice,
    }


def validate_provider(settings, data, existing=None):
    if data.apiBaseUrl:
        validate_url(data.apiBaseUrl, settings)
        if data.provider not in DIRECT_PROVIDERS:
            raise AppError(422, "INVALID_PROVIDER", "此供应商不支持在页面填写 API 地址。")
        if not (data.apiKey and data.apiKey.get_secret_value().strip()) and not (existing and existing.encrypted_api_key):
            raise AppError(422, "MODEL_CREDENTIAL_MISSING", "请填写模型 API Key。")
        return
    profile = settings.model_providers.get(data.provider)
    if not profile or settings.tenant_slug not in profile.tenant_slugs:
        raise AppError(422, "UNKNOWN_PROVIDER", "请从学校已配置的供应商中选择。")
    if data.active:
        connection_for(settings, data.provider)


def default_id(repo):
    route = repo.one(ModelRoute, ModelRoute.purpose == "chat")
    return route.model_id if route else None


@router.get("/model-providers", response_model=Envelope[list[ProviderOutput]])
def providers(request: Request, ctx: ADMIN):
    settings = request.app.state.settings
    result = []
    for key, profile in settings.model_providers.items():
        if settings.tenant_slug not in profile.tenant_slugs:
            continue
        try:
            connection_for(settings, key)
            ready = True
        except AppError:
            ready = False
        result.append({"id": key, "label": profile.label, "ready": ready})
    for key, label in DIRECT_PROVIDERS.items():
        if not any(item["id"] == key for item in result):
            result.append({"id": key, "label": label, "ready": bool(settings.model_credential_key)})
    return ok(request, result)


@router.get("/models", response_model=Envelope[Page[ModelOutput]])
def models(
    request: Request,
    ctx: ADMIN,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, cursor = ctx.repo.page(ModelConfig, limit=limit, cursor=cursor)
    route = default_id(ctx.repo)
    return ok(
        request,
        {
            "items": [serialize(row, request.app.state.settings, route) for row in rows],
            "nextCursor": cursor,
        },
    )


@router.post("/models", response_model=Envelope[ModelOutput], status_code=201)
def create(data: ModelInput, request: Request, ctx: ADMIN):
    validate_provider(request.app.state.settings, data)
    if data.apiBaseUrl:
        # Confirm encryption is configured before inserting the model.
        encrypted_key = encrypt_key(request.app.state.settings, data.apiKey.get_secret_value().strip())
    else:
        encrypted_key = None
    ctx.repo.require(Tenant, Tenant.id == ctx.repo.tenant_id, lock=True)
    row = ctx.repo.add(ModelConfig, **values(data), api_base_url=validate_url(data.apiBaseUrl, request.app.state.settings) if data.apiBaseUrl else None, encrypted_api_key=encrypted_key)
    ctx.repo.add(
        ModelPrice, model_id=row.id, revision=1, input_price=row.input_price, output_price=row.output_price
    )
    ctx.repo.audit(ctx.user.id, "model.created", row.id, request.state.request_id, {"revision": 1})
    return ok(request, serialize(row, request.app.state.settings))


@router.put("/models/{model_id}", response_model=Envelope[ModelOutput])
def update(model_id: str, data: ModelUpdateInput, request: Request, ctx: ADMIN):
    ctx.repo.require(Tenant, Tenant.id == ctx.repo.tenant_id, lock=True)
    row = ctx.repo.require(ModelConfig, ModelConfig.id == model_id, lock=True)
    if row.revision != data.expectedRevision:
        raise AppError(409, "MODEL_REVISION_CONFLICT", "配置已被修改，请刷新后重试。")
    validate_provider(request.app.state.settings, data, row)
    if data.apiBaseUrl:
        row.api_base_url = validate_url(data.apiBaseUrl, request.app.state.settings)
        if data.apiKey and data.apiKey.get_secret_value().strip():
            row.encrypted_api_key = encrypt_key(request.app.state.settings, data.apiKey.get_secret_value().strip())
    else:
        row.api_base_url = None
        row.encrypted_api_key = None
    for key, value in values(data).items():
        setattr(row, key, value)
    row.revision += 1
    ctx.repo.add(
        ModelPrice,
        model_id=row.id,
        revision=row.revision,
        input_price=row.input_price,
        output_price=row.output_price,
    )
    if not row.active:
        route = ctx.repo.one(ModelRoute, ModelRoute.model_id == row.id)
        if route:
            ctx.repo.session.delete(route)
            ctx.repo.session.flush()
    ctx.repo.audit(ctx.user.id, "model.updated", row.id, request.state.request_id, {"revision": row.revision})
    return ok(request, serialize(row, request.app.state.settings, default_id(ctx.repo)))


@router.post("/models/{model_id}/default", response_model=Envelope[ModelOutput])
def select_default(model_id: str, request: Request, ctx: ADMIN):
    ctx.repo.require(Tenant, Tenant.id == ctx.repo.tenant_id, lock=True)
    row = ctx.repo.require(ModelConfig, ModelConfig.id == model_id, lock=True)
    if not row.active:
        raise AppError(409, "MODEL_DISABLED", "请先启用该模型。")
    model_connection(request.app.state.settings, row)
    route = ctx.repo.one(ModelRoute, ModelRoute.purpose == "chat")
    if route:
        route.model_id = row.id
    else:
        ctx.repo.add(ModelRoute, purpose="chat", model_id=row.id)
    ctx.repo.audit(ctx.user.id, "model.default_changed", row.id, request.state.request_id)
    return ok(request, serialize(row, request.app.state.settings, row.id))


@router.patch("/models/{model_id}/state", response_model=Envelope[ModelOutput])
def set_state(model_id: str, data: ModelStateInput, request: Request, ctx: ADMIN):
    ctx.repo.require(Tenant, Tenant.id == ctx.repo.tenant_id, lock=True)
    row = ctx.repo.require(ModelConfig, ModelConfig.id == model_id, lock=True)
    if row.revision != data.expectedRevision:
        raise AppError(409, "MODEL_REVISION_CONFLICT", "配置已被修改，请刷新后重试。")
    if data.active:
        model_connection(request.app.state.settings, row)
    row.active = data.active
    row.revision += 1
    ctx.repo.add(
        ModelPrice,
        model_id=row.id,
        revision=row.revision,
        input_price=row.input_price,
        output_price=row.output_price,
    )
    if not row.active:
        route = ctx.repo.one(ModelRoute, ModelRoute.model_id == row.id)
        if route:
            ctx.repo.session.delete(route)
            ctx.repo.session.flush()
    ctx.repo.audit(
        ctx.user.id,
        "model.enabled" if row.active else "model.disabled",
        row.id,
        request.state.request_id,
        {"revision": row.revision},
    )
    return ok(request, serialize(row, request.app.state.settings, default_id(ctx.repo)))


@router.post("/models/{model_id}/check", response_model=Envelope[ModelCheckOutput])
async def check(model_id: str, request: Request, ctx: ADMIN):
    row = ctx.repo.require(ModelConfig, ModelConfig.id == model_id)
    connection = model_connection(request.app.state.settings, row)
    spec = ChatSpec(row.model, min(row.max_output_tokens, 256), min(row.timeout_seconds, 30))
    tenant_id, actor_id, revision = ctx.repo.tenant_id, ctx.user.id, row.revision
    # Release the read transaction before outbound I/O; the check never holds a quota lock.
    ctx.repo.session.commit()
    started, usage, failure = time.monotonic(), {}, None
    try:
        async for event in ChatAdapter(connection).stream(
            spec, [{"role": "user", "content": "Reply with OK."}]
        ):
            if event["event"] == "usage":
                usage = event
    except AppError as error:
        failure = error
    elapsed = int((time.monotonic() - started) * 1000)

    def save_result():
        with request.app.state.sessions.begin() as session:
            repo = Repository(session, tenant_id)
            repo.audit(
                actor_id,
                "model.connection_checked",
                model_id,
                request.state.request_id,
                {
                    "revision": revision,
                    "success": failure is None,
                    "latencyMs": elapsed,
                    "errorCode": failure.code if failure else None,
                    "inputTokens": usage.get("inputTokens", 0),
                    "outputTokens": usage.get("outputTokens", 0),
                },
            )

    await run_in_threadpool(save_result)
    if failure:
        raise failure
    return ok(
        request,
        {
            "connected": True,
            "inputTokens": usage["inputTokens"],
            "outputTokens": usage["outputTokens"],
            "latencyMs": elapsed,
        },
    )
