from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import func, or_

from .auth import ADMIN
from .billing import account_output, adjust, lock_account, transaction_output
from .errors import Envelope, Page, ok
from .models import AuditLog, QuotaAccount, QuotaTransaction, User
from .schemas import (
    AdminQuotaOutput,
    QuotaAdjustmentInput,
    QuotaStateInput,
    QuotaSummaryOutput,
    QuotaTransactionOutput,
)

router = APIRouter(prefix="/api/v1/admin/quota", tags=["quota"])


def admin_output(account, user):
    return {
        "id": account.id,
        "userId": user.id,
        "name": user.name,
        "username": user.username,
        "active": user.active,
        **account_output(account),
    }


@router.get("/summary", response_model=Envelope[QuotaSummaryOutput])
def summary(request: Request, ctx: ADMIN):
    # One statement gives a consistent view of totals even during concurrent billing.
    balance, reserved, count = ctx.repo.session.execute(
        ctx.repo.query(QuotaAccount).with_only_columns(
            func.coalesce(func.sum(QuotaAccount.balance), 0),
            func.coalesce(func.sum(QuotaAccount.reserved), 0),
            func.count(QuotaAccount.id),
        )
    ).one()
    consumed = ctx.repo.session.scalar(
        ctx.repo.query(QuotaTransaction)
        .where(QuotaTransaction.kind == "settle")
        .with_only_columns(func.coalesce(-func.sum(QuotaTransaction.balance_delta), 0))
    )
    return ok(
        request,
        {
            "accounts": count,
            "balance": balance,
            "reserved": reserved,
            "available": balance - reserved,
            "consumed": consumed,
        },
    )


@router.get("/accounts", response_model=Envelope[Page[AdminQuotaOutput]])
def accounts(
    request: Request,
    ctx: ADMIN,
    q: str = Query("", max_length=80),
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    users = ctx.repo.query(User).with_only_columns(User.id)
    if q.strip():
        users = users.where(
            or_(
                User.name.contains(q.strip(), autoescape=True),
                User.username.contains(q.strip(), autoescape=True),
            )
        )
    rows, next_cursor = ctx.repo.page(
        QuotaAccount,
        (QuotaAccount.user_id.in_(users),),
        limit=limit,
        cursor=cursor,
    )
    owners = {
        u.id: u
        for u in ctx.repo.session.scalars(
            ctx.repo.query(User).where(User.id.in_([row.user_id for row in rows]))
        )
    }
    return ok(
        request,
        {
            "items": [admin_output(row, owners[row.user_id]) for row in rows],
            "nextCursor": next_cursor,
        },
    )


@router.post("/accounts/{account_id}/adjustments", response_model=Envelope[QuotaTransactionOutput])
def adjustment(
    account_id: str,
    data: QuotaAdjustmentInput,
    request: Request,
    ctx: ADMIN,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    row = adjust(ctx.repo, account_id, data.amount, idempotency_key, data.reason)
    if not ctx.repo.one(AuditLog, AuditLog.action == "quota.adjusted", AuditLog.target_id == row.id):
        ctx.repo.audit(
            ctx.user.id,
            "quota.adjusted",
            row.id,
            request.state.request_id,
            {
                "accountId": account_id,
                "amount": data.amount,
                "reason": data.reason,
            },
        )
    return ok(request, transaction_output(row))


@router.patch("/accounts/{account_id}", response_model=Envelope[AdminQuotaOutput])
def set_state(account_id: str, data: QuotaStateInput, request: Request, ctx: ADMIN):
    account = lock_account(ctx.repo, account_id)
    if account.frozen != data.frozen:
        account.frozen = data.frozen
        ctx.repo.audit(
            ctx.user.id,
            "quota.frozen" if data.frozen else "quota.unfrozen",
            account.id,
            request.state.request_id,
            {"reason": data.reason},
        )
    return ok(request, admin_output(account, ctx.repo.require(User, User.id == account.user_id)))


@router.get("/accounts/{account_id}/transactions", response_model=Envelope[Page[QuotaTransactionOutput]])
def transactions(
    account_id: str,
    request: Request,
    ctx: ADMIN,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    ctx.repo.require(QuotaAccount, QuotaAccount.id == account_id)
    rows, next_cursor = ctx.repo.page(
        QuotaTransaction,
        (QuotaTransaction.account_id == account_id,),
        limit=limit,
        cursor=cursor,
    )
    return ok(request, {"items": [transaction_output(row) for row in rows], "nextCursor": next_cursor})
