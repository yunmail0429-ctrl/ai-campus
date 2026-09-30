import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, or_

from .errors import AppError
from .knowledge import ERRORS, lock
from .models import Document, DocumentChunk, DocumentPage, IndexJob, IngestionJob, OutboxEvent, new_id, now
from .repository import Repository
from .storage import ObjectStore

TRANSIENT = {"SCANNER_UNAVAILABLE", "STORAGE_UNAVAILABLE", "WORKER_INTERRUPTED", "PROCESSING_FAILED", "OCR_UNAVAILABLE"}


def scan(settings, content):
    if not settings.clamav_host:
        raise AppError(503, "SCANNER_NOT_CONFIGURED", ERRORS["SCANNER_NOT_CONFIGURED"])
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=settings.clamav_timeout
        ) as connection:
            connection.settimeout(settings.clamav_timeout)
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(content), 64 * 1024):
                block = content[offset : offset + 64 * 1024]
                connection.sendall(struct.pack("!I", len(block)) + block)
            connection.sendall(b"\0\0\0\0")
            reply = bytearray()
            while len(reply) < 4096 and b"\0" not in reply:
                block = connection.recv(1024)
                if not block:
                    break
                reply.extend(block)
            status = bytes(reply).split(b"\0", 1)[0]
            if status.startswith(b"stream: ") and status.endswith(b" FOUND"):
                raise AppError(422, "FILE_INFECTED", ERRORS["FILE_INFECTED"])
            if status != b"stream: OK" or b"\0" not in reply:
                raise OSError("Invalid scanner response")
    except OSError:
        raise AppError(503, "SCANNER_UNAVAILABLE", ERRORS["SCANNER_UNAVAILABLE"]) from None


class ParsedChunks(list):
    def __init__(self, chunks, pages, ocr_unavailable=False):
        super().__init__(chunks)
        self.pages = pages
        self.ocr_unavailable = ocr_unavailable


def bound_chunks(chunks, max_bytes):
    """Fit searchable text to the configured embedding input envelope."""
    result = []
    for chunk in chunks:
        content = chunk["content"]
        while content:
            encoded = content.encode("utf-8")
            if len(encoded) <= max_bytes:
                result.append({**chunk, "content": content})
                break
            piece = encoded[:max_bytes].decode("utf-8", errors="ignore")
            if not piece:
                raise ValueError("Embedding input envelope cannot fit a character")
            result.append({**chunk, "content": piece})
            content = content[len(piece):]
    return result


def parse(content, filename):
    environment = {
        key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ
    }
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("file_parser.py")), filename],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
            raise ValueError("Invalid parser response")
        data = json.loads(result.stdout)
        if data.get("error"):
            code = data["error"] if data["error"] in ERRORS else "PROCESSING_FAILED"
            raise AppError(422, code, ERRORS[code])
        return ParsedChunks(data["chunks"], data.get("pages", []), data.get("ocr_unavailable", False))
    except subprocess.TimeoutExpired:
        raise AppError(422, "PARSE_LIMIT", ERRORS["PARSE_LIMIT"]) from None
    except (OSError, ValueError, KeyError):
        raise AppError(422, "INVALID_FILE", ERRORS["INVALID_FILE"]) from None


def process_job(sessions, settings, tenant_id, job_id):
    token = new_id()
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        job = repo.one(IngestionJob, IngestionJob.id == job_id)
        if not job:
            return
        document = lock(repo, Document, job.document_id)
        job = repo.require(IngestionJob, IngestionJob.id == job_id, lock=True)
        if document.deleted_at or job.status != "queued":
            return
        job.status, job.lease_token, job.lease_until = "running", token, now() + timedelta(minutes=3)
        job.attempts += 1
        document.status, document.error_code, document.scanned_at = "scanning", None, None
        identity, key, filename, digest = document.id, document.object_key, document.filename, document.sha256
    chunks, error, vision_error = [], None, False
    try:
        content = ObjectStore(settings).read(key, settings.max_upload_bytes)
        if hashlib.sha256(content).hexdigest() != digest:
            raise AppError(422, "CONTENT_CHANGED", ERRORS["CONTENT_CHANGED"])
        scan(settings, content)
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            document = lock(repo, Document, identity)
            job = repo.require(IngestionJob, IngestionJob.id == job_id, lock=True)
            if document.deleted_at or job.lease_token != token or job.status != "running":
                return
            document.scanned_at, document.status = now(), "parsing"
        chunks = parse(content, filename)
        from .embeddings import active_config

        with sessions() as session:
            spec = active_config(Repository(session, tenant_id))
        if spec and spec.get("visionProvider"):
            from .visual_extraction import enrich

            try:
                chunks = enrich(settings, spec, content, filename, chunks)
            except Exception:  # noqa: BLE001 - optional vision must not discard valid OCR/native text
                vision_error = True
        if not chunks:
            code = "OCR_UNAVAILABLE" if getattr(chunks, "ocr_unavailable", False) else "NO_EXTRACTABLE_TEXT"
            raise AppError(422, code, ERRORS[code])
        # The parser's character-sized chunks may exceed the embedding adapter's
        # UTF-8 byte limit for Chinese text. Keep a margin for redaction growth.
        limit = min(1000, max(128, (spec["maxInputTokens"] if spec else 1000) - 128))
        chunks[:] = bound_chunks(chunks, limit)
    except AppError as exc:
        error = exc.code if exc.code in ERRORS else "PROCESSING_FAILED"
    except Exception:  # noqa: BLE001 - persist a terminal/retry state without leaking file content
        # Never persist supplier, object-store, parser or scanner exception text.
        error = "PROCESSING_FAILED"
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        document = lock(repo, Document, identity)
        job = repo.require(IngestionJob, IngestionJob.id == job_id, lock=True)
        if document.deleted_at or job.lease_token != token or job.status != "running":
            return
        job.lease_token, job.lease_until = None, None
        if error:
            document.error_code = error
            if error == "FILE_INFECTED":
                document.status, job.status = "quarantined", "failed"
            elif error in TRANSIENT and job.attempts < 3:
                document.status, job.status = "queued", "queued"
                repo.add(
                    OutboxEvent,
                    kind="document.ingest",
                    target_id=job.id,
                    available_at=now() + timedelta(seconds=15 * 2 ** (job.attempts - 1)),
                )
            else:
                document.status, job.status = "failed", "failed"
            repo.audit(
                document.owner_id,
                "document.failed",
                identity,
                job.id,
                {"errorCode": error, "attempt": job.attempts},
            )
        else:
            session.execute(
                delete(DocumentPage).where(
                    DocumentPage.tenant_id == tenant_id, DocumentPage.document_id == identity
                )
            )
            session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.tenant_id == tenant_id, DocumentChunk.document_id == identity
                )
            )
            for position, chunk in enumerate(chunks):
                repo.add(DocumentChunk, document_id=identity, position=position, **chunk)
            for page_index, page in enumerate(getattr(chunks, "pages", [])):
                repo.add(DocumentPage, document_id=identity, page_index=page_index, **page)
            document.chunk_count = len(chunks)
            document.qdrant_synced_key = None
            document.status, job.status = "parsed", "succeeded"
            repo.audit(document.owner_id, "document.parsed", identity, job.id, {"chunkCount": len(chunks)})
            if getattr(chunks, "vision_calls", 0):
                repo.audit(
                    document.owner_id, "document.vision_attempted", identity, job.id,
                    {"calls": chunks.vision_calls},
                )
            if vision_error:
                repo.audit(document.owner_id, "document.vision_failed", identity, job.id)
            from .embeddings import active_config, index_key
            from .indexing import enqueue

            spec = active_config(repo)
            if spec:
                try:
                    key = index_key(settings, spec)
                except AppError:
                    pass
                else:
                    enqueue(repo, document, key, force=True)
            else:
                previous = repo.one(IndexJob, IndexJob.document_id == identity, lock=True)
                if previous:
                    previous.status, previous.index_key = "cancelled", ""


def recover_jobs(sessions, tenant_id):
    """Recover crashed workers and lost broker deliveries; stale workers are fenced by token."""
    stamp = now()
    with sessions() as session:
        repo = Repository(session, tenant_id)
        ids = list(
            session.scalars(
                repo.query(IngestionJob)
                .where(
                    or_(
                        (IngestionJob.status == "running") & (IngestionJob.lease_until < stamp),
                        (IngestionJob.status == "queued")
                        & (IngestionJob.updated_at < stamp - timedelta(minutes=10)),
                    )
                )
                .with_only_columns(IngestionJob.id)
                .limit(100)
            )
        )
    for identity in ids:
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            job = repo.require(IngestionJob, IngestionJob.id == identity)
            document = lock(repo, Document, job.document_id)
            job = repo.one(
                IngestionJob,
                IngestionJob.id == identity,
                or_(
                    (IngestionJob.status == "running") & (IngestionJob.lease_until < stamp),
                    (IngestionJob.status == "queued")
                    & (IngestionJob.updated_at < stamp - timedelta(minutes=10)),
                ),
                lock=True,
            )
            if not job or document.deleted_at:
                continue
            job.lease_until, job.lease_token = None, None
            job.updated_at = stamp
            if job.attempts >= 3:
                document.status, job.status, document.error_code = "failed", "failed", "WORKER_INTERRUPTED"
            else:
                document.status, job.status = "queued", "queued"
                repo.add(OutboxEvent, kind="document.ingest", target_id=identity)


def delete_object(sessions, settings, tenant_id, document_id):
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        document = lock(repo, Document, document_id)
        if document.deleted_at and not document.object_deleted_at:
            ObjectStore(settings).delete(document.object_key)
            document.object_deleted_at = now()
        dimensions = list(document.qdrant_dimensions or [])
    if settings.qdrant_url:
        from .qdrant_index import QdrantIndex

        for dimension in dimensions:
            QdrantIndex(settings, dimension).delete_document(tenant_id, document_id)


def dispatch(sessions, tenant_id, publish):
    recover_jobs(sessions, tenant_id)
    with sessions.begin() as session:
        repo = Repository(session, tenant_id)
        documents = list(
            session.scalars(
                repo.query(Document)
                .where(
                    Document.deleted_at.is_not(None),
                    Document.object_deleted_at.is_(None),
                    Document.updated_at < now() - timedelta(minutes=10),
                )
                .limit(100)
            )
        )
        for row in documents:
            row = lock(repo, Document, row.id)
            if row.object_deleted_at:
                continue
            repo.add(OutboxEvent, kind="document.delete", target_id=row.id)
            row.updated_at = now()
    with sessions() as session:
        repo = Repository(session, tenant_id)
        ids = list(
            session.scalars(
                repo.query(OutboxEvent)
                .where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.available_at <= now(),
                )
                .with_only_columns(OutboxEvent.id)
                .order_by(OutboxEvent.created_at)
                .limit(100)
            )
        )
    count = 0
    for identity in ids:
        with sessions.begin() as session:
            repo = Repository(session, tenant_id)
            event = lock(repo, OutboxEvent, identity)
            if event.published_at:
                continue
            # Publish first, mark after: a crash may duplicate delivery, never silently lose it.
            publish(event.kind, tenant_id, event.target_id, event.id)
            event.published_at = now()
            count += 1
    return count
