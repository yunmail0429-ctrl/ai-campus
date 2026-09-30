# ruff: noqa: F811
from test_knowledge import knowledge  # noqa: F401

from app.models import OutboxEvent, ResourceOutput, ResourceTask
from app.repository import Repository
from app.resource_contracts import FACTORIES, ResourcePlan
from app.storage import ObjectStore


class PlanFixture:
    """Only tests API planning; never simulates a working production generator."""

    def prepare(self, data):
        assert "13812345678" not in data.prompt
        return ResourcePlan(snapshot={"adapter": "api-test"}, maxCredits=10)


def configured(env, monkeypatch, payer):
    monkeypatch.setitem(FACTORIES, "ppt", lambda _: PlanFixture())
    env["app"].state.settings.resource_payer = payer


def submit(client, key="resource-test", **changes):
    return client.post(
        "/api/v1/resource-tasks",
        headers={"Idempotency-Key": key},
        json={
            "kind": "ppt",
            "prompt": "函数课堂，联系13812345678",
            "options": {"pages": 12, "style": "简洁"},
            **changes,
        },
    )


def test_resources_unconfigured_do_not_create_or_charge(knowledge, monkeypatch):
    env = knowledge
    teacher = env["client"]()
    before = teacher.get("/api/v1/me/quota").json()["data"]
    capabilities = teacher.get("/api/v1/resource-tasks/capabilities").json()["data"]
    assert len(capabilities) == 6 and not any(row["available"] for row in capabilities)
    assert submit(teacher).json()["code"] == "RESOURCE_NOT_CONFIGURED"
    configured(env, monkeypatch, None)
    assert submit(teacher).json()["code"] == "RESOURCE_BILLING_NOT_CONFIGURED"
    assert teacher.get("/api/v1/resource-tasks").json()["data"]["items"] == []
    assert teacher.get("/api/v1/me/quota").json()["data"] == before


def test_resource_school_plan_idempotency_permissions_cancel_retry(knowledge, monkeypatch):
    env = knowledge
    configured(env, monkeypatch, "school")
    teacher = env["client"]()
    before = teacher.get("/api/v1/me/quota").json()["data"]
    result = submit(teacher)
    assert result.status_code == 201, result.text
    data = result.json()["data"]
    assert data["status"] == "queued" and data["maxCredits"] == 0
    assert "13812345678" not in result.text and "config_snapshot" not in result.text
    identity = data["id"]
    path = f"/api/v1/resource-tasks/{identity}"
    assert submit(teacher).json()["data"]["id"] == identity
    assert submit(teacher, prompt="不同内容").status_code == 409
    assert submit(teacher, key="wrong", kind="image").status_code == 422
    assert teacher.get(path + "/download").status_code == 409
    for other in (env["client"]("peer"), env["client"]("admin"), env["client"]("teacher", "school-b")):
        assert identity not in other.get("/api/v1/resource-tasks").text
        for suffix in ("", "/events", "/download"):
            assert other.get(path + suffix).status_code == 404
        assert other.post(path + "/cancel").status_code == 404
        assert other.post(path + "/retry", headers={"Idempotency-Key": "retry"}).status_code == 404
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert (
            len(list(session.scalars(repo.query(OutboxEvent).where(OutboxEvent.kind == "resource.step"))))
            == 1
        )
    assert teacher.post(path + "/cancel").json()["data"]["status"] == "cancelled"
    assert teacher.post(path + "/cancel").json()["data"]["status"] == "cancelled"
    events = teacher.get(path + "/events").json()["data"]["items"]
    assert len(events) == 2
    retry = teacher.post(path + "/retry", headers={"Idempotency-Key": "retry"})
    assert retry.status_code == 201 and retry.json()["data"]["id"] != identity
    assert (
        teacher.post(path + "/retry", headers={"Idempotency-Key": "retry"}).json()["data"]["id"]
        == retry.json()["data"]["id"]
    )
    assert teacher.get("/api/v1/me/quota").json()["data"] == before


def test_resource_teacher_reservation_cancel_release_and_owner_download(knowledge, monkeypatch):
    env = knowledge
    configured(env, monkeypatch, "teacher")
    teacher, admin = env["client"](), env["client"]("admin")
    assert submit(teacher).json()["code"] == "INSUFFICIENT_QUOTA"
    account = next(
        row
        for row in admin.get("/api/v1/admin/quota/accounts").json()["data"]["items"]
        if row["username"] == "teacher"
    )
    admin.post(
        f"/api/v1/admin/quota/accounts/{account['id']}/adjustments",
        headers={"Idempotency-Key": "resource-grant"},
        json={"amount": 30, "reason": "任务测试"},
    )
    task = submit(teacher).json()["data"]
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 10
    path = f"/api/v1/resource-tasks/{task['id']}"
    teacher.post(path + "/cancel")
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["reserved"] == quota["totalConsumed"] == 0 and quota["balance"] == 30
    # Seed a completed output solely to test the download gate; no generator is claimed.
    configured(env, monkeypatch, "school")
    completed = submit(teacher, key="download-fixture").json()["data"]
    with env["app"].state.sessions.begin() as session:
        repo = Repository(session, env["tenant_id"])
        row = repo.require(ResourceTask, ResourceTask.id == completed["id"])
        row.status, row.progress = "succeeded", 100
        repo.add(
            ResourceOutput,
            task_id=row.id,
            attempt=1,
            filename="结果.pptx",
            media_type="application/octet-stream",
            object_key="private-test-output",
            size=4,
            sha256="0" * 64,
            ready=True,
        )
    ObjectStore(env["app"].state.settings).put("private-test-output", b"test")
    response = teacher.get(f"/api/v1/resource-tasks/{completed['id']}/download", follow_redirects=False)
    assert response.status_code == 303 and "X-Amz-Signature=" in response.headers["location"]
    assert "resource.downloaded" in admin.get("/api/v1/admin/audit-logs").text
