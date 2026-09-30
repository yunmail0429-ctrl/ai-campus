# ruff: noqa: F811
"""Protocol fixtures, not live vendor generation or quality acceptance tests."""

import io
from datetime import timedelta

import pytest
from pptx import Presentation
from test_knowledge import knowledge  # noqa: F401
from test_resource_tasks import submit

from app.errors import AppError
from app.models import OutboxEvent, ResourceOutput, ResourceTask, now
from app.repository import Repository
from app.resource_contracts import FACTORIES, ResourceArtifact, ResourcePlan, ResourceState
from app.resource_execution import cleanup_resource, process_resource, recover_resources
from app.storage import ObjectStore


class AdapterFixture:
    supports_start_recovery = True

    def __init__(self):
        self.starts, self.polls, self.cancels = 0, 0, 0
        output = io.BytesIO()
        Presentation().save(output)
        self.state = ResourceState(
            status="succeeded",
            credits=7,
            artifact=ResourceArtifact(
                content=output.getvalue(),
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        )

    def prepare(self, data):
        return ResourcePlan(snapshot={"adapter": "execution-test"}, maxCredits=10)

    async def start(self, data, snapshot, key):
        self.starts += 1
        return "external-fixture-job"

    async def poll(self, job_id, snapshot):
        assert job_id == "external-fixture-job"
        self.polls += 1
        return self.state

    async def cancel(self, job_id, snapshot, key):
        self.cancels += 1
        return True


@pytest.fixture
def execution(knowledge, monkeypatch):
    env = knowledge
    adapter = AdapterFixture()
    monkeypatch.setitem(FACTORIES, "ppt", lambda _: adapter)
    monkeypatch.setattr("app.resource_execution.scan", lambda *_: None)
    teacher, admin = env["client"](), env["client"]("admin")
    account = next(
        row
        for row in admin.get("/api/v1/admin/quota/accounts").json()["data"]["items"]
        if row["username"] == "teacher"
    )
    response = admin.post(
        f"/api/v1/admin/quota/accounts/{account['id']}/adjustments",
        headers={"Idempotency-Key": "worker-grant"},
        json={"amount": 30, "reason": "执行测试"},
    )
    assert response.status_code < 300
    response = submit(teacher)
    assert response.status_code == 201, response.text
    identity = response.json()["data"]["id"]
    path = f"/api/v1/resource-tasks/{identity}"

    def step():
        process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)

    return env, adapter, teacher, identity, path, step


def test_worker_progress_success_and_duplicate_do_not_charge_twice(execution):
    env, adapter, teacher, identity, path, step = execution
    step()
    assert teacher.get(path).json()["data"]["status"] == "running"
    final = adapter.state
    adapter.state = ResourceState(status="running", progress=60)
    step()
    assert teacher.get(path).json()["data"]["progress"] == 60
    adapter.state = final
    step()
    step()
    task = teacher.get(path).json()["data"]
    assert task["status"] == "succeeded" and task["credits"] == 7 and task["progress"] == 100
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["reserved"] == 0 and quota["balance"] == 23 and quota["totalConsumed"] == 7
    assert adapter.starts == 1 and adapter.polls == 2
    assert teacher.get(path + "/download", follow_redirects=False).status_code == 303
    report = env["client"]("admin").get("/api/v1/admin/statistics").json()["data"]
    assert report["summary"]["resourceOutputs"] == report["resourceOutputs"]["ppt"] == 1
    assert report["summary"]["consumedCredits"] == 7
    with env["app"].state.sessions() as session:
        result = Repository(session, env["tenant_id"]).require(
            ResourceOutput, ResourceOutput.task_id == identity
        )
        assert result.ready
        assert (
            ObjectStore(env["app"].state.settings).read(result.object_key, 1000000) == final.artifact.content
        )


def test_inline_generation_settles_once_and_never_uses_remote_start(execution, monkeypatch):
    _env, adapter, teacher, _identity, path, step = execution
    calls = []

    async def execute(data, snapshot):
        calls.append(data.kind)
        return adapter.state

    monkeypatch.setattr(adapter, "execute", execute, raising=False)
    step()
    step()
    assert calls == ["ppt"] and adapter.starts == 0
    assert teacher.get(path).json()["data"]["status"] == "succeeded"
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["totalConsumed"] == 7 and quota["reserved"] == 0


def test_interrupted_inline_generation_is_not_repeated(execution, monkeypatch):
    env, adapter, teacher, identity, path, step = execution

    async def execute(*_):
        pytest.fail("An interrupted model request must not be submitted again")

    monkeypatch.setattr(adapter, "execute", execute, raising=False)
    with env["app"].state.sessions.begin() as session:
        task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
        task.status = "running"
    step()
    assert teacher.get(path).json()["data"]["status"] == "failed"
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["totalConsumed"] == quota["reserved"] == 0


@pytest.mark.parametrize("mode", ["overcharge", "failed", "invalid-file", "missing-artifact"])
def test_invalid_or_failed_result_releases_teacher_credits(execution, mode):
    _env, adapter, teacher, _identity, path, step = execution
    if mode == "overcharge":
        adapter.state.credits = 11
    elif mode == "failed":
        adapter.state = ResourceState(status="failed")
    elif mode == "invalid-file":
        adapter.state.artifact.content = b"not a pptx"
    else:
        adapter.state.artifact = None
    step()
    step()
    step()
    assert teacher.get(path).json()["data"]["status"] == "failed"
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["reserved"] == quota["totalConsumed"] == 0 and quota["balance"] == 30
    assert teacher.get(path + "/download").status_code == 409


def test_running_cancel_requires_remote_confirmation(execution, monkeypatch):
    _env, adapter, teacher, _identity, path, step = execution
    step()
    teacher.post(path + "/cancel")

    async def pending(*_):
        return False

    monkeypatch.setattr(adapter, "cancel", pending)
    step()
    assert teacher.get(path).json()["data"]["status"] == "running"
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 10
    monkeypatch.setattr(adapter, "cancel", AdapterFixture.cancel.__get__(adapter))
    step()
    assert teacher.get(path).json()["data"]["status"] == "cancelled"
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 0


def test_provider_rejected_submission_releases_reservation_without_retry(execution, monkeypatch):
    _env, adapter, teacher, _identity, path, step = execution
    calls = []

    async def reject(*args):
        calls.append(args)
        raise AppError(422, "RESOURCE_REQUEST_REJECTED", "生成请求被拒绝。")

    monkeypatch.setattr(adapter, "start", reject)
    step()
    step()
    task = teacher.get(path).json()["data"]
    assert task["status"] == "failed"
    assert task["errorCode"] == "RESOURCE_REQUEST_REJECTED"
    assert len(calls) == 1
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["reserved"] == quota["totalConsumed"] == 0
    assert quota["balance"] == 30


def test_cancel_during_storage_suppresses_output_and_cleans_pending_object(execution, monkeypatch):
    env, _adapter, teacher, identity, path, step = execution
    original = ObjectStore.put

    def racing_put(store, key, content):
        original(store, key, content)
        assert teacher.post(path + "/cancel").status_code == 200

    monkeypatch.setattr(ObjectStore, "put", racing_put)
    step()
    step()
    assert teacher.get(path).json()["data"]["status"] == "cancelled"
    assert teacher.get(path + "/download").status_code == 409
    cleanup_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)
    with env["app"].state.sessions() as session:
        output = Repository(session, env["tenant_id"]).require(
            ResourceOutput, ResourceOutput.task_id == identity
        )
        assert not output.ready and output.deleted_at
    assert teacher.get("/api/v1/me/quota").json()["data"]["totalConsumed"] == 0


def test_live_lease_blocks_duplicate_and_expired_start_is_not_blindly_repeated(execution):
    env, adapter, teacher, identity, path, step = execution
    adapter.supports_start_recovery = False
    sessions = env["app"].state.sessions
    with sessions.begin() as session:
        task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
        task.status, task.lease_token = "running", "crashed-worker"
        task.lease_until = now() + timedelta(minutes=3)
    step()
    assert adapter.starts == 0
    with sessions.begin() as session:
        task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
        task.lease_until = now() - timedelta(seconds=1)
    recover_resources(sessions, env["tenant_id"])
    with sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert (
            len(list(session.scalars(repo.query(OutboxEvent).where(OutboxEvent.kind == "resource.step"))))
            == 2
        )
    step()
    assert adapter.starts == 0
    assert teacher.get(path).json()["data"]["cancelRequested"]
    step()
    assert teacher.get(path).json()["data"]["status"] == "cancelled"


def test_cancel_arriving_during_start_keeps_remote_id_for_confirmation(execution, monkeypatch):
    env, adapter, teacher, identity, path, step = execution

    async def start(*_):
        teacher.post(path + "/cancel")
        return "remote-accepted-before-cancel"

    async def cancel(job_id, *_):
        assert job_id == "remote-accepted-before-cancel"
        return True

    monkeypatch.setattr(adapter, "start", start)
    monkeypatch.setattr(adapter, "cancel", cancel)
    step()
    assert teacher.get(path).json()["data"]["cancelRequested"]
    with env["app"].state.sessions() as session:
        task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
        assert task.provider_job_id == "remote-accepted-before-cancel"
    step()
    assert teacher.get(path).json()["data"]["status"] == "cancelled"


def test_infected_output_fails_before_storage_and_releases_credits(execution, monkeypatch):
    _env, _adapter, teacher, _identity, path, step = execution

    def infected(*_):
        raise AppError(422, "FILE_INFECTED", "fixture")

    def unexpected_put(*_):
        pytest.fail("Infected output must not be stored")

    monkeypatch.setattr("app.resource_execution.scan", infected)
    monkeypatch.setattr(ObjectStore, "put", unexpected_put)
    step()
    step()
    task = teacher.get(path).json()["data"]
    assert task["status"] == "failed" and task["errorCode"] == "FILE_INFECTED"
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 0


def test_expired_unconfirmed_remote_cancel_releases_with_explicit_error(execution, monkeypatch):
    env, adapter, teacher, identity, path, step = execution
    step()
    with env["app"].state.sessions.begin() as session:
        task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
        task.deadline_at = now() - timedelta(minutes=11)

    async def pending(*_):
        return False

    monkeypatch.setattr(adapter, "cancel", pending)
    step()
    task = teacher.get(path).json()["data"]
    assert task["status"] == "failed" and task["errorCode"] == "RESOURCE_CANCEL_UNCONFIRMED"
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 0


def test_stale_writer_after_cancel_cleanup_reopens_cleanup_tombstone(execution, monkeypatch):
    env, _adapter, teacher, identity, path, step = execution
    sessions, settings = env["app"].state.sessions, env["app"].state.settings
    original = ObjectStore.put

    def late_put(store, key, content):
        teacher.post(path + "/cancel")
        with sessions.begin() as session:
            task = Repository(session, env["tenant_id"]).require(ResourceTask, ResourceTask.id == identity)
            task.lease_until = now() - timedelta(seconds=1)
        step()  # Recovery confirms remote cancellation while old storage call is pending.
        cleanup_resource(sessions, settings, env["tenant_id"], identity)
        original(store, key, content)

    monkeypatch.setattr(ObjectStore, "put", late_put)
    step()
    step()
    with sessions() as session:
        output = Repository(session, env["tenant_id"]).require(
            ResourceOutput, ResourceOutput.task_id == identity
        )
        assert not output.ready and output.deleted_at is None
        key = output.object_key
    cleanup_resource(sessions, settings, env["tenant_id"], identity)
    with pytest.raises(AppError, match="STORAGE_UNAVAILABLE"):
        ObjectStore(settings).read(key, 1000000)
    assert teacher.get(path).json()["data"]["status"] == "cancelled"
