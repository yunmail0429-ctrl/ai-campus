from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import CTX
from .billing import lock_account
from .chat_runtime import expire_for_conversation, prepare, stream_chat
from .draft_exports import MIMES, render
from .errors import AppError, Envelope, Page, ok
from .knowledge import lock, retire_document
from .models import Conversation, Document, Message, ModelInvocation, QuotaAccount, iso_utc, now
from .retrieval import accessible_citations
from .safety import redact
from .schemas import ConversationInput, ConversationOutput, DraftInput, MessageInput, MessageOutput

router = APIRouter(prefix="/api/v1/conversations", tags=["workspace"])


def owned(ctx, conversation_id, lock=False):
    return ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
        lock=lock,
    )


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


class FeedbackInput(BaseModel):
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
        document = lock(ctx.repo, Document, document.id)
        retire_document(ctx.repo, document, ctx.user.id, request.state.request_id)
    ctx.repo.audit(ctx.user.id, "conversation.deleted", row.id, request.state.request_id)
    return ok(request)


@router.get("/{conversation_id}/draft", response_model=Envelope[DraftInput])
def draft(conversation_id: str, request: Request, ctx: CTX):
    return ok(request, {"content": owned(ctx, conversation_id).draft})


@router.put("/{conversation_id}/draft", response_model=Envelope[DraftInput])
def save_draft(conversation_id: str, data: DraftInput, request: Request, ctx: CTX):
    row = owned(ctx, conversation_id, lock=True)
    row.draft = data.content
    return ok(request, data)


class ExportInput(DraftInput):
    format: Literal["docx", "pdf", "md"]


@router.post("/{conversation_id}/export", response_class=Response)
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
