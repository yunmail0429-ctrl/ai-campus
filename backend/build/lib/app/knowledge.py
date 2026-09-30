import hashlib
import re
from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, or_, update

from .auth import CTX
from .errors import AppError, Envelope, Page, ok
from .file_parser import TYPES
from .models import (
    Conversation,
    Document,
    DocumentChunk,
    IndexJob,
    IngestionJob,
    KnowledgeBase,
    OutboxEvent,
    User,
    iso_utc,
    new_id,
    now,
)
from .storage import ObjectStore

router = APIRouter(prefix="/api/v1", tags=["knowledge"])
ERRORS = {
    "FILE_INFECTED": "文件未通过安全扫描，已隔离，请删除后检查原文件。",
    "SCANNER_NOT_CONFIGURED": "安全扫描服务尚未配置，请联系管理员后重试。",
    "SCANNER_UNAVAILABLE": "安全扫描服务暂时不可用，请稍后重试。",
    "STORAGE_UNAVAILABLE": "无法读取原文件，请联系管理员后重试。",
    "CONTENT_CHANGED": "原文件校验不一致，已停止处理，请重新上传。",
    "OCR_REQUIRED": "这份资料需要 OCR 识别，当前尚未配置 OCR 服务。",
    "INVALID_FILE": "文件格式或编码无效；文本请使用 UTF-8，Word/PPT 请使用 docx/pptx。",
    "ENCRYPTED_FILE": "暂不支持加密文件，请解除密码保护后上传。",
    "PARSE_LIMIT": "文档页数或展开后的内容超过处理限制，请拆分后上传。",
    "NO_EXTRACTABLE_TEXT": "没有提取到可用文字，请检查资料或使用 OCR。",
    "WORKER_INTERRUPTED": "文件处理意外中断，请重试。",
    "PROCESSING_FAILED": "文件处理失败，请稍后重试或联系管理员。",
}


class BaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    scope: Literal["private", "school"] = "private"


class BaseOutput(BaseInput):
    id: str
    canManage: bool


class TagsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = Field(max_length=10)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        values = list(dict.fromkeys(value.strip() for value in values))
        if any(not value or len(value) > 30 or any(ord(c) < 32 for c in value) for value in values):
            raise ValueError("Invalid tags")
        return values


class DocumentOutput(BaseModel):
    id: str
    filename: str
    sizeBytes: int
    mediaType: str
    status: str
    tags: list[str]
    chunkCount: int
    errorCode: str | None
    errorMessage: str | None
    updatedAt: str
    canManage: bool


class ChunkOutput(BaseModel):
    id: str
    position: int
    location: str
    content: str


class DownloadOutput(BaseModel):
    url: str
    expiresIn: int


def lock(repo, model, identity):
    if repo.session.bind.dialect.name == "sqlite":
        repo.session.execute(
            update(model)
            .where(model.tenant_id == repo.tenant_id, model.id == identity)
            .values(updated_at=model.updated_at)
        )
    return repo.require(model, model.id == identity, lock=True)


def knowledge_base(ctx, identity, write=False):
    row = ctx.repo.require(KnowledgeBase, KnowledgeBase.id == identity)
    if row.scope == "private" and row.owner_id != ctx.user.id:
        raise AppError(404, "NOT_FOUND", "资料不存在或无权访问。")
    if write and row.scope == "school" and "admin" not in ctx.roles:
        raise AppError(403, "FORBIDDEN", "学校知识库由管理员维护。")
    return row


def document_access(ctx, identity, write=False, locked=False):
    row = (
        lock(ctx.repo, Document, identity) if locked else ctx.repo.require(Document, Document.id == identity)
    )
    if row.deleted_at:
        raise AppError(404, "NOT_FOUND", "资料不存在或已删除。")
    if row.knowledge_base_id:
        knowledge_base(ctx, row.knowledge_base_id, write)
    else:
        ctx.repo.require(
            Conversation,
            Conversation.id == row.conversation_id,
            Conversation.user_id == ctx.user.id,
            Conversation.deleted_at.is_(None),
        )
    return row


def output(ctx, row):
    manageable = row.owner_id == ctx.user.id
    if row.knowledge_base_id:
        base = knowledge_base(ctx, row.knowledge_base_id)
        manageable = base.scope == "private" or "admin" in ctx.roles
    status, error_code, error_message = (
        row.status,
        row.error_code,
        ERRORS.get(row.error_code, "文件处理失败，请重试。") if row.error_code else None,
    )
    if status in ("parsed", "ready"):
        from .embeddings import active_config, index_key

        spec = active_config(ctx.repo)
        try:
            key = index_key(ctx.settings, spec) if spec else None
        except AppError:
            key = None
        job = ctx.repo.one(IndexJob, IndexJob.document_id == row.id)
        if not key or not job or job.index_key != key:
            status = "parsed"
        elif job.status in ("queued", "running"):
            status = "indexing"
        elif job.status == "failed":
            status, error_code, error_message = (
                "index_failed",
                job.error_code,
                "资料索引失败，请检查向量模型配置后重试。",
            )
    return {
        "id": row.id,
        "filename": row.filename,
        "sizeBytes": row.size_bytes,
        "mediaType": row.media_type,
        "status": status,
        "tags": row.tags,
        "chunkCount": row.chunk_count,
        "errorCode": error_code,
        "errorMessage": error_message,
        "updatedAt": iso_utc(row.updated_at),
        "canManage": manageable,
    }


@router.get("/knowledge-bases", response_model=Envelope[Page[BaseOutput]])
def list_bases(
    request: Request,
    ctx: CTX,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(
        KnowledgeBase,
        (or_(KnowledgeBase.owner_id == ctx.user.id, KnowledgeBase.scope == "school"),),
        limit,
        cursor,
    )
    return ok(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "scope": row.scope,
                    "canManage": row.scope == "private" or "admin" in ctx.roles,
                }
                for row in rows
            ],
            "nextCursor": next_cursor,
        },
    )


@router.post("/knowledge-bases", response_model=Envelope[BaseOutput], status_code=201)
def create_base(data: BaseInput, request: Request, ctx: CTX):
    if data.scope == "school" and "admin" not in ctx.roles:
        raise AppError(403, "FORBIDDEN", "学校知识库由管理员创建。")
    row = ctx.repo.add(KnowledgeBase, owner_id=ctx.user.id, name=data.name, scope=data.scope)
    ctx.repo.audit(ctx.user.id, "knowledge.created", row.id, request.state.request_id, {"scope": row.scope})
    return ok(request, {"id": row.id, "name": row.name, "scope": row.scope, "canManage": True})


@router.get("/knowledge-bases/{base_id}/documents", response_model=Envelope[Page[DocumentOutput]])
def list_documents(
    base_id: str,
    request: Request,
    ctx: CTX,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    knowledge_base(ctx, base_id)
    rows, next_cursor = ctx.repo.page(
        Document, (Document.knowledge_base_id == base_id, Document.deleted_at.is_(None)), limit, cursor
    )
    return ok(request, {"items": [output(ctx, row) for row in rows], "nextCursor": next_cursor})


def accept_upload(ctx, request, file, key, base_id=None, conversation_id=None):
    store = ObjectStore(request.app.state.settings)
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = PurePath(name).suffix.lower()
    if not name or len(name) > 200 or re.search(r"[\x00-\x1f\x7f]", name) or suffix not in TYPES:
        raise AppError(422, "UNSUPPORTED_FILE", "请上传 PDF、docx、pptx、图片、HTML 或 UTF-8 文本资料。")
    content = file.file.read(request.app.state.settings.max_upload_bytes + 1)
    if not content:
        raise AppError(422, "EMPTY_FILE", "不能上传空文件。")
    if len(content) > request.app.state.settings.max_upload_bytes:
        raise AppError(413, "FILE_TOO_LARGE", "文件超过上传大小限制。")
    digest = hashlib.sha256(content).hexdigest()
    # Serialize requests with the same owner's operation key, including SQLite.
    lock(ctx.repo, User, ctx.user.id)
    row = ctx.repo.one(Document, Document.owner_id == ctx.user.id, Document.operation_key == key)
    if row:
        if (row.sha256, row.filename, row.knowledge_base_id, row.conversation_id) != (
            digest,
            name,
            base_id,
            conversation_id,
        ) or row.deleted_at:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "此上传标识已用于其他资料，请重新选择文件。")
        if row.status != "uploading":
            return output(ctx, row)
    else:
        identity = new_id()
        row = ctx.repo.add(
            Document,
            id=identity,
            owner_id=ctx.user.id,
            knowledge_base_id=base_id,
            conversation_id=conversation_id,
            filename=name,
            media_type=TYPES[suffix],
            size_bytes=len(content),
            sha256=digest,
            object_key=f"{ctx.repo.tenant_id}/documents/{identity}/original",
            operation_key=key,
        )
    identity = row.id
    # Persist recovery metadata before crossing the DB/object-store transaction boundary.
    ctx.repo.session.commit()
    row = lock(ctx.repo, Document, identity)
    document_access(ctx, identity, write=True)
    if row.status == "uploading":
        store.put(row.object_key, content)
        row.status = "queued"
        job = ctx.repo.add(IngestionJob, document_id=row.id)
        ctx.repo.add(OutboxEvent, kind="document.ingest", target_id=job.id)
        ctx.repo.audit(
            ctx.user.id,
            "document.uploaded",
            row.id,
            request.state.request_id,
            {"sizeBytes": row.size_bytes, "knowledgeBaseId": base_id},
        )
    return output(ctx, row)


@router.post("/knowledge-bases/{base_id}/documents", response_model=Envelope[DocumentOutput], status_code=201)
def upload_document(
    base_id: str,
    request: Request,
    ctx: CTX,
    file: UploadFile,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    knowledge_base(ctx, base_id, write=True)
    return ok(request, accept_upload(ctx, request, file, idempotency_key, base_id=base_id))


@router.post(
    "/conversations/{conversation_id}/attachments", response_model=Envelope[DocumentOutput], status_code=201
)
def upload_attachment(
    conversation_id: str,
    request: Request,
    ctx: CTX,
    file: UploadFile,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
    )
    return ok(request, accept_upload(ctx, request, file, idempotency_key, conversation_id=conversation_id))


@router.get("/conversations/{conversation_id}/attachments", response_model=Envelope[Page[DocumentOutput]])
def list_attachments(
    conversation_id: str,
    request: Request,
    ctx: CTX,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
    )
    rows, cursor = ctx.repo.page(
        Document, (Document.conversation_id == conversation_id, Document.deleted_at.is_(None)), limit, cursor
    )
    return ok(request, {"items": [output(ctx, row) for row in rows], "nextCursor": cursor})


@router.get("/documents/{document_id}", response_model=Envelope[DocumentOutput])
def get_document(document_id: str, request: Request, ctx: CTX):
    return ok(request, output(ctx, document_access(ctx, document_id)))


@router.patch("/documents/{document_id}", response_model=Envelope[DocumentOutput])
def tag_document(document_id: str, data: TagsInput, request: Request, ctx: CTX):
    row = document_access(ctx, document_id, write=True, locked=True)
    row.tags = data.tags
    ctx.repo.audit(ctx.user.id, "document.tagged", row.id, request.state.request_id)
    return ok(request, output(ctx, row))


@router.get("/documents/{document_id}/chunks", response_model=Envelope[Page[ChunkOutput]])
def chunks(document_id: str, request: Request, ctx: CTX, offset: int = Query(0, ge=0, le=10000)):
    document_access(ctx, document_id)
    rows = list(
        ctx.repo.session.scalars(
            ctx.repo.query(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.position)
            .offset(offset)
            .limit(20)
        )
    )
    return ok(
        request,
        {
            "items": [
                {"id": r.id, "position": r.position, "location": r.location, "content": r.content}
                for r in rows
            ],
            "nextCursor": str(offset + 20) if len(rows) == 20 else None,
        },
    )


@router.post("/documents/{document_id}/download", response_model=Envelope[DownloadOutput])
def download(document_id: str, request: Request, ctx: CTX):
    row = document_access(ctx, document_id, locked=True)
    if not row.scanned_at or row.status == "quarantined":
        raise AppError(409, "DOCUMENT_NOT_SAFE", "文件尚未通过安全扫描，暂不能下载。")
    url = ObjectStore(request.app.state.settings).download(row.object_key, row.filename)
    ctx.repo.audit(ctx.user.id, "document.downloaded", row.id, request.state.request_id)
    return ok(request, {"url": url, "expiresIn": 120})


@router.post("/documents/{document_id}/retry", response_model=Envelope[DocumentOutput])
def retry(document_id: str, request: Request, ctx: CTX):
    row = document_access(ctx, document_id, write=True, locked=True)
    if row.status != "failed":
        raise AppError(409, "DOCUMENT_NOT_RETRYABLE", "只有处理失败的资料可以重试；隔离文件请删除。")
    job = ctx.repo.require(IngestionJob, IngestionJob.document_id == row.id, lock=True)
    job.status, job.attempts, job.lease_token, job.lease_until = "queued", 0, None, None
    row.status, row.error_code, row.scanned_at = "queued", None, None
    ctx.repo.add(OutboxEvent, kind="document.ingest", target_id=job.id)
    ctx.repo.audit(ctx.user.id, "document.retried", row.id, request.state.request_id)
    return ok(request, output(ctx, row))


@router.delete("/documents/{document_id}", response_model=Envelope[None])
def remove(document_id: str, request: Request, ctx: CTX):
    row = document_access(ctx, document_id, write=True, locked=True)
    retire_document(ctx.repo, row, ctx.user.id, request.state.request_id)
    return ok(request)


def retire_document(repo, row, actor_id, request_id):
    row.status, row.deleted_at, row.chunk_count = "deleted", now(), 0
    repo.session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.tenant_id == repo.tenant_id, DocumentChunk.document_id == row.id
        )
    )
    job = repo.one(IngestionJob, IngestionJob.document_id == row.id, lock=True)
    if job:
        job.status, job.lease_token, job.lease_until = "cancelled", None, None
    index = repo.one(IndexJob, IndexJob.document_id == row.id, lock=True)
    if index:
        index.status, index.lease_token, index.lease_until = "cancelled", None, None
    repo.add(OutboxEvent, kind="document.delete", target_id=row.id)
    repo.audit(actor_id, "document.deleted", row.id, request_id)


@router.post("/documents/{document_id}/reindex", response_model=Envelope[DocumentOutput])
def reindex(document_id: str, request: Request, ctx: CTX):
    from .embeddings import active_config, index_key
    from .indexing import enqueue

    row = document_access(ctx, document_id, write=True, locked=True)
    spec = active_config(ctx.repo)
    if not spec:
        raise AppError(503, "EMBEDDING_NOT_CONFIGURED", "请先由管理员启用本地或外部向量模型。")
    if row.status not in ("parsed", "ready") or not row.chunk_count:
        raise AppError(409, "DOCUMENT_NOT_READY", "请先完成文档扫描与解析。")
    key = index_key(ctx.settings, spec)
    job = ctx.repo.one(IndexJob, IndexJob.document_id == row.id)
    if job and job.index_key == key and job.status in ("queued", "running"):
        raise AppError(409, "INDEX_IN_PROGRESS", "索引正在处理，请稍后查看。")
    enqueue(ctx.repo, row, key, force=True)
    ctx.repo.audit(ctx.user.id, "document.reindexed", row.id, request.state.request_id)
    return ok(request, output(ctx, row))
