from fastapi import APIRouter, Query, Request

from .auth import ADMIN, CTX, hasher, user_output, verify_password
from .billing import account_output, transaction_output, usage_totals
from .errors import AppError, Envelope, Page, ok
from .models import (
    AuditLog,
    AuthSession,
    Preferences,
    QuotaAccount,
    QuotaTransaction,
    Role,
    Skill,
    User,
    UserRole,
    Workspace,
    iso_utc,
    now,
)
from .schemas import (
    CreateUserInput,
    PasswordInput,
    PersonalQuotaOutput,
    PreferencesInput,
    ProfileInput,
    QuotaTransactionOutput,
    UserOutput,
    UserStateInput,
)

router = APIRouter(prefix="/api/v1", tags=["accounts"])


def provision_user(repo, data: CreateUserInput):
    username = data.username.lower()
    if repo.one(User, User.username == username):
        raise AppError(409, "USERNAME_EXISTS", "该账号已存在。")
    role = repo.require(Role, Role.name == data.role)
    user = repo.add(
        User,
        username=username,
        password_hash=hasher.hash(data.password),
        name=data.name,
        subject=data.subject,
        grade=data.grade,
    )
    repo.add(UserRole, user_id=user.id, role_id=role.id)
    repo.add(Preferences, user_id=user.id, data=PreferencesInput().model_dump())
    repo.add(Workspace, user_id=user.id)
    repo.add(QuotaAccount, user_id=user.id, balance=0)
    return user


@router.patch("/me", response_model=Envelope[UserOutput])
def update_profile(data: ProfileInput, request: Request, ctx: CTX):
    for key, value in data.model_dump().items():
        setattr(ctx.user, key, value)
    ctx.repo.audit(ctx.user.id, "profile.updated", ctx.user.id, request.state.request_id)
    return ok(request, user_output(ctx.repo, ctx.user))


@router.get("/me/preferences", response_model=Envelope[PreferencesInput])
def get_preferences(request: Request, ctx: CTX):
    row = ctx.repo.require(Preferences, Preferences.user_id == ctx.user.id)
    return ok(request, row.data)


@router.put("/me/preferences", response_model=Envelope[PreferencesInput])
def update_preferences(data: PreferencesInput, request: Request, ctx: CTX):
    if data.skill:
        row = ctx.repo.require(Skill, Skill.id == data.skill)
        if row.owner_id != ctx.user.id and not (row.scope == "school" and row.status == "published"):
            raise AppError(404, "NOT_FOUND", "所选 Skill 不可用。")
    row = ctx.repo.require(Preferences, Preferences.user_id == ctx.user.id)
    row.data = data.model_dump()
    return ok(request, row.data)


@router.post("/me/password", response_model=Envelope[None])
def change_password(data: PasswordInput, request: Request, ctx: CTX):
    user = ctx.repo.require(User, User.id == ctx.user.id, lock=True)
    if not verify_password(user.password_hash, data.currentPassword):
        raise AppError(400, "INVALID_PASSWORD", "当前密码不正确。")
    user.password_hash = hasher.hash(data.newPassword)
    for session in ctx.repo.session.scalars(
        ctx.repo.query(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ):
        if session.id != ctx.session.id:
            session.revoked_at = now()
    ctx.repo.audit(user.id, "password.changed", user.id, request.state.request_id)
    return ok(request)


@router.get("/me/quota", response_model=Envelope[PersonalQuotaOutput])
def get_quota(request: Request, ctx: CTX):
    row = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    return ok(request, {**account_output(row), **usage_totals(ctx.repo, row.id)})


@router.get("/me/quota/transactions", response_model=Envelope[Page[QuotaTransactionOutput]])
def quota_transactions(
    request: Request,
    ctx: CTX,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    account = ctx.repo.require(QuotaAccount, QuotaAccount.user_id == ctx.user.id)
    rows, next_cursor = ctx.repo.page(
        QuotaTransaction,
        (QuotaTransaction.account_id == account.id,),
        limit=limit,
        cursor=cursor,
    )
    return ok(
        request,
        {
            "items": [transaction_output(row) for row in rows],
            "nextCursor": next_cursor,
        },
    )


@router.get("/admin/users", response_model=Envelope[Page[UserOutput]])
def list_users(
    request: Request,
    ctx: ADMIN,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(User, limit=limit, cursor=cursor)
    return ok(request, {"items": [user_output(ctx.repo, row) for row in rows], "nextCursor": next_cursor})


@router.post("/admin/users", response_model=Envelope[UserOutput], status_code=201)
def create_user(data: CreateUserInput, request: Request, ctx: ADMIN):
    row = provision_user(ctx.repo, data)
    ctx.repo.audit(ctx.user.id, "user.created", row.id, request.state.request_id, {"role": data.role})
    return ok(request, user_output(ctx.repo, row))


@router.patch("/admin/users/{user_id}", response_model=Envelope[UserOutput])
def set_user_state(user_id: str, data: UserStateInput, request: Request, ctx: ADMIN):
    if user_id == ctx.user.id and not data.active:
        raise AppError(409, "SELF_DISABLE_FORBIDDEN", "不能停用当前管理员账号。")
    user = ctx.repo.require(User, User.id == user_id, lock=True)
    user.active = data.active
    if not data.active:
        for session in ctx.repo.session.scalars(
            ctx.repo.query(AuthSession).where(AuthSession.user_id == user.id)
        ):
            session.revoked_at = now()
    ctx.repo.audit(
        ctx.user.id, "user.enabled" if data.active else "user.disabled", user.id, request.state.request_id
    )
    return ok(request, user_output(ctx.repo, user))


@router.get("/admin/audit-logs", response_model=Envelope[Page[dict]])
def audit_logs(
    request: Request,
    ctx: ADMIN,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    rows, next_cursor = ctx.repo.page(AuditLog, limit=limit, cursor=cursor)
    return ok(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "actorId": row.actor_id,
                    "action": row.action,
                    "targetId": row.target_id,
                    "requestId": row.request_id,
                    "createdAt": iso_utc(row.created_at),
                    "details": row.details,
                }
                for row in rows
            ],
            "nextCursor": next_cursor,
        },
    )
