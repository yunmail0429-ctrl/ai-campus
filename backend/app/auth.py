import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import db_session
from .errors import AppError, Envelope, ok
from .models import AuthSession, LoginAttempt, Role, Tenant, User, UserAvatar, UserRole, now
from .repository import Repository
from .schemas import LoginInput, UserOutput

hasher = PasswordHasher()
dummy_hash = hasher.hash(secrets.token_urlsafe(32))
router = APIRouter(prefix="/api/v1", tags=["identity"])
DB = Annotated[Session, Depends(db_session, scope="function")]


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(encoded: str, password: str):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def tenant_for(request, db):
    # Authentication bootstrap exception: the slug comes from deployment configuration, never a body/header.
    tenant = db.scalar(
        select(Tenant).where(Tenant.slug == request.app.state.settings.tenant_slug, Tenant.active.is_(True))
    )
    if not tenant:
        raise AppError(503, "TENANT_NOT_INITIALIZED", "学校尚未初始化，请联系管理员。")
    return tenant


def roles_for(repo: Repository, user_id):
    return list(
        repo.session.scalars(
            repo.query(Role)
            .join(UserRole, (Role.id == UserRole.role_id) & (Role.tenant_id == UserRole.tenant_id))
            .where(UserRole.user_id == user_id, UserRole.tenant_id == repo.tenant_id)
        ).all()
    )


def user_output(repo, user):
    avatar = repo.one(UserAvatar, UserAvatar.user_id == user.id)
    return UserOutput(
        id=user.id,
        username=user.username,
        name=user.name,
        subject=user.subject,
        grade=user.grade,
        roles=[r.name for r in roles_for(repo, user.id)],
        active=user.active,
        avatarUrl=(
            f"/api/v1/users/{user.id}/avatar?v={hashlib.sha256(avatar.image).hexdigest()[:12]}"
            if avatar else None
        ),
    )


@dataclass
class Context:
    repo: Repository
    user: User
    session: AuthSession
    roles: list[str]
    settings: object


def current_context(request: Request, db: DB):
    tenant = tenant_for(request, db)
    repo = Repository(db, tenant.id)
    token = request.cookies.get(request.app.state.settings.cookie_name, "")
    if not token or len(token) > 200:
        raise AppError(401, "UNAUTHENTICATED", "请先登录。")
    session = repo.one(
        AuthSession, AuthSession.token_hash == hash_token(token), AuthSession.revoked_at.is_(None)
    )
    expires = session.expires_at if session else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if not session or expires <= now():
        raise AppError(401, "SESSION_EXPIRED", "登录已过期，请重新登录。")
    user = repo.one(User, User.id == session.user_id, User.active.is_(True))
    if not user:
        raise AppError(401, "ACCOUNT_DISABLED", "账号已停用，请联系管理员。")
    return Context(
        repo, user, session, [role.name for role in roles_for(repo, user.id)], request.app.state.settings
    )


CTX = Annotated[Context, Depends(current_context, scope="function")]


def require_admin(context: CTX):
    if "admin" not in context.roles:
        raise AppError(403, "FORBIDDEN", "此操作需要学校管理员权限。")
    return context


ADMIN = Annotated[Context, Depends(require_admin, scope="function")]


@router.post("/auth/login", response_model=Envelope[UserOutput])
def login(data: LoginInput, request: Request, response: Response, db: DB):
    settings = request.app.state.settings
    tenant = tenant_for(request, db)
    repo = Repository(db, tenant.id)
    # Row lock serializes attempts across API workers when running on PostgreSQL.
    repo.require(Tenant, Tenant.id == tenant.id, lock=True)
    key = hash_token(f"{tenant.id}:{data.username.lower()}")
    since = now() - timedelta(seconds=settings.login_window_seconds)
    count = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.tenant_id == tenant.id, LoginAttempt.key_hash == key, LoginAttempt.created_at > since
        )
    )
    if count >= settings.login_attempts:
        raise AppError(429, "LOGIN_RATE_LIMITED", "尝试次数过多，请稍后再试。")
    repo.add(LoginAttempt, key_hash=key)
    user = repo.one(User, User.username == data.username.lower(), lock=True)
    valid = verify_password(user.password_hash if user else dummy_hash, data.password)
    if not valid or not user or not user.active:
        repo.audit(None, "auth.login_failed", None, request.state.request_id)
        db.commit()  # failed attempts must survive the exception's transaction rollback
        raise AppError(401, "INVALID_CREDENTIALS", "账号或密码错误。")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(data.password)
    old_token = request.cookies.get(settings.cookie_name)
    if old_token:
        old = repo.one(AuthSession, AuthSession.token_hash == hash_token(old_token))
        if old:
            old.revoked_at = now()
    token = secrets.token_urlsafe(32)
    repo.add(
        AuthSession,
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now() + timedelta(hours=settings.session_hours),
    )
    repo.audit(user.id, "auth.login", user.id, request.state.request_id)
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return ok(request, user_output(repo, user))


@router.post("/auth/logout", response_model=Envelope[None])
def logout(request: Request, response: Response, context: CTX):
    context.session.revoked_at = now()
    context.repo.audit(context.user.id, "auth.logout", context.user.id, request.state.request_id)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return ok(request)


@router.get("/me", response_model=Envelope[UserOutput])
def me(request: Request, context: CTX):
    return ok(request, user_output(context.repo, context.user))
