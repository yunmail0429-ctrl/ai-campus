from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.accounts import provision_user
from app.billing import adjust, finish, reserve
from app.errors import AppError
from app.models import AuditLog, QuotaAccount, QuotaTransaction
from app.repository import Repository
from app.schemas import CreateUserInput


def account_id(environment, username="teacher"):
    with environment["app"].state.sessions() as session:
        repo = Repository(session, environment["tenant_id"])
        return repo.require(
            QuotaAccount, QuotaAccount.user_id == environment["users"][(repo.tenant_id, username)]
        ).id


def perform(environment, fn, *args):
    with environment["app"].state.sessions.begin() as session:
        return fn(Repository(session, environment["tenant_id"]), *args)


def test_admin_allocates_once_and_teacher_sees_source_totals(environment, client):
    identity = account_id(environment)
    admin = environment["client"]("admin")
    path = f"/api/v1/admin/quota/accounts/{identity}/adjustments"
    body = {"amount": 120, "reason": "九月教学额度"}
    assert client.post(path, json=body, headers={"Idempotency-Key": "grant1"}).status_code == 403
    assert admin.post(path, json=body).status_code == 422
    first = admin.post(path, json=body, headers={"Idempotency-Key": "grant1"})
    assert first.status_code == 200
    assert (
        admin.post(path, json=body, headers={"Idempotency-Key": "grant1"}).json()["data"]
        == first.json()["data"]
    )
    assert (
        admin.post(path, json={**body, "amount": 121}, headers={"Idempotency-Key": "grant1"}).status_code
        == 409
    )
    assert (
        admin.post(path, json={**body, "amount": 0}, headers={"Idempotency-Key": "zero"}).status_code == 422
    )
    assert (
        admin.post(path, json={**body, "amount": 1.5}, headers={"Idempotency-Key": "fraction"}).status_code
        == 422
    )
    personal = client.get("/api/v1/me/quota").json()["data"]
    assert personal == {
        "balance": 120,
        "reserved": 0,
        "available": 120,
        "frozen": False,
        "totalAllocated": 120,
        "totalReclaimed": 0,
        "totalConsumed": 0,
    }
    records = client.get("/api/v1/me/quota/transactions").json()["data"]["items"]
    assert len(records) == 1
    assert records[0]["source"] == "学校分配"
    assert records[0]["reason"] == "九月教学额度"
    assert records[0]["createdAt"].endswith("+00:00")
    assert admin.get("/api/v1/admin/quota/summary").json()["data"]["balance"] == 120
    recent = admin.get("/api/v1/admin/quota/transactions").json()["data"]["items"]
    assert recent[0]["teacherName"] == "teacher"
    assert recent[0]["balanceDelta"] == 120
    assert recent[0]["source"] == "学校分配"
    assert client.get("/api/v1/admin/quota/transactions").status_code == 403
    with environment["app"].state.sessions() as session:
        repo = Repository(session, environment["tenant_id"])
        assert (
            len(list(session.scalars(repo.query(AuditLog).where(AuditLog.action == "quota.adjusted")))) == 1
        )


def test_reserve_settle_release_and_ledger_reconcile(environment, client):
    identity = account_id(environment)
    perform(environment, adjust, identity, 100, "grant", "学校分配")
    hold = perform(environment, reserve, identity, 70, "call-1", "教案生成")
    assert perform(environment, reserve, identity, 70, "call-1", "教案生成").id == hold.id
    with pytest.raises(AppError) as error:
        perform(environment, reserve, identity, 31, "call-2", "图片生成")
    assert error.value.code == "INSUFFICIENT_QUOTA"
    with pytest.raises(AppError) as error:
        perform(environment, finish, identity, hold.id, 71)
    assert error.value.code == "RESERVATION_EXCEEDED"
    assert client.get("/api/v1/me/quota").json()["data"]["reserved"] == 70
    perform(environment, finish, identity, hold.id, 40)
    perform(environment, finish, identity, hold.id, 40)
    with pytest.raises(AppError) as error:
        perform(environment, finish, identity, hold.id)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    failed = perform(environment, reserve, identity, 20, "call-2", "图片生成")
    perform(environment, finish, identity, failed.id)
    perform(environment, finish, identity, failed.id)
    perform(environment, adjust, identity, -10, "reclaim", "收回未使用额度")
    current = client.get("/api/v1/me/quota").json()["data"]
    assert current["available"] == current["balance"] == 50
    assert current["reserved"] == 0
    assert current["totalAllocated"] == 100
    assert current["totalConsumed"] == 40
    assert current["totalReclaimed"] == 10
    ledger = client.get("/api/v1/me/quota/transactions").json()["data"]["items"]
    assert len(ledger) == 6
    assert [row["kind"] for row in ledger] == [
        "adjustment",
        "release",
        "reserve",
        "settle",
        "reserve",
        "adjustment",
    ]
    assert sum(row["balanceDelta"] for row in ledger) == current["balance"]
    assert sum(row["reservedDelta"] for row in ledger) == current["reserved"]
    first = client.get("/api/v1/me/quota/transactions?limit=2").json()["data"]
    second = client.get(
        "/api/v1/me/quota/transactions", params={"limit": 2, "cursor": first["nextCursor"]}
    ).json()["data"]
    assert len({row["id"] for row in first["items"] + second["items"]}) == 4


def test_freeze_blocks_new_usage_but_allows_inflight_settlement(environment, client):
    identity = account_id(environment)
    perform(environment, adjust, identity, 100, "grant", "额度")
    hold = perform(environment, reserve, identity, 60, "pending", "备课")
    admin = environment["client"]("admin")
    path = f"/api/v1/admin/quota/accounts/{identity}"
    assert admin.patch(path, json={"frozen": True, "reason": "暂停调用"}).status_code == 200
    with pytest.raises(AppError) as error:
        perform(environment, reserve, identity, 1, "blocked", "备课")
    assert error.value.code == "QUOTA_FROZEN"
    with pytest.raises(AppError) as error:
        perform(environment, adjust, identity, -41, "reclaim", "收回")
    assert error.value.code == "INSUFFICIENT_QUOTA"
    perform(environment, finish, identity, hold.id, 50)
    assert client.get("/api/v1/me/quota").json()["data"]["balance"] == 50
    assert admin.patch(path, json={"frozen": False, "reason": "恢复调用"}).status_code == 200
    perform(environment, reserve, identity, 10, "new", "备课")


def test_quota_isolation_and_transaction_rollback(environment, client):
    identity = account_id(environment)
    peer = environment["client"]("peer")
    other = environment["client"]("teacher", "school-b")
    other_tenant = next(tenant for tenant, username in environment["users"] if username == "teacher" and tenant != environment["tenant_id"])
    with environment["app"].state.sessions.begin() as session:
        provision_user(Repository(session, other_tenant), CreateUserInput(
            username="other-admin", password=environment["password"], name="其他学校管理员",
            subject="数学", grade="高一", role="admin",
        ))
    other_admin = environment["client"]("other-admin", "school-b")
    perform(environment, adjust, identity, 100, "grant", "额度")
    assert peer.get("/api/v1/me/quota").json()["data"]["balance"] == 0
    assert other.get("/api/v1/me/quota/transactions").json()["data"]["items"] == []
    assert other_admin.get("/api/v1/admin/quota/transactions").json()["data"]["items"] == []
    assert peer.get(f"/api/v1/admin/quota/accounts/{identity}/transactions").status_code == 403
    with environment["app"].state.sessions.begin() as session:
        repo = Repository(session, "other-tenant")
        with pytest.raises(AppError) as error:
            reserve(repo, identity, 10, "cross", "越权")
        assert error.value.status == 404
    with pytest.raises(RuntimeError), environment["app"].state.sessions.begin() as session:
        repo = Repository(session, environment["tenant_id"])
        reserve(repo, identity, 50, "rolled-back", "备课")
        raise RuntimeError("Simulate failure before commit")
    assert client.get("/api/v1/me/quota").json()["data"]["reserved"] == 0
    assert len(client.get("/api/v1/me/quota/transactions").json()["data"]["items"]) == 1
    assert client.delete("/api/v1/me/quota/transactions").status_code == 405


def test_concurrent_reservations_cannot_overspend(environment, client):
    identity = account_id(environment)
    perform(environment, adjust, identity, 100, "grant", "额度")
    barrier = Barrier(4)

    def attempt(index):
        barrier.wait(timeout=10)
        try:
            return perform(environment, reserve, identity, 60, f"call-{index}", "备课").id
        except AppError as error:
            assert error.code == "INSUFFICIENT_QUOTA"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert len([result for result in results if result]) == 1
    assert client.get("/api/v1/me/quota").json()["data"]["available"] == 40


def test_concurrent_settlement_is_charged_once(environment, client):
    identity = account_id(environment)
    perform(environment, adjust, identity, 100, "grant", "额度")
    hold = perform(environment, reserve, identity, 80, "call", "备课")
    barrier = Barrier(4)

    def attempt(_):
        barrier.wait(timeout=10)
        return perform(environment, finish, identity, hold.id, 30).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(attempt, range(4)))) == 1
    current = client.get("/api/v1/me/quota").json()["data"]
    assert current["balance"] == 70
    assert current["reserved"] == 0
    assert current["totalConsumed"] == 30
    with environment["app"].state.sessions() as session:
        repo = Repository(session, environment["tenant_id"])
        assert (
            len(list(session.scalars(repo.query(QuotaTransaction).where(QuotaTransaction.kind == "settle"))))
            == 1
        )


def test_ledger_order_with_identical_clock_readings(environment, client, monkeypatch):
    from app.models import now

    fixed = now()
    monkeypatch.setattr("app.billing.now", lambda: fixed)
    identity = account_id(environment)
    perform(environment, adjust, identity, 100, "grant", "分配")
    hold = perform(environment, reserve, identity, 20, "call", "备课")
    perform(environment, finish, identity, hold.id, 10)
    ledger = client.get("/api/v1/me/quota/transactions").json()["data"]["items"]
    assert [row["kind"] for row in ledger] == ["settle", "reserve", "adjustment"]
    assert len({row["createdAt"] for row in ledger}) == 3


def test_concurrent_allocation_replays_do_not_duplicate_credit(environment, client):
    identity = account_id(environment)
    barrier = Barrier(4)

    def attempt(_):
        barrier.wait(timeout=10)
        return perform(environment, adjust, identity, 100, "same-grant", "学校分配").id

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(attempt, range(4)))) == 1
    current = client.get("/api/v1/me/quota").json()["data"]
    assert current["balance"] == current["totalAllocated"] == 100


def test_ledger_pagination_with_new_credit_and_foreign_cursor(environment, client):
    identity = account_id(environment)
    for index in range(5):
        perform(environment, adjust, identity, 10, f"page-grant-{index}", "school allocation")
    path = "/api/v1/me/quota/transactions"
    first = client.get(path, params={"limit": 2}).json()["data"]
    original = client.get(path).json()["data"]["items"]
    perform(environment, adjust, identity, 7, "new-between-pages", "school allocation")
    rows = list(first["items"])
    cursor = first["nextCursor"]
    while cursor:
        response = client.get(path, params={"limit": 2, "cursor": cursor})
        assert response.status_code == 200
        page = response.json()["data"]
        rows.extend(page["items"])
        cursor = page["nextCursor"]
        assert len(rows) <= 5
    assert [row["id"] for row in rows] == [row["id"] for row in original]
    assert sum(row["balanceDelta"] for row in rows) == 50
    assert client.get("/api/v1/me/quota").json()["data"]["totalAllocated"] == 57
    for other in (environment["client"]("peer"), environment["client"]("teacher", "school-b")):
        response = other.get(path, params={"cursor": first["nextCursor"]})
        assert response.status_code == 200
        assert response.json()["data"]["items"] == []
    fresh = client.get(path).json()["data"]["items"]
    assert len(fresh) == 6
    assert sum(row["balanceDelta"] for row in fresh) == 57
