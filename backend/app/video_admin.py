from typing import Literal

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field

from .auth import ADMIN
from .errors import AppError, Envelope, ok
from .knowledge import lock
from .models import Tenant, VideoConfig
from .schemas import Input

router = APIRouter(prefix="/api/v1/admin/video", tags=["video models"])


class VideoSettings(Input):
    model_config = ConfigDict(extra="forbid")
    model: Literal["wan2.7-t2v", "wan2.7-t2v-2026-06-12"] = "wan2.7-t2v"
    endpoint: Literal["https://dashscope.aliyuncs.com", "https://dashscope-intl.aliyuncs.com"] = (
        "https://dashscope.aliyuncs.com"
    )
    resolution: Literal["720P", "1080P"] = "720P"
    ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = "16:9"
    duration: int = Field(default=5, strict=True, ge=2, le=15)
    creditsPerSecond: int = Field(strict=True, ge=0, le=1000000)
    active: bool = False


class VideoUpdate(VideoSettings):
    expectedRevision: int = Field(default=0, strict=True, ge=0)


def configured(settings):
    return bool(settings.wanxiang_api_key and settings.wanxiang_api_key.get_secret_value().strip())


def snapshot(repo):
    row = repo.one(VideoConfig)
    return {**row.config, "revision": row.revision} if row else None


@router.get("", response_model=Envelope[dict])
def get_config(request: Request, ctx: ADMIN):
    return ok(request, {"config": snapshot(ctx.repo), "connectionReady": configured(ctx.settings)})


@router.put("", response_model=Envelope[dict])
def save_config(data: VideoUpdate, request: Request, ctx: ADMIN):
    lock(ctx.repo, Tenant, ctx.repo.tenant_id)
    row = ctx.repo.one(VideoConfig, lock=True)
    if (row.revision if row else 0) != data.expectedRevision:
        raise AppError(409, "MODEL_REVISION_CONFLICT", "配置已更新，请刷新后重试。")
    if data.active and not configured(ctx.settings):
        raise AppError(422, "VIDEO_CREDENTIAL_MISSING", "请先配置对应地域的百炼 API Key。")
    values = data.model_dump(exclude={"expectedRevision"})
    if row:
        row.config, row.revision = values, row.revision + 1
    else:
        row = ctx.repo.add(VideoConfig, config=values)
    ctx.repo.audit(
        ctx.user.id,
        "video.configured",
        row.id,
        request.state.request_id,
        {"revision": row.revision, "model": data.model, "active": data.active},
    )
    return ok(request, {"config": snapshot(ctx.repo), "connectionReady": configured(ctx.settings)})
