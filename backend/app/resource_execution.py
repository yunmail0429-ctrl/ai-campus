"""Leased media execution. Adapters perform network work outside database locks."""

import asyncio
import hashlib
import io
import zipfile
from datetime import UTC, timedelta

from sqlalchemy import or_

from .billing import finish, lock_account
from .errors import AppError
from .ingestion import scan
from .knowledge import lock
from .models import OutboxEvent, ResourceOutput, ResourceTask, new_id, now
from .repository import Repository
from .resource_contracts import ResourceInput, ResourceState, adapter_for
from .resource_tasks import event
from .storage import ObjectStore

TERMINAL = ("succeeded", "failed", "cancelled")


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp


def locked(repo, identity):
    task = repo.require(ResourceTask, ResourceTask.id == identity)
    lock_account(repo, task.account_id)
    return lock(repo, ResourceTask, identity)


def enqueue(repo, task, delay=5):
    repo.add(
        OutboxEvent,
        kind="resource.step",
        target_id=task.id,
        available_at=now() + timedelta(seconds=delay),
    )


def terminate(repo, task, status, code=None):
    if task.reservation_id:
        outline_credits = task.config_snapshot.get("outlineCredits", 0) if task.kind == "ppt" else 0
        finish(repo, task.account_id, task.reservation_id, outline_credits if outline_credits else None)
        task.credits = outline_credits
    task.status, task.error_code = status, code
    task.lease_token, task.lease_until = None, None
    event(repo, task)
    repo.add(OutboxEvent, kind="resource.cleanup", target_id=task.id)
    repo.audit(task.user_id, "resource." + status, task.id, task.id, {"errorCode": code})


def extension(kind, artifact):
    """Check allowed container signatures; malware scanning is a separate required step."""
    data, mime = artifact.content, artifact.media_type
    valid = False
    suffix = ""
    if kind == "ppt" and mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        suffix = "pptx"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                names = {item.filename for item in entries}
                valid = (
                    len(entries) <= 2000
                    and sum(item.file_size for item in entries) <= 100 * 1024 * 1024
                    and {"[Content_Types].xml", "ppt/presentation.xml"} <= names
                    and not any(item.flag_bits & 1 for item in entries)
                    and not any("vbaproject" in name.lower() for name in names)
                )
                if valid:
                    valid = archive.testzip() is None
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
            valid = False
    elif kind in ("image", "video") and mime == "text/markdown":
        suffix = "md"
        try:
            content = data.decode("utf-8")
            valid = bool(content.strip()) and "\x00" not in content
        except UnicodeDecodeError:
            valid = False
    elif kind == "image":
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "")
        valid = (
            (suffix == "png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix == "jpg" and data.startswith(b"\xff\xd8\xff"))
            or (suffix == "webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        )
    elif kind in ("video", "avatar"):
        suffix, valid = "mp4", mime == "video/mp4" and data[4:8] == b"ftyp"
    elif kind == "music":
        suffix = {"audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime, "")
        valid = (
            suffix == "mp3"
            and (data.startswith(b"ID3") or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224))
        ) or (suffix == "wav" and data[:4] == b"RIFF" and data[8:12] == b"WAVE")
    elif kind == "3d":
        suffix, valid = "glb", mime == "model/gltf-binary" and data[:4] == b"glTF"
    if not valid:
        raise AppError(502, "INVALID_RESOURCE_OUTPUT", "生成服务返回的文件格式不符合要求。")
    return suffix


def process_resource(sessions, settings, tenant_id, identity):
    token = new_id()
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        task = locked(repo, identity)
        if task.status in TERMINAL or (task.lease_until and utc(task.lease_until) > now()):
            return
        recovering = task.status == "running" and (
            task.config_snapshot.get("adapter") != "editable-ppt"
            or task.config_snapshot.get("generationStarted", False)
        )
        task.status, task.lease_token = "running", token
        task.lease_until = now() + timedelta(minutes=5 if task.kind == "ppt" else 3)
        event(repo, task)
        data = ResourceInput(kind=task.kind, prompt=task.prompt, options=task.options)
        snapshot, remote = task.config_snapshot, task.provider_job_id
        if snapshot.get("adapter") == "editable-ppt" and snapshot.get("approvedOutline") and not recovering:
            snapshot = {**snapshot, "generationStarted": True}
            task.config_snapshot = snapshot
        key = f"resource:{task.id}:{task.attempt}"
        deadline = utc(task.deadline_at)
        cancelling = task.cancel_requested or deadline <= now()

    try:
        adapter = adapter_for(settings, data.kind)

        async def perform():
            if hasattr(adapter, "execute") and not cancelling:
                if data.kind == "ppt" and hasattr(adapter, "generate_outline"):
                    if snapshot.get("outline") and not snapshot.get("approvedOutline"):
                        return "awaiting_outline", None
                    if not snapshot.get("outline"):
                        async with asyncio.timeout(125):
                            return "outline", await adapter.generate_outline(snapshot)
                if recovering:
                    raise AppError(502, "PPT_GENERATION_FAILED", "生成任务已中断，请重新提交。")
                async with asyncio.timeout(240 if data.kind == "ppt" else 125):
                    return "poll", await adapter.execute(data, snapshot)
            async with asyncio.timeout(45):
                if cancelling:
                    return "cancel", await adapter.cancel(remote, snapshot, key)
                if not remote:
                    if recovering and not getattr(adapter, "supports_start_recovery", False):
                        return "uncertain", None
                    job_id = await adapter.start(data, snapshot, key)
                    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 300:
                        raise ValueError("Invalid external task identifier")
                    return "start", job_id
                return "poll", ResourceState.model_validate(await adapter.poll(remote, snapshot))

        action, result = asyncio.run(perform())
        if action == "poll" and result.status == "succeeded":
            save_result(sessions, settings, tenant_id, identity, token, result)
            return
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            task = locked(repo, identity)
            if task.lease_token != token or task.status in TERMINAL:
                return
            task.lease_token, task.lease_until = None, None
            if action == "outline":
                outline, credits = result
                task.config_snapshot = {**task.config_snapshot, "outline": outline, "outlineCredits": credits}
                task.progress = max(task.progress, 25)
                event(repo, task)
                enqueue(repo, task, 300)
                return
            if action == "awaiting_outline":
                enqueue(repo, task, 300)
                return
            if action == "start":
                task.provider_job_id = result
            elif action == "uncertain":
                task.cancel_requested = True
                task.error_code = "RESOURCE_START_UNCERTAIN"
            elif action == "cancel":
                if result is True:
                    terminate(
                        repo,
                        task,
                        "failed" if deadline <= now() else "cancelled",
                        "RESOURCE_TIMEOUT" if deadline <= now() else None,
                    )
                    return
                if now() >= deadline + timedelta(minutes=10):
                    terminate(repo, task, "failed", "RESOURCE_CANCEL_UNCONFIRMED")
                    return
            elif result.status in ("failed", "cancelled"):
                terminate(
                    repo,
                    task,
                    result.status,
                    "RESOURCE_PROVIDER_FAILED" if result.status == "failed" else None,
                )
                return
            else:
                task.progress = max(task.progress, min(result.progress, 99))
            event(repo, task)
            enqueue(repo, task)
    except Exception as exc:  # noqa: BLE001 -- adapter failures must preserve durable recovery state
        # A failed start request may already have been accepted remotely. Never
        # blindly retry a non-idempotent start or expose provider error payloads.
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            task = locked(repo, identity)
            if task.lease_token != token or task.status in TERMINAL:
                return
            task.lease_token, task.lease_until = None, None
            if isinstance(exc, AppError) and exc.code == "FILE_INFECTED":
                terminate(repo, task, "failed", "FILE_INFECTED")
            elif isinstance(exc, AppError) and exc.code == "RESOURCE_REQUEST_REJECTED":
                terminate(repo, task, "failed", "RESOURCE_REQUEST_REJECTED")
            elif isinstance(exc, AppError) and exc.code in ("PPT_GENERATION_FAILED", "TEXT_CREATION_FAILED"):
                terminate(repo, task, "failed", exc.code)
            elif now() >= deadline + timedelta(minutes=10):
                terminate(repo, task, "failed", "RESOURCE_CANCEL_UNCONFIRMED")
            else:
                enqueue(repo, task, 30)


def save_result(sessions, settings, tenant_id, identity, token, result):
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        task = locked(repo, identity)
        if task.lease_token != token or task.status in TERMINAL:
            return
        if task.cancel_requested or utc(task.deadline_at) <= now():
            # Completed remotely: no further remote cancellation is required.
            terminate(
                repo,
                task,
                "cancelled" if task.cancel_requested else "failed",
                None if task.cancel_requested else "RESOURCE_TIMEOUT",
            )
            return
        if result.artifact is None or result.credits > task.max_credits:
            terminate(repo, task, "failed", "INVALID_RESOURCE_RESULT")
            return
        try:
            suffix = extension(task.kind, result.artifact)
        except AppError:
            terminate(repo, task, "failed", "INVALID_RESOURCE_OUTPUT")
            return
        digest = hashlib.sha256(result.artifact.content).hexdigest()
        output = repo.one(
            ResourceOutput, ResourceOutput.task_id == task.id, ResourceOutput.attempt == task.attempt
        )
        if output and output.sha256 != digest:
            terminate(repo, task, "failed", "RESOURCE_OUTPUT_CHANGED")
            return
        if not output:
            output = repo.add(
                ResourceOutput,
                task_id=task.id,
                attempt=task.attempt,
                filename=f"{task.kind}-{task.id}.{suffix}",
                media_type=result.artifact.media_type,
                object_key=f"{tenant_id}/resources/{task.id}/{task.attempt}/{digest}.{suffix}",
                size=len(result.artifact.content),
                sha256=digest,
            )
        object_key = output.object_key
    scan(settings, result.artifact.content)
    ObjectStore(settings).put(object_key, result.artifact.content)
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        task = locked(repo, identity)
        if task.lease_token != token or task.status in TERMINAL:
            # A recovery worker may have succeeded with the identical output;
            # cleanup checks committed readiness before deleting anything.
            pending = repo.one(
                ResourceOutput, ResourceOutput.task_id == identity, ResourceOutput.attempt == task.attempt
            )
            if pending and not pending.ready:
                pending.deleted_at = None
            repo.add(OutboxEvent, kind="resource.cleanup", target_id=identity)
            return
        if task.cancel_requested or utc(task.deadline_at) <= now():
            terminate(
                repo,
                task,
                "cancelled" if task.cancel_requested else "failed",
                None if task.cancel_requested else "RESOURCE_TIMEOUT",
            )
            return
        output = repo.require(
            ResourceOutput, ResourceOutput.task_id == identity, ResourceOutput.attempt == task.attempt
        )
        if task.reservation_id:
            finish(repo, task.account_id, task.reservation_id, result.credits)
        output.ready = True
        task.status, task.progress = "succeeded", 100
        task.credits = result.credits if task.payer == "teacher" else 0
        task.lease_token, task.lease_until, task.error_code = None, None, None
        event(repo, task)
        repo.audit(task.user_id, "resource.succeeded", task.id, task.id, {"credits": task.credits})


def cleanup_resource(sessions, settings, tenant_id, identity):
    with sessions() as session:
        repo = Repository(session, tenant_id)
        task = repo.require(ResourceTask, ResourceTask.id == identity)
        if task.status not in ("failed", "cancelled"):
            return
        outputs = list(
            session.scalars(
                repo.query(ResourceOutput).where(
                    ResourceOutput.task_id == identity,
                    ResourceOutput.ready.is_(False),
                    ResourceOutput.deleted_at.is_(None),
                )
            )
        )
        keys = [(row.id, row.object_key) for row in outputs]
    for output_id, key in keys:
        ObjectStore(settings).delete(key)
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            output = lock(repo, ResourceOutput, output_id)
            output.deleted_at = now()


def recover_resources(sessions, tenant_id):
    stamp = now()
    with sessions() as session:
        repo = Repository(session, tenant_id)
        ids = list(
            session.scalars(
                repo.query(ResourceTask)
                .where(
                    ResourceTask.status.in_(("queued", "running")),
                    or_(
                        ResourceTask.lease_until < stamp,
                        (ResourceTask.lease_until.is_(None))
                        & (ResourceTask.updated_at < stamp - timedelta(minutes=10)),
                    ),
                )
                .with_only_columns(ResourceTask.id)
                .limit(100)
            )
        )
    for identity in ids:
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            task = locked(repo, identity)
            if task.status in TERMINAL or (task.lease_until and utc(task.lease_until) > stamp):
                continue
            task.lease_token, task.lease_until = None, None
            task.updated_at = stamp
            enqueue(repo, task, 0)
    # Recover lost cleanup deliveries as well as generation deliveries.
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        outputs = list(
            session.scalars(
                repo.query(ResourceOutput)
                .where(
                    ResourceOutput.ready.is_(False),
                    ResourceOutput.deleted_at.is_(None),
                    ResourceOutput.updated_at < stamp - timedelta(minutes=10),
                )
                .limit(100)
            )
        )
        for output in outputs:
            task = repo.require(ResourceTask, ResourceTask.id == output.task_id)
            if task.status in ("failed", "cancelled"):
                output.updated_at = stamp
                repo.add(OutboxEvent, kind="resource.cleanup", target_id=task.id)
