import base64
import io
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, case, exists, or_, select

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
    UserAvatar,
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

AVATAR_UPLOAD_LIMIT = 5 * 1024 * 1024


@router.post("/me/avatar", response_model=Envelope[UserOutput])
def update_avatar(request: Request, ctx: CTX, file: UploadFile):
    content = file.file.read(AVATAR_UPLOAD_LIMIT + 1)
    if len(content) > AVATAR_UPLOAD_LIMIT:
        raise AppError(413, "FILE_TOO_LARGE", "头像图片不能超过 5 MB。")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ("JPEG", "PNG", "WEBP") or max(source.size) > 6000:
                raise ValueError("Unsupported avatar")
            image = ImageOps.fit(ImageOps.exif_transpose(source), (256, 256), method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise AppError(422, "INVALID_AVATAR", "请上传有效的 JPG、PNG 或 WebP 图片。") from None
    avatar = ctx.repo.one(UserAvatar, UserAvatar.user_id == ctx.user.id, lock=True)
    if avatar:
        avatar.image = output.getvalue()
        avatar.updated_at = now()
    else:
        avatar = ctx.repo.add(UserAvatar, user_id=ctx.user.id, image=output.getvalue())
    ctx.repo.audit(ctx.user.id, "avatar.updated", ctx.user.id, request.state.request_id)
    return ok(request, user_output(ctx.repo, ctx.user))


@router.delete("/me/avatar", response_model=Envelope[UserOutput])
def remove_avatar(request: Request, ctx: CTX):
    avatar = ctx.repo.one(UserAvatar, UserAvatar.user_id == ctx.user.id, lock=True)
    if avatar:
        ctx.repo.session.delete(avatar)
        ctx.repo.session.flush()
        ctx.repo.audit(ctx.user.id, "avatar.removed", ctx.user.id, request.state.request_id)
    return ok(request, user_output(ctx.repo, ctx.user))


@router.get("/users/{user_id}/avatar")
def get_avatar(user_id: str, ctx: CTX):
    if user_id != ctx.user.id and "admin" not in ctx.roles:
        raise AppError(403, "FORBIDDEN", "无权查看该头像。")
    ctx.repo.require(User, User.id == user_id, User.deleted_at.is_(None))
    avatar = ctx.repo.require(UserAvatar, UserAvatar.user_id == user_id)
    return Response(
        content=avatar.image,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


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
    admin_role = ctx.repo.require(Role, Role.name == "admin")
    is_admin = exists(select(UserRole.id).where(
        UserRole.tenant_id == ctx.repo.tenant_id,
        UserRole.user_id == User.id,
        UserRole.role_id == admin_role.id,
    ))
    rank = case((User.id == ctx.user.id, 0), (is_admin, 1), else_=2)
    statement = ctx.repo.query(User).where(User.deleted_at.is_(None))
    if cursor:
        try:
            cursor_rank, stamp, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            stamp = datetime.fromisoformat(stamp)
            if cursor_rank not in (0, 1, 2) or str(UUID(row_id)) != row_id:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise AppError(422, "INVALID_CURSOR", "分页标识无效。") from None
        statement = statement.where(or_(
            rank > cursor_rank,
            and_(rank == cursor_rank, or_(
                User.created_at < stamp,
                and_(User.created_at == stamp, User.id < row_id),
            )),
        ))
    rows = list(ctx.repo.session.scalars(
        statement.order_by(rank, User.created_at.desc(), User.id.desc()).limit(limit + 1)
    ))
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        last_rank = 0 if last.id == ctx.user.id else 1 if ctx.repo.one(
            UserRole, UserRole.user_id == last.id, UserRole.role_id == admin_role.id
        ) else 2
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [last_rank, last.created_at.isoformat(), last.id]
        ).encode()).decode()
    rows = rows[:limit]
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
    user = ctx.repo.require(User, User.id == user_id, User.deleted_at.is_(None), lock=True)
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


@router.delete("/admin/users/{user_id}", response_model=Envelope[None])
def delete_teacher(user_id: str, request: Request, ctx: ADMIN):
    if user_id == ctx.user.id:
        raise AppError(409, "SELF_DELETE_FORBIDDEN", "不能删除当前登录账号。")
    user = ctx.repo.require(User, User.id == user_id, User.deleted_at.is_(None), lock=True)
    admin_role = ctx.repo.require(Role, Role.name == "admin")
    if ctx.repo.one(UserRole, UserRole.user_id == user.id, UserRole.role_id == admin_role.id):
        raise AppError(403, "ADMIN_DELETE_FORBIDDEN", "只能删除教师账号。")
    user.active = False
    user.deleted_at = now()
    for session in ctx.repo.session.scalars(
        ctx.repo.query(AuthSession).where(AuthSession.user_id == user.id)
    ):
        session.revoked_at = now()
    ctx.repo.audit(ctx.user.id, "user.deleted", user.id, request.state.request_id)
    return ok(request)


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
