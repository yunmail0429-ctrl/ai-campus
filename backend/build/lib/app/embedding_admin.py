from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func

from .adapters import connection_for
from .auth import ADMIN
from .embeddings import measured_embed, snapshot
from .errors import AppError, Envelope, ok
from .knowledge import lock
from .models import EmbeddingConfig, EmbeddingUsage, Tenant

router = APIRouter(prefix="/api/v1/admin/embedding", tags=["knowledge models"])


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    model: str = Field(min_length=1, max_length=120)
    dimensions: int = Field(strict=True, ge=1, le=4096)
    requestDimensions: bool = False
    maxInputTokens: int = Field(strict=True, default=8192, ge=256, le=32000)
    timeoutSeconds: int = Field(strict=True, default=30, ge=5, le=120)
    minScore: float = Field(default=0.35, ge=0, le=1, allow_inf_nan=False)
    active: bool = False
    expectedRevision: int = Field(default=0, ge=0)


class ConfigOutput(BaseModel):
    config: dict | None
    completedCalls: int
    inputTokens: int
    uncertainCalls: int


def output(repo):
    row = repo.one(EmbeddingConfig)
    complete = (EmbeddingUsage.status == "completed",)
    total = repo.session.scalar(repo.query(EmbeddingUsage).where(*complete).with_only_columns(func.count()))
    tokens = repo.session.scalar(
        repo.query(EmbeddingUsage)
        .where(*complete)
        .with_only_columns(func.coalesce(func.sum(EmbeddingUsage.input_tokens), 0))
    )
    uncertain = repo.session.scalar(
        repo.query(EmbeddingUsage)
        .where(EmbeddingUsage.status.in_(["running", "unknown", "failed"]))
        .with_only_columns(func.count())
    )
    return {
        "config": {**snapshot(row), "active": row.active} if row else None,
        "completedCalls": total,
        "inputTokens": tokens,
        "uncertainCalls": uncertain,
    }


@router.get("", response_model=Envelope[ConfigOutput])
def get_config(request: Request, ctx: ADMIN):
    return ok(request, output(ctx.repo))


@router.put("", response_model=Envelope[ConfigOutput])
def save_config(data: ConfigInput, request: Request, ctx: ADMIN):
    lock(ctx.repo, Tenant, ctx.repo.tenant_id)
    profile = ctx.settings.model_providers.get(data.provider)
    if not profile or ctx.settings.tenant_slug not in profile.tenant_slugs:
        raise AppError(422, "UNKNOWN_PROVIDER", "请选择学校已配置的本地或外部模型服务。")
    if data.active:
        connection_for(ctx.settings, data.provider)
    row = ctx.repo.one(EmbeddingConfig, lock=True)
    if (row.revision if row else 0) != data.expectedRevision:
        raise AppError(409, "MODEL_REVISION_CONFLICT", "配置已被修改，请刷新后重试。")
    values = {
        "provider": data.provider,
        "model": data.model,
        "dimensions": data.dimensions,
        "request_dimensions": data.requestDimensions,
        "max_input_tokens": data.maxInputTokens,
        "timeout_seconds": data.timeoutSeconds,
        "min_score": data.minScore,
        "active": data.active,
    }
    if row:
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
    else:
        row = ctx.repo.add(EmbeddingConfig, **values)
    ctx.repo.session.flush()
    ctx.repo.audit(
        ctx.user.id,
        "embedding.configured",
        row.id,
        request.state.request_id,
        {"revision": row.revision, "active": row.active},
    )
    return ok(request, output(ctx.repo))


@router.post("/check", response_model=Envelope[dict])
async def check(request: Request, ctx: ADMIN):
    row = ctx.repo.require(EmbeddingConfig)
    spec, tenant, actor = snapshot(row), ctx.repo.tenant_id, ctx.user.id
    ctx.repo.session.commit()
    vectors = await measured_embed(
        request.app.state.sessions, ctx.settings, tenant, spec, ["学校教学资料检索连接检查"], "check", actor
    )
    return ok(request, {"connected": True, "dimensions": len(vectors[0])})
