"""Owner-scoped durable task API. Provider execution is implemented separately."""

import hashlib
import json
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import RedirectResponse

from .auth import CTX
from .billing import finish, lock_account, reserve
from .errors import AppError, Envelope, Page, ok
from .knowledge import lock
from .models import OutboxEvent, QuotaAccount, ResourceOutput, ResourceTask, TaskEvent, iso_utc, new_id, now
from .resource_contracts import FACTORIES, KINDS, ResourceInput, adapter_for
from .safety import redact
from .storage import ObjectStore

router = APIRouter(prefix="/api/v1/resource-tasks", tags=["resource tasks"])
OperationKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=100)]


def output(task):
    return {
        "id": task.id,
        "kind": task.kind,
        "prompt": task.prompt,
        "options": task.options,
        "status": task.status,
        "progress": task.progress,
        "cancelRequested": task.cancel_requested,
        "errorCode": task.error_code,
        "error": "任务处理失败，请重试或联系管理员。" if task.error_code else None,
        "payer": task.payer,
        "maxCredits": task.max_credits if task.payer == "teacher" else 0,
        "credits": task.credits,
        "createdAt": iso_utc(task.created_at),
        "updatedAt": iso_utc(task.updated_at),
    }


def owned(ctx, identity, locked=False):
    task = ctx.repo.require(ResourceTask, ResourceTask.id == identity, ResourceTask.user_id == ctx.user.id)
    return lock(ctx.repo, ResourceTask, identity) if locked else task


def event(repo, task):
    repo.add(
        TaskEvent,
        task_id=task.id,
        attempt=task.attempt,
        status=task.status,
        progress=task.progress,
        error_code=task.error_code,
    )


@router.get("/capabilities", response_model=Envelope[list[dict]])
def capabilities(request: Request, ctx: CTX):
    return ok(
        request,
        [
            {
                "kind": kind,
                "available": kind in FACTORIES
                and ctx.settings.resource_payer is not None
                and ctx.settings.storage is not None,
                "reason": "生成服务或媒体计费规则尚未配置。"
                if kind not in FACTORIES or ctx.settings.resource_payer is None
                else ("私有存储尚未配置。" if ctx.settings.storage is None else None),
            }
            for kind in KINDS
        ],
    )


@router.get("", response_model=Envelope[Page[dict]])
def list_tasks(
    request: Request,
    ctx: CTX,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(ResourceTask, (ResourceTask.user_id == ctx.user.id,), limit, cursor)
    return ok(request, {"items": [output(row) for row in rows], "nextCursor": next_cursor})


def create(data, request, ctx, key, parent=None):
    if not key.strip():
        raise AppError(422, "INVALID_OPERATION_KEY", "任务请求标识不能为空。")
    if data.kind != "ppt" and data.options.pages is not None:
        raise AppError(422, "INVALID_RESOURCE_OPTIONS", "只有 PPT 任务可以指定页数。")
    fingerprint = hashlib.sha256(
        json.dumps({**data.model_dump(), "retryOf": parent}, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    account = lock_account(ctx.repo, account.id)
    previous = ctx.repo.one(
        ResourceTask, ResourceTask.user_id == ctx.user.id, ResourceTask.operation_key == key
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一任务标识不能用于不同内容。")
        return previous
    adapter = adapter_for(ctx.settings, data.kind)
    if ctx.settings.resource_payer is None:
        raise AppError(503, "RESOURCE_BILLING_NOT_CONFIGURED", "媒体生成积分规则尚未确认，请联系管理员。")
    ObjectStore(ctx.settings)
    safe = data.model_copy(
        update={
            "prompt": redact(data.prompt),
            "options": data.options.model_copy(update={"style": redact(data.options.style)}),
        }
    )
    plan = adapter.prepare(safe)
    identity = new_id()
    reservation = None
    if ctx.settings.resource_payer == "teacher":
        if account.frozen:
            raise AppError(409, "QUOTA_FROZEN", "积分账户已冻结，请联系管理员。")
        if plan.maxCredits:
            reservation = reserve(
                ctx.repo, account.id, plan.maxCredits, f"resource:{identity}:1", "AI 创作生成"
            )
    task = ctx.repo.add(
        ResourceTask,
        id=identity,
        user_id=ctx.user.id,
        account_id=account.id,
        reservation_id=reservation.id if reservation else None,
        operation_key=key,
        request_hash=fingerprint,
        kind=data.kind,
        prompt=safe.prompt,
        options=safe.options.model_dump(),
        config_snapshot={**plan.snapshot, "retryOf": parent},
        payer=ctx.settings.resource_payer,
        max_credits=plan.maxCredits,
        deadline_at=now() + timedelta(hours=2),
    )
    event(ctx.repo, task)
    ctx.repo.add(OutboxEvent, kind="resource.step", target_id=task.id)
    ctx.repo.audit(ctx.user.id, "resource.created", task.id, request.state.request_id, {"kind": task.kind})
    return task


@router.post("", response_model=Envelope[dict], status_code=201)
def create_task(data: ResourceInput, request: Request, ctx: CTX, operation_key: OperationKey):
    return ok(request, output(create(data, request, ctx, operation_key)))


@router.get("/{task_id}", response_model=Envelope[dict])
def get_task(task_id: str, request: Request, ctx: CTX):
    return ok(request, output(owned(ctx, task_id)))


@router.get("/{task_id}/events", response_model=Envelope[Page[dict]])
def task_events(
    task_id: str,
    request: Request,
    ctx: CTX,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    owned(ctx, task_id)
    rows, next_cursor = ctx.repo.page(TaskEvent, (TaskEvent.task_id == task_id,), limit, cursor)
    return ok(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "status": row.status,
                    "progress": row.progress,
                    "errorCode": row.error_code,
                    "createdAt": iso_utc(row.created_at),
                }
                for row in rows
            ],
            "nextCursor": next_cursor,
        },
    )


@router.post("/{task_id}/cancel", response_model=Envelope[dict])
def cancel(task_id: str, request: Request, ctx: CTX):
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    lock_account(ctx.repo, account.id)
    task = owned(ctx, task_id, locked=True)
    if task.status in ("succeeded", "failed", "cancelled") or task.cancel_requested:
        return ok(request, output(task))
    task.cancel_requested = True
    if task.status == "queued" and task.provider_job_id is None and task.lease_token is None:
        task.status = "cancelled"
        if task.reservation_id:
            finish(ctx.repo, task.account_id, task.reservation_id)
    else:
        ctx.repo.add(OutboxEvent, kind="resource.step", target_id=task.id)
    event(ctx.repo, task)
    ctx.repo.audit(ctx.user.id, "resource.cancel_requested", task.id, request.state.request_id)
    return ok(request, output(task))


@router.post("/{task_id}/retry", response_model=Envelope[dict], status_code=201)
def retry(task_id: str, request: Request, ctx: CTX, operation_key: OperationKey):
    previous = owned(ctx, task_id)
    if previous.status not in ("failed", "cancelled"):
        raise AppError(409, "TASK_NOT_RETRYABLE", "只有失败或已取消的任务可以重试。")
    data = ResourceInput(kind=previous.kind, prompt=previous.prompt, options=previous.options)
    return ok(request, output(create(data, request, ctx, operation_key, previous.id)))


@router.get("/{task_id}/download")
def download(task_id: str, request: Request, ctx: CTX):
    task = owned(ctx, task_id)
    if task.status != "succeeded":
        raise AppError(409, "OUTPUT_NOT_READY", "任务尚未完成，没有可下载结果。")
    result = ctx.repo.require(
        ResourceOutput,
        ResourceOutput.task_id == task.id,
        ResourceOutput.attempt == task.attempt,
        ResourceOutput.ready.is_(True),
        ResourceOutput.deleted_at.is_(None),
    )
    url = ObjectStore(ctx.settings).download(result.object_key, result.filename)
    ctx.repo.audit(ctx.user.id, "resource.downloaded", task.id, request.state.request_id)
    return RedirectResponse(url, status_code=303)
