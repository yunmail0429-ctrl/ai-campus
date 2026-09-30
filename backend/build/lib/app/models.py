import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def iso_utc(value: datetime):
    """SQLite drops tzinfo; stored timestamps are always UTC in both backends."""
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Tenant(Row, Base):
    __tablename__ = "tenants"
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Row, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "username"))
    username: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(30))
    subject: Mapped[str] = mapped_column(String(30), default="")
    grade: Mapped[str] = mapped_column(String(30), default="高一")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Row, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "name"))
    name: Mapped[str] = mapped_column(String(30))


class UserRole(Row, Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "role_id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36))
    role_id: Mapped[str] = mapped_column(String(36))


class AuthSession(Row, Base):
    __tablename__ = "sessions"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginAttempt(Row, Base):
    __tablename__ = "login_attempts"
    key_hash: Mapped[str] = mapped_column(String(64), index=True)


class Preferences(Row, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Workspace(Row, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36))


class Conversation(Row, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(60))
    draft: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Row, Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
    )
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="completed")


class QuotaAccount(Row, Base):
    __tablename__ = "quota_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id", name="uq_quota_account_tenant_id"),
        CheckConstraint("balance >= 0"),
        CheckConstraint("reserved >= 0 AND reserved <= balance", name="ck_quota_reserved"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36))
    balance: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)


class QuotaReservation(Row, Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "operation_key"),
        UniqueConstraint("tenant_id", "account_id", "id"),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["quota_accounts.tenant_id", "quota_accounts.id"]),
        CheckConstraint("amount > 0 AND settled_amount >= 0 AND settled_amount <= amount"),
        CheckConstraint("status IN ('reserved', 'settled', 'released')"),
    )
    account_id: Mapped[str] = mapped_column(String(36))
    operation_key: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)
    settled_amount: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    reason: Mapped[str] = mapped_column(String(300))


class QuotaTransaction(Row, Base):
    __tablename__ = "quota_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "kind", "operation_key"),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["quota_accounts.tenant_id", "quota_accounts.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "reservation_id"],
            ["quota_reservations.tenant_id", "quota_reservations.account_id", "quota_reservations.id"],
        ),
        CheckConstraint("balance_after >= 0 AND reserved_after >= 0 AND reserved_after <= balance_after"),
        CheckConstraint("kind IN ('adjustment', 'reserve', 'settle', 'release')"),
    )
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    operation_key: Mapped[str] = mapped_column(String(100))
    balance_delta: Mapped[int] = mapped_column(Integer)
    reserved_delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reserved_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300))


class AuditLog(Row, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Skill(Row, Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
    )
    owner_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(300))
    prompt: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20), default="private")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=0)
    active_version: Mapped[int] = mapped_column(Integer, default=0)
    risk_boundary: Mapped[str] = mapped_column(Text, default="")
    grades: Mapped[list] = mapped_column(JSON, default=list)
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")


class SkillVersion(Row, Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "skill_id", "number"),
        ForeignKeyConstraint(["tenant_id", "skill_id"], ["skills.tenant_id", "skills.id"]),
    )
    skill_id: Mapped[str] = mapped_column(String(36))
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)


class SkillTestCase(Row, Base):
    __tablename__ = "skill_test_cases"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "skill_id"], ["skills.tenant_id", "skills.id"]),
        UniqueConstraint("tenant_id", "skill_id", "position", name="uq_skill_test_case_position"),
    )
    skill_id: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str] = mapped_column(Text)


class SkillReview(Row, Base):
    __tablename__ = "skill_reviews"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "skill_id"], ["skills.tenant_id", "skills.id"]),)
    skill_id: Mapped[str] = mapped_column(String(36))
    reviewer_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(Text)


class ModelConfig(Row, Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint(
            "context_tokens > 0 AND max_output_tokens > 0 AND max_output_tokens < context_tokens"
        ),
        CheckConstraint("input_price >= 0 AND output_price >= 0"),
    )
    name: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    context_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90)
    input_price: Mapped[int] = mapped_column(Integer)
    output_price: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ModelRoute(Row, Base):
    __tablename__ = "model_routes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "purpose"),
        ForeignKeyConstraint(["tenant_id", "model_id"], ["model_configs.tenant_id", "model_configs.id"]),
    )
    purpose: Mapped[str] = mapped_column(String(30), default="chat")
    model_id: Mapped[str] = mapped_column(String(36))


class ModelPrice(Row, Base):
    __tablename__ = "model_prices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "model_id", "revision"),
        ForeignKeyConstraint(["tenant_id", "model_id"], ["model_configs.tenant_id", "model_configs.id"]),
        CheckConstraint("input_price >= 0 AND output_price >= 0"),
    )
    model_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    input_price: Mapped[int] = mapped_column(Integer)
    output_price: Mapped[int] = mapped_column(Integer)


class ModelInvocation(Row, Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "operation_key"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "model_id"], ["model_configs.tenant_id", "model_configs.id"]),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["quota_accounts.tenant_id", "quota_accounts.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "reservation_id"],
            ["quota_reservations.tenant_id", "quota_reservations.account_id", "quota_reservations.id"],
        ),
        CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')"),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0 AND credits >= 0"),
        CheckConstraint(
            "feedback_rating IS NULL OR feedback_rating BETWEEN 1 AND 5", name="ck_feedback_rating"
        ),
    )
    user_id: Mapped[str] = mapped_column(String(36))
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    model_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36))
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    user_message_id: Mapped[str] = mapped_column(String(36))
    assistant_message_id: Mapped[str] = mapped_column(String(36))
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    safety_flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    feedback_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class KnowledgeBase(Row, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint("scope IN ('private', 'school')"),
    )
    owner_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(20), default="private")


class Document(Row, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "owner_id", "operation_key"),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"], ["knowledge_bases.tenant_id", "knowledge_bases.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
        CheckConstraint(
            "(knowledge_base_id IS NOT NULL AND conversation_id IS NULL) OR (knowledge_base_id IS NULL AND conversation_id IS NOT NULL)"
        ),
        CheckConstraint(
            "status IN ('uploading', 'queued', 'scanning', 'parsing', 'parsed', 'ready', 'failed', 'quarantined', 'deleted')"
        ),
        CheckConstraint("size_bytes > 0 AND chunk_count >= 0"),
    )
    owner_id: Mapped[str] = mapped_column(String(36))
    knowledge_base_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(200))
    operation_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(Row, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "position"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
    )
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)


class IngestionJob(Row, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"),
    )
    document_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxEvent(Row, Base):
    __tablename__ = "outbox_events"
    kind: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(36))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmbeddingConfig(Row, Base):
    __tablename__ = "embedding_configs"
    __table_args__ = (UniqueConstraint("tenant_id"),)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    dimensions: Mapped[int] = mapped_column(Integer)
    request_dimensions: Mapped[bool] = mapped_column(Boolean, default=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, default=8192)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    min_score: Mapped[float] = mapped_column(Float, default=0.35)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class IndexJob(Row, Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"),
    )
    document_id: Mapped[str] = mapped_column(String(36))
    index_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ChunkVector(Row, Base):
    __tablename__ = "chunk_vectors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "position", "index_key"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "position"],
            ["document_chunks.tenant_id", "document_chunks.document_id", "document_chunks.position"],
            ondelete="CASCADE",
        ),
        Index("ix_chunk_vectors_scope", "tenant_id", "index_key"),
    )
    document_id: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer)
    index_key: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list] = mapped_column(JSON().with_variant(VECTOR(), "postgresql"))


class EmbeddingUsage(Row, Base):
    __tablename__ = "embedding_usage"
    __table_args__ = (CheckConstraint("status IN ('running', 'completed', 'failed', 'unknown')"),)
    purpose: Mapped[str] = mapped_column(String(20))
    reference_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResourceTask(Row, Base):
    __tablename__ = "resource_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "user_id", "operation_key"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "account_id"], ["quota_accounts.tenant_id", "quota_accounts.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "account_id", "reservation_id"],
            ["quota_reservations.tenant_id", "quota_reservations.account_id", "quota_reservations.id"],
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_resource_status"
        ),
        CheckConstraint(
            "kind IN ('image', 'ppt', 'video', 'music', 'avatar', '3d')", name="ck_resource_kind"
        ),
        CheckConstraint("payer IN ('school', 'teacher')", name="ck_resource_payer"),
        CheckConstraint(
            "progress BETWEEN 0 AND 100 AND max_credits >= 0 AND credits >= 0 AND credits <= max_credits",
            name="ck_resource_amounts",
        ),
    )
    user_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36))
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSON)
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    payer: Mapped[str] = mapped_column(String(20))
    max_credits: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ResourceOutput(Row, Base):
    __tablename__ = "resource_outputs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "task_id"], ["resource_tasks.tenant_id", "resource_tasks.id"]),
        UniqueConstraint("tenant_id", "task_id", "attempt"),
    )
    task_id: Mapped[str] = mapped_column(String(36))
    attempt: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(120))
    object_key: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskEvent(Row, Base):
    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "task_id"], ["resource_tasks.tenant_id", "resource_tasks.id"]),
    )
    task_id: Mapped[str] = mapped_column(String(36))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    progress: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
