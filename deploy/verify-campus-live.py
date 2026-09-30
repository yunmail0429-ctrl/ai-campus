"""Read-only verification of actual persisted model invocations and outputs."""
import json
from sqlalchemy import select, func
from app.config import Settings
from app.database import make_engine, make_sessions
from app.models import (Tenant, ModelInvocation, Message, Conversation, DraftVersion,
    QuotaAccount, QuotaTransaction, QuotaReservation, Skill)

s = Settings()
with make_sessions(make_engine(s.database_url))() as db:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == s.tenant_slug))
    calls = list(db.scalars(select(ModelInvocation).where(
        ModelInvocation.tenant_id == tenant.id,
        ModelInvocation.operation_key.startswith("campus-live-20260923-v1:"))))
    assert len(calls) == 36, len(calls)
    assert all(c.status == "completed" and c.input_tokens > 0 and c.output_tokens > 0 and c.credits > 0 for c in calls)
    assert all(not c.config_snapshot.get("simulation") for c in calls)
    assert len({c.operation_key for c in calls}) == 36
    assert len({c.conversation_id for c in calls}) == 30
    answers = []
    for call in calls:
        answer = db.get(Message, call.assistant_message_id)
        assert answer.status == "completed" and len(answer.content) > 120
        answers.append(answer.content)
        reservation = db.get(QuotaReservation, call.reservation_id)
        assert reservation.status == "settled" and reservation.settled_amount == call.credits
        settlement = db.scalar(select(QuotaTransaction).where(
            QuotaTransaction.reservation_id == reservation.id, QuotaTransaction.kind == "settle"))
        assert settlement.balance_delta == -call.credits and settlement.created_at >= call.created_at
    assert len(set(answers)) == len(answers)
    for cid in {c.conversation_id for c in calls}:
        conversation = db.get(Conversation, cid)
        latest = max([c for c in calls if c.conversation_id == cid], key=lambda c: c.created_at)
        assert conversation.draft == db.get(Message, latest.assistant_message_id).content
        assert db.scalar(select(func.count()).select_from(DraftVersion).where(DraftVersion.conversation_id == cid)) >= 1
    skills = list(db.scalars(select(Skill).where(Skill.tenant_id == tenant.id,
        Skill.description == "由当前接入的大模型生成的校园工作助手提示词，待教师审核。")))
    assert len(skills) == 6
    assert all(skill.status == "draft" and skill.prompt in answers for skill in skills)
    assert not db.scalar(select(func.count()).select_from(ModelInvocation).where(
        ModelInvocation.tenant_id == tenant.id,
        ModelInvocation.config_snapshot["seedBatch"].as_string() == "campus-demo-20260923-v1"))
    for account in db.scalars(select(QuotaAccount).where(QuotaAccount.tenant_id == tenant.id)):
        balance, reserved = db.execute(select(func.coalesce(func.sum(QuotaTransaction.balance_delta), 0),
            func.coalesce(func.sum(QuotaTransaction.reserved_delta), 0)).where(QuotaTransaction.account_id == account.id)).one()
        assert (account.balance, account.reserved) == (balance, reserved)
    print(json.dumps({"verified": True, "model": sorted({c.config_snapshot['model'] for c in calls}),
        "calls": len(calls), "conversations": 30, "drafts": 30, "skillsPendingReview": len(skills),
        "inputTokens": sum(c.input_tokens for c in calls), "outputTokens": sum(c.output_tokens for c in calls),
        "credits": sum(c.credits for c in calls), "firstCall": min(c.created_at for c in calls).isoformat(),
        "lastCall": max(c.created_at for c in calls).isoformat(), "oldSimulatedBatchRemaining": 0,
        "checks": ["real_call_metadata", "unique_outputs", "draft_matches_model_output", "settlements_match_usage", "ledger_balances", "no_fabricated_skill_reviews"]}, ensure_ascii=False))
