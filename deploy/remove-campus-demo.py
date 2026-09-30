"""Remove only the rejected campus-demo-20260923-v1 batch, transactionally."""
from sqlalchemy import select, delete, text
from app.config import Settings
from app.database import make_engine, make_sessions
from app.models import (Tenant, AuditLog, ModelInvocation, Message, Conversation,
    DraftVersion, QuotaTransaction, QuotaReservation, QuotaAccount, User,
    Skill, SkillVersion, SkillReview, SkillTestCase, UserRole, Preferences, Workspace)

batch = "campus-demo-20260923-v1"
s = Settings()
with make_sessions(make_engine(s.database_url)).begin() as db:
    db.execute(text("SELECT pg_advisory_xact_lock(2026092301)"))
    tenant = db.scalar(select(Tenant).where(Tenant.slug == s.tenant_slug))
    calls = list(db.scalars(select(ModelInvocation).where(
        ModelInvocation.tenant_id == tenant.id,
        ModelInvocation.config_snapshot["seedBatch"].as_string() == batch)))
    if not calls:
        print("No rejected batch remains")
    else:
        conversations = [c.conversation_id for c in calls]
        assert len(calls) == 339
        # Abort if a seeded conversation acquired any genuine subsequent messages.
        messages = list(db.scalars(select(Message.id).where(Message.conversation_id.in_(conversations))))
        assert set(messages) == {m for c in calls for m in (c.user_message_id, c.assistant_message_id)}
        def remove(model, condition):
            result = db.execute(delete(model).where(model.tenant_id == tenant.id, condition))
            print(model.__tablename__, result.rowcount)
        remove(ModelInvocation, ModelInvocation.id.in_([c.id for c in calls]))
        remove(DraftVersion, DraftVersion.conversation_id.in_(conversations))
        remove(Message, Message.conversation_id.in_(conversations))
        remove(Conversation, Conversation.id.in_(conversations))
        people = ["chenyu", "liming", "wanglin", "zhangwei", "liufang", "zhaoning", "sunhao", "zhoumin", "wutong", "xujie", "hejun", "linxi"]
        users = list(db.scalars(select(User.id).where(User.tenant_id == tenant.id, User.username.in_(["demo."+p for p in people]))))
        accounts = list(db.scalars(select(QuotaAccount.id).where(QuotaAccount.tenant_id == tenant.id, QuotaAccount.user_id.in_(users))))
        assert not list(db.scalars(select(QuotaTransaction.id).where(QuotaTransaction.account_id.in_(accounts), ~QuotaTransaction.operation_key.startswith(batch))))
        remove(QuotaTransaction, QuotaTransaction.operation_key.startswith(batch))
        remove(QuotaReservation, QuotaReservation.operation_key.startswith(batch))
        skills = list(db.scalars(select(Skill.id).where(Skill.tenant_id == tenant.id, Skill.reviewer_notes == "模拟上线资料，用于展示校园日常工作流程。")))
        assert len(skills) == 6
        for cls in (SkillReview, SkillTestCase, SkillVersion):
            remove(cls, cls.skill_id.in_(skills))
        remove(Skill, Skill.id.in_(skills))
        for cls in (QuotaAccount, UserRole, Preferences, Workspace):
            remove(cls, cls.user_id.in_(users))
        remove(User, User.id.in_(users))
        remove(AuditLog, AuditLog.request_id == batch)
        db.add(AuditLog(tenant_id=tenant.id, action="demo.seed.removed", request_id=batch,
            details={"reason": "用户要求实际模型生成，撤销直接插入的模拟批次", "conversations": len(calls)}))
print("Committed")
