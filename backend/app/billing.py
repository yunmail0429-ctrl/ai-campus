"""Transactional quota primitives. Caller owns the transaction and trusted tenant.

Only trusted server workflows can change credit amounts. No public mutation route
accepts client-reported usage. Account locks serialize idempotency and mutations;
the caller must not hold these locks while calling external model providers.
"""

from datetime import datetime, timedelta

from sqlalchemy import case, func, update

from .errors import AppError
from .models import QuotaAccount, QuotaReservation, QuotaTransaction, iso_utc, now

MAX_CREDITS = 2_000_000_000


def usage_totals(repo, account_id=None):
    query = repo.query(QuotaTransaction)
    if account_id is not None:
        query = query.where(QuotaTransaction.account_id == account_id)
    row = repo.session.execute(
        query.with_only_columns(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (QuotaTransaction.kind == "adjustment") & (QuotaTransaction.balance_delta > 0),
                            QuotaTransaction.balance_delta,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (QuotaTransaction.kind == "adjustment") & (QuotaTransaction.balance_delta < 0),
                            -QuotaTransaction.balance_delta,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            QuotaTransaction.kind == "settle",
                            -QuotaTransaction.balance_delta,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
    ).one()
    return {"totalAllocated": row[0], "totalReclaimed": row[1], "totalConsumed": row[2]}


def transaction_output(row):
    source = {
        "adjustment": "学校分配" if row.balance_delta > 0 else "学校收回",
        "reserve": "AI 任务预占",
        "settle": "AI 使用结算",
        "release": "任务取消或失败释放",
    }[row.kind]
    if row.kind == "adjustment" and row.id == row.account_id:
        source = "历史余额迁移"
    return {
        "source": source,
        "id": row.id,
        "kind": row.kind,
        "balanceDelta": row.balance_delta,
        "reservedDelta": row.reserved_delta,
        "balanceAfter": row.balance_after,
        "reservedAfter": row.reserved_after,
        "reason": row.reason,
        "createdAt": iso_utc(row.created_at),
    }


def account_output(account):
    return {
        "balance": account.balance,
        "reserved": account.reserved,
        "available": account.balance - account.reserved,
        "frozen": account.frozen,
    }


def lock_account(repo, account_id):
    if repo.session.get_bind().dialect.name == "sqlite":
        # SQLite has no SELECT FOR UPDATE; take the write lock before reading.
        repo.session.execute(
            update(QuotaAccount)
            .where(QuotaAccount.tenant_id == repo.tenant_id, QuotaAccount.id == account_id)
            .values(balance=QuotaAccount.balance)
            .execution_options(synchronize_session=False)
        )
    return repo.require(QuotaAccount, QuotaAccount.id == account_id, lock=True)


def validate(amount, key, reason, *, signed=False):
    if type(amount) is not int or abs(amount) > MAX_CREDITS or (not signed and amount <= 0) or amount == 0:
        raise AppError(422, "INVALID_CREDIT_AMOUNT", "积分必须为有效范围内的整数。")
    if not isinstance(key, str) or not key.strip() or len(key) > 100:
        raise AppError(422, "INVALID_OPERATION_KEY", "积分操作标识无效。")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise AppError(422, "INVALID_QUOTA_REASON", "请填写积分变更原因。")


def conflict():
    raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一积分操作标识不能用于不同的请求。")


def record(repo, account, kind, key, balance_delta, reserved_delta, reason, reservation_id=None):
    timestamp = now()
    latest = repo.session.scalar(
        repo.query(QuotaTransaction)
        .where(QuotaTransaction.account_id == account.id)
        .with_only_columns(QuotaTransaction.created_at)
        .order_by(QuotaTransaction.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        timestamp = max(timestamp, datetime.fromisoformat(iso_utc(latest)) + timedelta(microseconds=1))
    account.balance += balance_delta
    account.reserved += reserved_delta
    return repo.add(
        QuotaTransaction,
        account_id=account.id,
        reservation_id=reservation_id,
        kind=kind,
        operation_key=key,
        balance_delta=balance_delta,
        reserved_delta=reserved_delta,
        balance_after=account.balance,
        reserved_after=account.reserved,
        reason=reason,
        created_at=timestamp,
    )


def adjust(repo, account_id, amount, operation_key, reason):
    """Internal allocation/refund primitive; authorization belongs to its caller."""
    validate(amount, operation_key, reason, signed=True)
    account = lock_account(repo, account_id)
    previous = repo.one(
        QuotaTransaction,
        QuotaTransaction.account_id == account.id,
        QuotaTransaction.kind == "adjustment",
        QuotaTransaction.operation_key == operation_key,
    )
    if previous:
        if previous.balance_delta != amount or previous.reason != reason:
            conflict()
        return previous
    if account.balance + amount < account.reserved:
        raise AppError(409, "INSUFFICIENT_QUOTA", "可用积分不足，不能收回已预占的积分。")
    if account.balance + amount > MAX_CREDITS:
        raise AppError(422, "QUOTA_LIMIT_EXCEEDED", "积分余额超出支持范围。")
    return record(repo, account, "adjustment", operation_key, amount, 0, reason)


def reserve(repo, account_id, amount, operation_key, reason):
    validate(amount, operation_key, reason)
    account = lock_account(repo, account_id)
    previous = repo.one(
        QuotaReservation,
        QuotaReservation.account_id == account.id,
        QuotaReservation.operation_key == operation_key,
        lock=True,
    )
    if previous:
        if previous.amount != amount or previous.reason != reason:
            conflict()
        return previous
    if account.frozen:
        raise AppError(409, "QUOTA_FROZEN", "积分账户已冻结，请联系管理员。")
    if account.balance - account.reserved < amount:
        raise AppError(409, "INSUFFICIENT_QUOTA", "可用积分不足，请联系管理员。")
    reservation = repo.add(
        QuotaReservation,
        account_id=account.id,
        operation_key=operation_key,
        amount=amount,
        reason=reason,
    )
    record(repo, account, "reserve", operation_key, 0, amount, reason, reservation.id)
    return reservation


def finish(repo, account_id, reservation_id, actual_amount=None):
    """None releases a failed/cancelled call; a nonnegative amount settles it."""
    if actual_amount is not None and (type(actual_amount) is not int or actual_amount < 0):
        raise AppError(422, "INVALID_CREDIT_AMOUNT", "结算积分必须为非负整数。")
    account = lock_account(repo, account_id)
    reservation = repo.require(
        QuotaReservation,
        QuotaReservation.account_id == account.id,
        QuotaReservation.id == reservation_id,
        lock=True,
    )
    status = "released" if actual_amount is None else "settled"
    if reservation.status != "reserved":
        if reservation.status != status or reservation.settled_amount != (actual_amount or 0):
            conflict()
        return reservation
    if actual_amount is not None and actual_amount > reservation.amount:
        raise AppError(409, "RESERVATION_EXCEEDED", "实际消耗超过预占上限，需核查模型计量。")
    reservation.status = status
    reservation.settled_amount = actual_amount or 0
    record(
        repo,
        account,
        "release" if actual_amount is None else "settle",
        reservation.operation_key,
        -(actual_amount or 0),
        -reservation.amount,
        reservation.reason,
        reservation.id,
    )
    return reservation
