from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import ConfigDict, Field
from sqlalchemy import func

from .auth import CTX
from .billing import lock_account
from .chat_runtime import expire_for_conversation, prepare, stream_chat
from .draft_exports import MIMES, render
from .errors import AppError, Envelope, Page, ok
from .knowledge import lock as lock_record
from .knowledge import retire_document
from .models import Conversation, Document, DraftVersion, Message, ModelInvocation, QuotaAccount, iso_utc, now
from .retrieval import accessible_citations
from .safety import redact
from .schemas import ConversationInput, ConversationOutput, DraftInput, Input, MessageInput, MessageOutput

router = APIRouter(prefix="/api/v1/conversations", tags=["workspace"])


def owned(ctx, conversation_id, lock=False):
    row = ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
    )
    if lock:
        row = lock_record(ctx.repo, Conversation, conversation_id)
        if row.deleted_at:
            raise AppError(404, "NOT_FOUND", "记录不存在或无权访问。")
    return row


def serialize(row):
    return {"id": row.id, "title": row.title, "updatedAt": iso_utc(row.updated_at)}


def feedback_output(row):
    if row.feedback_rating is None:
        return None
    return {
        "rating": row.feedback_rating,
        "comment": row.feedback_comment or "",
        "updatedAt": iso_utc(row.feedback_at),
    }


class FeedbackInput(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rating: int | None = Field(ge=1, le=5, strict=True)
    comment: str = Field(default="", max_length=1000)


@router.put("/{conversation_id}/messages/{message_id}/feedback", response_model=Envelope[dict | None])
def save_feedback(conversation_id: str, message_id: str, data: FeedbackInput, request: Request, ctx: CTX):
    owned(ctx, conversation_id, lock=True)
    invocation = ctx.repo.require(
        ModelInvocation,
        ModelInvocation.conversation_id == conversation_id,
        ModelInvocation.assistant_message_id == message_id,
        ModelInvocation.user_id == ctx.user.id,
        lock=True,
    )
    if invocation.status != "completed":
        raise AppError(409, "ANSWER_NOT_COMPLETED", "请在回答完成后提交评价。")
    comment = redact(data.comment) if data.rating is not None else None
    if invocation.feedback_rating != data.rating or invocation.feedback_comment != comment:
        invocation.feedback_rating, invocation.feedback_comment = data.rating, comment
        invocation.feedback_at = now() if data.rating is not None else None
        ctx.repo.audit(
            ctx.user.id,
            "answer.feedback_changed",
            invocation.id,
            request.state.request_id,
            {"rating": data.rating},
        )
    return ok(request, feedback_output(invocation))


@router.get("", response_model=Envelope[Page[ConversationOutput]])
def list_conversations(
    request: Request,
    ctx: CTX,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(
        Conversation, (Conversation.user_id == ctx.user.id, Conversation.deleted_at.is_(None)), limit, cursor
    )
    return ok(request, {"items": [serialize(row) for row in rows], "nextCursor": next_cursor})


@router.post("", response_model=Envelope[ConversationOutput], status_code=201)
def create_conversation(data: ConversationInput, request: Request, ctx: CTX):
    row = ctx.repo.add(Conversation, user_id=ctx.user.id, title=data.title)
    return ok(request, serialize(row))


@router.patch("/{conversation_id}", response_model=Envelope[ConversationOutput])
def rename(conversation_id: str, data: ConversationInput, request: Request, ctx: CTX):
    row = owned(ctx, conversation_id, lock=True)
    row.title = data.title
    ctx.repo.session.flush()
    return ok(request, serialize(row))


@router.delete("/{conversation_id}", response_model=Envelope[None])
def remove(conversation_id: str, request: Request, ctx: CTX):
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    lock_account(ctx.repo, account.id)
    row = owned(ctx, conversation_id, lock=True)
    expire_for_conversation(ctx.repo, row.id)
    if ctx.repo.one(
        ModelInvocation, ModelInvocation.conversation_id == row.id, ModelInvocation.status == "running"
    ):
        raise AppError(409, "GENERATION_IN_PROGRESS", "对话正在生成，请先停止生成后再删除。")
    row.deleted_at = now()
    documents = list(
        ctx.repo.session.scalars(
            ctx.repo.query(Document)
            .where(Document.conversation_id == row.id, Document.deleted_at.is_(None))
            .order_by(Document.id)
        )
    )
    for document in documents:
        document = lock_record(ctx.repo, Document, document.id)
        retire_document(ctx.repo, document, ctx.user.id, request.state.request_id)
    ctx.repo.audit(ctx.user.id, "conversation.deleted", row.id, request.state.request_id)
    return ok(request)


class DraftOutput(DraftInput):
    revision: int


class SaveDraftInput(DraftInput):
    revision: int = Field(strict=True, ge=0, le=2_000_000_000)


class RestoreDraftInput(Input):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0, le=2_000_000_000)


def draft_revision(repo, conversation_id):
    return (
        repo.session.scalar(
            repo.query(DraftVersion)
            .where(DraftVersion.conversation_id == conversation_id)
            .with_only_columns(func.max(DraftVersion.number))
        )
        or 0
    )


def write_draft(ctx, row, content, expected, request, restored_from=None):
    revision = draft_revision(ctx.repo, row.id)
    # Retrying an identical save after an uncertain response is harmless.
    if row.draft == content:
        return {"content": row.draft, "revision": revision}
    if expected != revision:
        raise AppError(
            409,
            "DRAFT_VERSION_CONFLICT",
            "草稿已在其他页面更新，你的编辑内容已保留，请查看最新版本后再保存。",
        )
    version = ctx.repo.add(
        DraftVersion,
        conversation_id=row.id,
        number=revision + 1,
        content=content,
        restored_from=restored_from,
    )
    row.draft = content
    ctx.repo.audit(
        ctx.user.id,
        "draft.restored" if restored_from is not None else "draft.saved",
        row.id,
        request.state.request_id,
        {"revision": version.number, "restoredFrom": restored_from},
    )
    return {"content": content, "revision": version.number}


@router.get("/{conversation_id}/draft", response_model=Envelope[DraftOutput])
def draft(conversation_id: str, request: Request, ctx: CTX):
    row = owned(ctx, conversation_id, lock=True)
    return ok(request, {"content": row.draft, "revision": draft_revision(ctx.repo, row.id)})


@router.put("/{conversation_id}/draft", response_model=Envelope[DraftOutput])
def save_draft(conversation_id: str, data: SaveDraftInput, request: Request, ctx: CTX):
    row = owned(ctx, conversation_id, lock=True)
    return ok(request, write_draft(ctx, row, data.content, data.revision, request))


@router.get("/{conversation_id}/draft/versions", response_model=Envelope[Page[dict]])
def draft_versions(
    conversation_id: str,
    request: Request,
    ctx: CTX,
    limit: int = Query(20, ge=1, le=100),
    cursor: int | None = Query(None, ge=1, le=2_147_483_647),
):
    owned(ctx, conversation_id)
    statement = ctx.repo.query(DraftVersion).where(DraftVersion.conversation_id == conversation_id)
    if cursor is not None:
        statement = statement.where(DraftVersion.number < cursor)
    rows = list(ctx.repo.session.scalars(statement.order_by(DraftVersion.number.desc()).limit(limit + 1)))
    next_cursor = str(rows[limit - 1].number) if len(rows) > limit else None
    rows = rows[:limit]
    return ok(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "number": row.number,
                    "createdAt": iso_utc(row.created_at),
                    "restoredFrom": row.restored_from,
                }
                for row in rows
            ],
            "nextCursor": next_cursor,
        },
    )


@router.get("/{conversation_id}/draft/versions/{number}", response_model=Envelope[dict])
def draft_version(conversation_id: str, number: int, request: Request, ctx: CTX):
    owned(ctx, conversation_id)
    row = ctx.repo.require(
        DraftVersion, DraftVersion.conversation_id == conversation_id, DraftVersion.number == number
    )
    return ok(
        request,
        {
            "content": row.content,
            "revision": row.number,
            "createdAt": iso_utc(row.created_at),
            "restoredFrom": row.restored_from,
        },
    )


@router.post("/{conversation_id}/draft/versions/{number}/restore", response_model=Envelope[DraftOutput])
def restore_draft(conversation_id: str, number: int, data: RestoreDraftInput, request: Request, ctx: CTX):
    row = owned(ctx, conversation_id, lock=True)
    previous = ctx.repo.require(
        DraftVersion, DraftVersion.conversation_id == conversation_id, DraftVersion.number == number
    )
    return ok(request, write_draft(ctx, row, previous.content, data.revision, request, previous.number))


class ExportInput(DraftInput):
    format: Literal["docx", "pdf", "md"]


@router.post(
    "/{conversation_id}/export",
    response_class=Response,
    responses={
        200: {
            "description": "当前编辑稿文件",
            "content": {
                mime.split(";")[0]: {"schema": {"type": "string", "format": "binary"}}
                for mime in MIMES.values()
            },
        }
    },
)
def export_draft(conversation_id: str, data: ExportInput, request: Request, ctx: CTX):
    conversation = owned(ctx, conversation_id)
    content = render(conversation.title, data.content, data.format)
    ctx.repo.audit(
        ctx.user.id,
        "draft.exported",
        conversation.id,
        request.state.request_id,
        {"format": data.format, "bytes": len(content)},
    )
    return Response(
        content,
        media_type=MIMES[data.format],
        headers={
            "Content-Disposition": f'attachment; filename="teaching-draft-{conversation.id}.{data.format}"',
        },
    )


@router.get("/{conversation_id}/messages", response_model=Envelope[Page[MessageOutput]])
def messages(
    conversation_id: str,
    request: Request,
    ctx: CTX,
    limit: int = Query(100, ge=1, le=200),
    cursor: str | None = Query(None, max_length=500),
):
    owned(ctx, conversation_id)
    expire_for_conversation(ctx.repo, conversation_id)
    rows, next_cursor = ctx.repo.page(Message, (Message.conversation_id == conversation_id,), limit, cursor)
    feedback = {
        row.assistant_message_id: feedback_output(row)
        for row in ctx.repo.session.scalars(
            ctx.repo.query(ModelInvocation).where(
                ModelInvocation.conversation_id == conversation_id,
                ModelInvocation.assistant_message_id.in_([message.id for message in rows]),
            )
        )
    }
    return ok(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "role": row.role,
                    "content": row.content,
                    "citations": accessible_citations(ctx, row.citations),
                    "status": row.status,
                    "feedback": feedback.get(row.id),
                }
                for row in reversed(rows)
            ],
            "nextCursor": next_cursor,
        },
    )


@router.post(
    "/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def send_message(
    conversation_id: str,
    data: MessageInput,
    request: Request,
    ctx: CTX,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    if not data.content.strip() and not data.documentIds:
        raise AppError(422, "EMPTY_MESSAGE", "请输入教学需求或添加资料。")
    prepared = prepare(ctx, request.app.state.settings, conversation_id, data, idempotency_key)
    return StreamingResponse(
        stream_chat(request.app.state.sessions, request.app.state.settings, prepared),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
