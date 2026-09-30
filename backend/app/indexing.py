import asyncio
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_, update

from .embeddings import active_config, index_key, measured_embed
from .errors import AppError
from .knowledge import lock
from .models import (
    ChunkVector,
    Document,
    DocumentChunk,
    EmbeddingUsage,
    IndexJob,
    OutboxEvent,
    iso_utc,
    new_id,
    now,
)
from .repository import Repository


def enqueue(repo, document, key, force=False):
    job = repo.one(IndexJob, IndexJob.document_id == document.id, lock=True)
    if job and job.index_key == key and not force:
        return job
    if job:
        job.index_key, job.status, job.attempts = key, "queued", 0
        job.lease_token, job.lease_until, job.error_code = None, None, None
    else:
        job = repo.add(IndexJob, document_id=document.id, index_key=key)
    document.status = "parsed"
    repo.add(OutboxEvent, kind="document.index", target_id=job.id)
    return job


def schedule(sessions, settings, tenant):
    with sessions.begin() as session:
        repo = Repository(session, tenant)
        session.execute(
            update(EmbeddingUsage)
            .where(
                EmbeddingUsage.tenant_id == tenant,
                EmbeddingUsage.status == "running",
                EmbeddingUsage.deadline_at < now(),
            )
            .values(status="unknown")
        )
        spec = active_config(repo)
        if not spec:
            return
        try:
            key = index_key(settings, spec)
        except AppError:
            return
        ids = list(
            session.scalars(
                repo.query(Document)
                .outerjoin(IndexJob, and_(IndexJob.tenant_id == tenant, IndexJob.document_id == Document.id))
                .where(
                    Document.deleted_at.is_(None),
                    Document.scanned_at.is_not(None),
                    Document.chunk_count > 0,
                    Document.status.in_(["parsed", "ready"]),
                    or_(
                        IndexJob.id.is_(None),
                        IndexJob.index_key != key,
                        and_(IndexJob.status == "running", IndexJob.lease_until < now()),
                        and_(
                            IndexJob.status == "queued", IndexJob.updated_at < now() - timedelta(minutes=10)
                        ),
                    ),
                )
                .with_only_columns(Document.id)
                .order_by(Document.id)
                .limit(100)
            )
        )
    for identity in ids:
        with sessions.begin() as session:
            repo = Repository(session, tenant)
            document = lock(repo, Document, identity)
            if document.deleted_at or document.status not in ("parsed", "ready"):
                continue
            job = repo.one(IndexJob, IndexJob.document_id == identity, lock=True)
            if job and job.index_key == key:
                expired = (
                    job.status == "running"
                    and job.lease_until
                    and datetime.fromisoformat(iso_utc(job.lease_until)) < now()
                )
                lost = job.status == "queued" and datetime.fromisoformat(
                    iso_utc(job.updated_at)
                ) < now() - timedelta(minutes=10)
                if not expired and not lost:
                    continue
                job.lease_token, job.lease_until = None, None
                if job.attempts >= 3:
                    job.status, job.error_code = "failed", "INDEX_INTERRUPTED"
                    continue
                job.status, job.updated_at = "queued", now()
                repo.add(OutboxEvent, kind="document.index", target_id=job.id)
            else:
                enqueue(repo, document, key)


def process_index(sessions, settings, tenant, identity):
    token = new_id()
    with sessions.begin() as session:
        repo = Repository(session, tenant)
        job = repo.one(IndexJob, IndexJob.id == identity)
        if not job:
            return
        document = lock(repo, Document, job.document_id)
        job = repo.require(IndexJob, IndexJob.id == identity, lock=True)
        spec = active_config(repo)
        if not spec or document.deleted_at or job.status != "queued":
            return
        try:
            key = index_key(settings, spec)
        except AppError as exc:
            job.status, job.error_code = "failed", exc.code
            return
        if job.index_key != key:
            return
        job.status, job.lease_token, job.lease_until = "running", token, now() + timedelta(minutes=3)
        job.attempts += 1
        doc_id = document.id
        # One task handles one bounded batch; success enqueues the next batch transactionally.
        rows = list(
            session.scalars(
                repo.query(DocumentChunk)
                .where(
                    DocumentChunk.document_id == doc_id,
                    ~repo.query(ChunkVector)
                    .where(
                        ChunkVector.document_id == DocumentChunk.document_id,
                        ChunkVector.position == DocumentChunk.position,
                        ChunkVector.index_key == key,
                    )
                    .exists(),
                )
                .order_by(DocumentChunk.position)
                .limit(16)
            )
        )
        batch = [(row.position, row.content) for row in rows]
    error, vectors = None, []
    try:
        if batch:
            vectors = asyncio.run(
                measured_embed(sessions, settings, tenant, spec, [row[1] for row in batch], "index", identity)
            )
    except AppError as exc:
        error = exc.code
    except Exception:  # noqa: BLE001 - task boundary persists a sanitized failure
        error = "INDEX_FAILED"
    with sessions.begin() as session:
        repo = Repository(session, tenant)
        document = lock(repo, Document, doc_id)
        job = repo.require(IndexJob, IndexJob.id == identity, lock=True)
        if document.deleted_at or job.lease_token != token or job.status != "running":
            return
        job.lease_token, job.lease_until = None, None
        if error:
            job.error_code = error
            if (
                error
                in ("MODEL_RATE_LIMITED", "MODEL_UNAVAILABLE", "EMBEDDING_TIMEOUT", "EMBEDDING_UNAVAILABLE")
                and job.attempts < 3
            ):
                job.status = "queued"
                repo.add(
                    OutboxEvent,
                    kind="document.index",
                    target_id=identity,
                    available_at=now() + timedelta(seconds=15 * 2 ** (job.attempts - 1)),
                )
            else:
                job.status = "failed"
            repo.audit(document.owner_id, "document.index_failed", doc_id, identity, {"errorCode": error})
            return
        for (position, _), vector in zip(batch, vectors, strict=True):
            repo.add(ChunkVector, document_id=doc_id, position=position, index_key=key, embedding=vector)
        remaining = repo.one(
            DocumentChunk,
            DocumentChunk.document_id == doc_id,
            ~repo.query(ChunkVector)
            .where(
                ChunkVector.document_id == DocumentChunk.document_id,
                ChunkVector.position == DocumentChunk.position,
                ChunkVector.index_key == key,
            )
            .exists(),
        )
        job.error_code, job.attempts = None, 0
        if remaining:
            job.status = "queued"
            repo.add(OutboxEvent, kind="document.index", target_id=identity)
        else:
            job.status, document.status = "succeeded", "ready"
            session.execute(
                delete(ChunkVector).where(
                    ChunkVector.tenant_id == tenant,
                    ChunkVector.document_id == doc_id,
                    ChunkVector.index_key != key,
                )
            )
            repo.audit(
                document.owner_id, "document.indexed", doc_id, identity, {"chunkCount": document.chunk_count}
            )
