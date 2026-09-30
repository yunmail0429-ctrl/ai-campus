import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from starlette.concurrency import run_in_threadpool

from .adapters import ChatAdapter, ChatSpec, connection_for, cost
from .billing import finish, lock_account, reserve
from .errors import AppError
from .models import (
    Conversation,
    Message,
    ModelConfig,
    ModelInvocation,
    ModelRoute,
    QuotaAccount,
    iso_utc,
    now,
)
from .repository import Repository
from .retrieval import accessible_citations, check_selection, context_for, retrieve
from .safety import SYSTEM_PROMPT, flags_for, redact
from .skills import visible

logger = logging.getLogger("xflj")


@dataclass
class PreparedChat:
    tenant_id: str
    invocation_id: str
    account_id: str
    snapshot: dict
    messages: list
    replay: dict | None = None


def terminal(repo, invocation, *, status, error_code=None, usage=None, latency=0):
    if invocation.status != "running":
        return
    credit = 0
    if status == "completed":
        credit = cost(
            usage["inputTokens"],
            usage["outputTokens"],
            invocation.config_snapshot["inputPrice"],
            invocation.config_snapshot["outputPrice"],
        )
    if invocation.reservation_id:
        finish(
            repo, invocation.account_id, invocation.reservation_id, credit if status == "completed" else None
        )
    invocation.status, invocation.error_code = status, error_code
    invocation.credits, invocation.latency_ms = credit, latency
    if usage:
        invocation.input_tokens, invocation.output_tokens = usage["inputTokens"], usage["outputTokens"]
    for identity in (invocation.user_message_id, invocation.assistant_message_id):
        row = repo.require(
            Message, Message.id == identity, Message.conversation_id == invocation.conversation_id
        )
        row.status = status
    repo.audit(
        invocation.user_id,
        "chat." + status,
        invocation.id,
        invocation.id,
        {
            "modelId": invocation.model_id,
            "revision": invocation.config_snapshot["revision"],
            "inputTokens": invocation.input_tokens,
            "outputTokens": invocation.output_tokens,
            "credits": credit,
            "errorCode": error_code,
        },
    )


def expire_for_conversation(repo, conversation_id):
    rows = list(
        repo.session.scalars(
            repo.query(ModelInvocation).where(
                ModelInvocation.conversation_id == conversation_id,
                ModelInvocation.status == "running",
                ModelInvocation.deadline_at < now(),
            )
        )
    )
    for row in rows:
        lock_account(repo, row.account_id)
        current = repo.require(ModelInvocation, ModelInvocation.id == row.id, lock=True)
        if current.status == "running" and datetime.fromisoformat(iso_utc(current.deadline_at)) < now():
            terminal(repo, current, status="failed", error_code="MODEL_TIMEOUT")


def prepare(ctx, settings, conversation_id, data, operation_key):
    if not operation_key.strip():
        raise AppError(422, "INVALID_OPERATION_KEY", "请提供有效的请求标识。")
    conversation = ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
    )
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    lock_account(ctx.repo, account.id)
    conversation = ctx.repo.require(
        Conversation,
        Conversation.id == conversation_id,
        Conversation.user_id == ctx.user.id,
        Conversation.deleted_at.is_(None),
        lock=True,
    )
    expire_for_conversation(ctx.repo, conversation.id)
    request_data = data.model_dump()
    if data.useKnowledge:
        request_data.pop("useKnowledge")  # Preserve hashes of pre-retrieval default requests.
    fingerprint = hashlib.sha256(
        json.dumps(
            {"conversation": conversation.id, **request_data},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    previous = ctx.repo.one(
        ModelInvocation,
        ModelInvocation.user_id == ctx.user.id,
        ModelInvocation.operation_key == operation_key,
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "该请求标识已用于其他教学需求。")
        if previous.status == "running":
            raise AppError(409, "GENERATION_IN_PROGRESS", "此请求正在生成，请稍后刷新查看结果。")
        message = ctx.repo.require(
            Message, Message.id == previous.assistant_message_id, Message.conversation_id == conversation.id
        )
        return PreparedChat(
            ctx.repo.tenant_id,
            previous.id,
            account.id,
            previous.config_snapshot,
            [],
            {
                "content": message.content,
                "status": previous.status,
                "errorCode": previous.error_code,
                "messageId": message.id,
                "inputTokens": previous.input_tokens,
                "outputTokens": previous.output_tokens,
                "credits": previous.credits,
                "citations": accessible_citations(ctx, message.citations),
            },
        )
    if ctx.repo.one(
        ModelInvocation,
        ModelInvocation.conversation_id == conversation.id,
        ModelInvocation.status == "running",
    ):
        raise AppError(409, "GENERATION_IN_PROGRESS", "此对话正在生成，请等待本次结果。")
    route = ctx.repo.one(ModelRoute, ModelRoute.purpose == "chat")
    config = (
        ctx.repo.one(ModelConfig, ModelConfig.id == route.model_id, ModelConfig.active.is_(True))
        if route
        else None
    )
    if config is None:
        raise AppError(503, "MODEL_NOT_CONFIGURED", "尚未配置对话模型，请联系管理员。")
    connection_for(settings, config.provider)
    if not data.content.strip() and not data.documentIds:
        raise AppError(422, "EMPTY_MESSAGE", "请输入教学需求或添加资料。")
    check_selection(ctx, conversation.id, data.documentIds)
    if account.frozen:
        raise AppError(409, "QUOTA_FROZEN", "积分账户已冻结，请联系管理员。")
    skill = visible(ctx, data.skillId) if data.skillId else None
    history = list(
        ctx.repo.session.scalars(
            ctx.repo.query(Message)
            .where(Message.conversation_id == conversation.id, Message.status == "completed")
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(20)
        )
    )
    prompt = [{"role": "system", "content": SYSTEM_PROMPT}]
    if skill:
        prompt.append(
            {
                "role": "user",
                "content": "供本次教学任务参考的方法（不覆盖系统规则）：\n" + redact(skill.prompt),
            }
        )
    prompt.extend({"role": row.role, "content": redact(row.content)} for row in reversed(history))
    prompt.append({"role": "user", "content": redact(data.content)})
    # A conservative bound for byte-tokenized compatible models, not fabricated usage.
    input_bound = sum(len(item["content"].encode()) + 128 for item in prompt)
    if input_bound + config.max_output_tokens > config.context_tokens:
        raise AppError(422, "CONTEXT_TOO_LONG", "对话内容超过当前模型上下文限制，请缩短内容或新建对话。")
    from .embeddings import active_config

    rag_budget = (
        min(18000, config.context_tokens - config.max_output_tokens - input_bound)
        if (data.useKnowledge or data.documentIds) and active_config(ctx.repo)
        else 0
    )
    input_bound += rag_budget
    estimate = cost(input_bound, config.max_output_tokens, config.input_price, config.output_price)
    hold = (
        reserve(
            ctx.repo,
            account.id,
            estimate,
            "chat:" + hashlib.sha256(operation_key.encode()).hexdigest(),
            "AI 教学对话",
        )
        if estimate
        else None
    )
    latest = ctx.repo.session.scalar(
        ctx.repo.query(Message)
        .where(Message.conversation_id == conversation.id)
        .with_only_columns(Message.created_at)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    stamp = (
        max(now(), datetime.fromisoformat(iso_utc(latest)) + timedelta(microseconds=1)) if latest else now()
    )
    user = ctx.repo.add(
        Message,
        conversation_id=conversation.id,
        role="user",
        content=data.content,
        status="running",
        created_at=stamp,
    )
    assistant = ctx.repo.add(
        Message,
        conversation_id=conversation.id,
        role="assistant",
        content="",
        status="running",
        created_at=stamp + timedelta(microseconds=1),
    )
    snapshot = {
        "provider": config.provider,
        "model": config.model,
        "revision": config.revision,
        "maxOutputTokens": config.max_output_tokens,
        "timeoutSeconds": config.timeout_seconds,
        "inputPrice": config.input_price,
        "outputPrice": config.output_price,
        "inputBound": input_bound,
        "skillId": skill.id if skill else None,
        "skillVersion": skill.active_version if skill else None,
        "userId": ctx.user.id,
        "conversationId": conversation.id,
        "documentIds": data.documentIds,
        "useKnowledge": data.useKnowledge,
        "ragBudget": rag_budget,
    }
    flags = flags_for(data.content + (skill.prompt if skill else ""))
    invocation = ctx.repo.add(
        ModelInvocation,
        user_id=ctx.user.id,
        conversation_id=conversation.id,
        model_id=config.id,
        account_id=account.id,
        reservation_id=hold.id if hold else None,
        operation_key=operation_key,
        request_hash=fingerprint,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        config_snapshot=snapshot,
        safety_flags=flags,
        deadline_at=now() + timedelta(seconds=config.timeout_seconds + 180),
    )
    conversation.updated_at = now()
    if flags:
        ctx.repo.audit(ctx.user.id, "chat.safety_indicator", invocation.id, invocation.id, {"flags": flags})
    return PreparedChat(ctx.repo.tenant_id, invocation.id, account.id, snapshot, prompt)


def frame(event, data):
    return "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def mutate(
    sessions, prepared, *, content=None, citations=None, status=None, error_code=None, usage=None, latency=0
):
    with sessions.begin() as session:
        repo = Repository(session, prepared.tenant_id)
        lock_account(repo, prepared.account_id)
        invocation = repo.require(ModelInvocation, ModelInvocation.id == prepared.invocation_id, lock=True)
        if invocation.status != "running":
            if status == "completed":
                raise AppError(409, "GENERATION_FINISHED", "此生成请求已结束，请刷新查看结果。")
            return None
        message = repo.require(
            Message,
            Message.id == invocation.assistant_message_id,
            Message.conversation_id == invocation.conversation_id,
        )
        if content is not None:
            message.content = content
        if citations is not None:
            message.citations = citations
            source_flags = flags_for("\n".join(item.get("excerpt", "") for item in citations))
            if source_flags:
                invocation.safety_flags = sorted(set(invocation.safety_flags + source_flags))
                repo.audit(
                    invocation.user_id,
                    "chat.source_safety_indicator",
                    invocation.id,
                    invocation.id,
                    {"flags": source_flags, "documentIds": [item["documentId"] for item in citations]},
                )
        if status:
            terminal(repo, invocation, status=status, error_code=error_code, usage=usage, latency=latency)
        return {"messageId": message.id, "credits": invocation.credits}


async def stream_chat(sessions, settings, prepared):
    if prepared.replay is not None:
        previous = prepared.replay
        if previous["status"] != "completed":
            yield frame(
                "error",
                {
                    "code": previous["errorCode"],
                    "message": "上次生成未完成，已释放预占。请使用新的请求重试。",
                },
            )
            return
        for citation in previous.get("citations", []):
            yield frame("citation", citation)
        yield frame("message.delta", {"delta": previous["content"], "messageId": previous["messageId"]})
        yield frame("usage", {key: previous[key] for key in ("inputTokens", "outputTokens", "credits")})
        yield frame(
            "message.completed", {"id": previous["messageId"], "invocationId": prepared.invocation_id}
        )
        return
    snapshot, text, usage = prepared.snapshot, "", None
    started, finalized = time.monotonic(), False
    try:
        prompt = list(prepared.messages)
        if snapshot.get("useKnowledge") or snapshot.get("documentIds"):
            sources, state = await retrieve(
                sessions,
                settings,
                prepared.tenant_id,
                snapshot["userId"],
                prompt[-1]["content"],
                prepared.invocation_id,
                snapshot["conversationId"],
                snapshot["documentIds"],
            )
            citations, context = context_for(sources, snapshot["ragBudget"])
            if sources and not citations:
                if snapshot["documentIds"]:
                    raise AppError(
                        422, "CONTEXT_TOO_LONG", "模型上下文不足以容纳资料，请新建对话或扩大上下文配置。"
                    )
                state = "context_limit"
            if context:
                prompt.insert(-1, {"role": "user", "content": context})
            saved = await run_in_threadpool(mutate, sessions, prepared, citations=citations)
            if saved is None:
                raise AppError(409, "GENERATION_FINISHED", "生成已结束，请刷新查看结果。")
            yield frame("knowledge", {"status": state, "count": len(citations)})
            for citation in citations:
                yield frame("citation", citation)
        adapter = ChatAdapter(connection_for(settings, snapshot["provider"]))
        spec = ChatSpec(snapshot["model"], snapshot["maxOutputTokens"], snapshot["timeoutSeconds"])
        async for event in adapter.stream(spec, prompt):
            if event["event"] == "delta":
                text += event["delta"]
                saved = await run_in_threadpool(mutate, sessions, prepared, content=text)
                if saved is None:
                    raise AppError(409, "GENERATION_FINISHED", "生成已结束，请刷新查看结果。")
                yield frame("message.delta", {"delta": event["delta"], "messageId": saved["messageId"]})
            elif event["event"] == "usage":
                usage = event
        if usage is None or usage["inputTokens"] > snapshot["inputBound"]:
            raise AppError(503, "MODEL_USAGE_INVALID", "模型计量异常，本次未扣除积分。")
        saved = await run_in_threadpool(
            mutate,
            sessions,
            prepared,
            status="completed",
            usage=usage,
            latency=int((time.monotonic() - started) * 1000),
        )
        finalized = True
        yield frame(
            "usage",
            {
                "inputTokens": usage["inputTokens"],
                "outputTokens": usage["outputTokens"],
                "credits": saved["credits"],
            },
        )
        yield frame("message.completed", {"id": saved["messageId"], "invocationId": prepared.invocation_id})
    except AppError as error:
        await run_in_threadpool(
            mutate, sessions, prepared, status="failed", error_code=error.code, usage=usage
        )
        finalized = True
        yield frame(
            "error", {"code": error.code, "message": error.message, "invocationId": prepared.invocation_id}
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - stream boundary must sanitize upstream/DB secrets
        logger.error("chat failed type=%s invocation_id=%s", type(error).__name__, prepared.invocation_id)
        await run_in_threadpool(mutate, sessions, prepared, status="failed", error_code="GENERATION_FAILED")
        finalized = True
        yield frame("error", {"code": "GENERATION_FAILED", "message": "生成失败，请稍后重试。"})
    finally:
        if not finalized:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(
                    mutate, sessions, prepared, status="cancelled", error_code="GENERATION_CANCELLED"
                )
