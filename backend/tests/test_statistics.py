# ruff: noqa: F811
import csv
import io
from datetime import UTC, datetime

from test_model_runtime import enable_chat, provider  # noqa: F401

from app.models import AuditLog, ModelInvocation, QuotaAccount, QuotaTransaction, ResourceTask, TaskEvent
from app.repository import Repository


def test_statistics_permissions_bounds_and_empty_range(environment):
    env = environment
    teacher, admin = env["client"](), env["client"]("admin")
    for path in ("/api/v1/admin/statistics", "/api/v1/admin/statistics/export"):
        assert teacher.get(path).status_code == 403
        for query in (
            "start=2026-02-01&end=2026-01-01",
            "start=2024-01-01&end=2026-01-01",
            "end=0001-01-01",
            "end=9999-12-31",
            "start=wrong",
        ):
            assert admin.get(path + "?" + query).status_code == 422
    data = admin.get("/api/v1/admin/statistics?start=2020-01-01&end=2020-01-02").json()["data"]
    assert len(data["daily"]) == 2
    assert data["summary"]["calls"] == data["summary"]["activeAccounts"] == 0
    assert data["summary"]["successRate"] is None and data["summary"]["resourceOutputs"] == 0
    assert data["resourceOutputs"] == dict.fromkeys(("image", "ppt", "video", "music", "avatar", "3d"), 0)


def test_statistics_generation_replay_settlement_dates_and_private_export(environment, provider):
    env = environment
    enable_chat(env)
    teacher, admin = env["client"](), env["client"]("admin")
    chat = teacher.post("/api/v1/conversations", json={"title": "私人教学内容"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{chat}/messages"
    for _ in range(2):
        result = teacher.post(
            path,
            json={"content": "私人教学内容", "useKnowledge": False},
            headers={"Idempotency-Key": "stats-one"},
        )
        assert "event: message.completed" in result.text
    tenant_b = next(tenant for tenant, _ in env["users"] if tenant != env["tenant_id"])
    with env["app"].state.sessions.begin() as session:
        repo = Repository(session, env["tenant_id"])
        invocation = repo.require(ModelInvocation)
        invocation.created_at = datetime(2026, 1, 1, 16, tzinfo=UTC)  # Jan 2, Beijing
        invocation.config_snapshot = {**invocation.config_snapshot, "skillId": "test-skill"}
        repo.require(QuotaTransaction, QuotaTransaction.kind == "settle").created_at = datetime(
            2026, 1, 2, 16, tzinfo=UTC
        )  # Jan 3
        for hour in (15, 16, 17):
            repo.add(
                AuditLog,
                actor_id=env["users"][(env["tenant_id"], "teacher")],
                action="auth.login",
                request_id="test",
                created_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
            )
        other = Repository(session, tenant_b)
        other.add(
            AuditLog,
            actor_id=env["users"][(tenant_b, "teacher")],
            action="auth.login",
            request_id="other-school",
            created_at=datetime(2026, 1, 1, 18, tzinfo=UTC),
        )
    url = "/api/v1/admin/statistics?start=2026-01-02&end=2026-01-03"
    response = admin.get(url)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["summary"]["calls"] == data["summary"]["skillCalls"] == 1
    assert data["summary"]["activeAccounts"] == data["summary"]["consumedCredits"] == 1
    assert data["summary"]["successRate"] == 1
    assert data["daily"][0]["calls"] == 1 and data["daily"][0]["consumedCredits"] == 0
    assert data["daily"][1]["calls"] == 0 and data["daily"][1]["consumedCredits"] == 1
    exported = admin.get(url.replace("statistics?", "statistics/export?"))
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    assert (
        "私人教学内容" not in exported.text
        and "teacher" not in exported.text
        and "test-skill" not in exported.text
    )
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == 3 and rows[1][0] == "2026-01-02" and rows[2][10] == "1"
    assert "statistics.exported" in admin.get("/api/v1/admin/audit-logs").text


def test_failed_generation_does_not_report_confirmed_usage_or_credit_consumption(environment, provider):
    env = environment
    enable_chat(env, model="missing-usage")
    teacher, admin = env["client"](), env["client"]("admin")
    chat = teacher.post("/api/v1/conversations", json={"title": "失败统计"}).json()["data"]["id"]
    result = teacher.post(
        f"/api/v1/conversations/{chat}/messages",
        json={"content": "课堂活动", "useKnowledge": False},
        headers={"Idempotency-Key": "failed-stat"},
    )
    assert "event: error" in result.text
    data = admin.get("/api/v1/admin/statistics").json()["data"]["summary"]
    assert data["calls"] == data["failed"] == 1
    assert data["completed"] == data["consumedCredits"] == data["inputTokens"] == data["outputTokens"] == 0
    assert data["successRate"] == 0


def test_resource_counts_use_first_completion_school_scope_and_numeric_export(environment):
    env = environment
    admin = env["client"]("admin")
    with env["app"].state.sessions.begin() as session:
        for tenant in {tenant for tenant, _ in env["users"]}:
            repo = Repository(session, tenant)
            user = env["users"][(tenant, "teacher")]
            account = repo.require(QuotaAccount, QuotaAccount.user_id == user)
            for index, kind in enumerate(("image", "ppt", "video", "music", "avatar", "3d", "ppt")):
                task = repo.add(
                    ResourceTask,
                    user_id=user,
                    account_id=account.id,
                    operation_key=f"statistics-{index}",
                    request_hash="0" * 64,
                    kind=kind,
                    prompt="私人创作内容",
                    options={},
                    config_snapshot={},
                    payer="teacher",
                    max_credits=0,
                    status="succeeded" if index < 6 else "failed",
                    deadline_at=datetime(2026, 1, 3, tzinfo=UTC),
                    created_at=datetime(2025, 12, 31, tzinfo=UTC),
                )
                # Jan 1 23:59 is outside; Jan 2 00:00 is included.
                stamp = (
                    datetime(2026, 1, 1, 15, 59, tzinfo=UTC)
                    if index == 0
                    else datetime(2026, 1, 1, 16, tzinfo=UTC)
                )
                repo.add(
                    TaskEvent, task_id=task.id, attempt=1, status=task.status, progress=100, created_at=stamp
                )
                # Duplicate delivery on a later day must not create/move a success.
                repo.add(
                    TaskEvent,
                    task_id=task.id,
                    attempt=1,
                    status=task.status,
                    progress=100,
                    created_at=datetime(2026, 1, 2, 16, tzinfo=UTC),
                )
    url = "/api/v1/admin/statistics?start=2026-01-02&end=2026-01-03"
    data = admin.get(url).json()["data"]
    assert data["summary"]["resourceOutputs"] == 5
    assert data["resourceOutputs"] == {"image": 0, "ppt": 1, "video": 1, "music": 1, "avatar": 1, "3d": 1}
    assert [row["resourceOutputs"] for row in data["daily"]] == [5, 0]
    assert data["summary"]["calls"] == 0  # Media tasks are not text invocations.
    csv_response = admin.get(url.replace("statistics?", "statistics/export?"))
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0]["资源产出"] == "5" and rows[0]["PPT产出"] == "1"
    assert rows[1]["资源产出"] == "0"
    assert "私人创作内容" not in csv_response.text


def test_statistics_midnight_exclusion_and_distinct_accounts_across_days(environment):
    env = environment
    admin = env["client"]("admin")
    with env["app"].state.sessions.begin() as session:
        repo = Repository(session, env["tenant_id"])
        user = env["users"][(env["tenant_id"], "teacher")]
        for index, stamp in enumerate((
            datetime(2026, 2, 28, 15, 59, 59, tzinfo=UTC),
            datetime(2026, 2, 28, 16, tzinfo=UTC),
            datetime(2026, 3, 1, 15, 59, 59, tzinfo=UTC),
            datetime(2026, 3, 1, 16, tzinfo=UTC),
            datetime(2026, 3, 2, 16, tzinfo=UTC),
        )):
            repo.add(AuditLog, actor_id=user, action="auth.login",
                     request_id=f"boundary-{index}", created_at=stamp)
        repo.add(AuditLog, actor_id=env["users"][(env["tenant_id"], "peer")],
                 action="auth.login", request_id="outside-range",
                 created_at=datetime(2026, 3, 2, 16, tzinfo=UTC))
    params = {"start": "2026-03-01", "end": "2026-03-02"}
    data = admin.get("/api/v1/admin/statistics", params=params).json()["data"]
    assert [row["logins"] for row in data["daily"]] == [1, 1]
    assert data["summary"]["activeAccounts"] == 1
    response = admin.get("/api/v1/admin/statistics/export", params=params)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [(row[0], row[1]) for row in rows[1:]] == [
        ("2026-03-01", "1"), ("2026-03-02", "1")]
