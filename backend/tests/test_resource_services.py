"""Real broker, Celery worker lifecycle, S3 and antivirus; test-only generation adapter."""

import io
import os
import threading
import time
import zipfile

import httpx
import pytest
from celery import signals
from celery.contrib.testing.worker import start_worker
from test_resource_execution import AdapterFixture
from test_resource_tasks import submit

from app.models import ResourceOutput, ResourceTask, new_id
from app.repository import Repository
from app.resource_contracts import FACTORIES
from app.storage import ObjectStore

pytestmark = pytest.mark.skipif(
    os.environ.get("XFLJ_TEST_KNOWLEDGE_SERVICES") != "1",
    reason="Run tests/run_knowledge_services.py for real S3/Redis/ClamAV",
)


def test_real_resource_queue_restart_download_settlement_and_antivirus(environment, monkeypatch):
    from app import worker

    env = environment
    settings, sessions, tenant = env["app"].state.settings, env["app"].state.sessions, env["tenant_id"]
    monkeypatch.setattr(worker, "settings", settings)
    adapter = AdapterFixture()
    monkeypatch.setitem(FACTORIES, "ppt", lambda _: adapter)
    teacher, admin = env["client"](), env["client"]("admin")
    account = next(
        row
        for row in admin.get("/api/v1/admin/quota/accounts").json()["data"]["items"]
        if row["username"] == "teacher"
    )
    assert (
        admin.post(
            f"/api/v1/admin/quota/accounts/{account['id']}/adjustments",
            headers={"Idempotency-Key": "services-grant"},
            json={"amount": 30, "reason": "队列验收"},
        ).status_code
        < 300
    )
    response = submit(teacher, key="services-success")
    assert response.status_code == 201, response.text
    identity = response.json()["data"]["id"]
    path = f"/api/v1/resource-tasks/{identity}"

    def wait_until(predicate, dispatch=True):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if dispatch:
                worker.dispatch_outbox()
            if predicate():
                return
            time.sleep(0.25)
        pytest.fail("Real resource worker did not reach expected durable state")

    def started():
        with sessions() as session:
            row = Repository(session, tenant).require(ResourceTask, ResourceTask.id == identity)
            return row.provider_job_id == "external-fixture-job"

    def running_worker():
        return start_worker(
            worker.app, pool="solo", queues=[worker.QUEUE], perform_ping_check=False, shutdown_timeout=15
        )

    assert teacher.get(path).json()["data"]["status"] == "queued"
    with running_worker():
        worker.dispatch_outbox()  # Real Outbox publisher -> Redis -> registered Celery task.
        wait_until(started, dispatch=False)
    assert teacher.get(path).json()["data"]["status"] == "running"
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 10

    # Restart the consumer after start() has persisted the external ID. Only poll
    # may run after restart; the external adapter is a test fixture, not a vendor.
    with running_worker():
        wait_until(lambda: teacher.get(path).json()["data"]["status"] == "succeeded")
        assert adapter.starts == adapter.polls == 1
        quota = teacher.get("/api/v1/me/quota").json()["data"]
        assert quota["balance"] == 23 and quota["reserved"] == 0 and quota["totalConsumed"] == 7
        with sessions() as session:
            output = Repository(session, tenant).require(ResourceOutput, ResourceOutput.task_id == identity)
            key = output.object_key
            assert output.ready
        store = ObjectStore(settings)
        with httpx.Client(trust_env=False) as http:
            assert http.get(f"{store.profile.endpoint_url}/{store.profile.bucket}/{key}").status_code == 403
            response = teacher.get(path + "/download", follow_redirects=False)
            assert response.status_code == 303
            download = http.get(response.headers["location"])
            assert download.status_code == 200 and download.content == adapter.state.artifact.content
            assert "attachment" in download.headers["content-disposition"]

        # Wait for this exact redelivery to finish, not merely for an empty queue.
        duplicate_id, delivered = new_id(), threading.Event()

        def completed(task_id=None, **_):
            if task_id == duplicate_id:
                delivered.set()

        signals.task_postrun.connect(completed, weak=False)
        try:
            worker.app.send_task("xflj.resource", args=[tenant, identity], task_id=duplicate_id)
            assert delivered.wait(10), "Duplicate delivery was not processed"
        finally:
            signals.task_postrun.disconnect(completed)
        assert teacher.get("/api/v1/me/quota").json()["data"] == quota
        assert adapter.starts == adapter.polls == 1

        # A harmless antivirus test string inside a valid PPTX container must be
        # caught by real ClamAV before S3 output publication or teacher charging.
        content = io.BytesIO(adapter.state.artifact.content)
        with zipfile.ZipFile(content, "a") as archive:
            archive.writestr(
                "ppt/media/scan-test.txt",
                b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
            )
        adapter.state.artifact.content = content.getvalue()
        infected = submit(teacher, key="services-infected").json()["data"]["id"]
        infected_path = f"/api/v1/resource-tasks/{infected}"
        wait_until(lambda: teacher.get(infected_path).json()["data"]["status"] == "failed")
        assert teacher.get(infected_path).json()["data"]["errorCode"] == "FILE_INFECTED"
        assert teacher.get(infected_path + "/download").status_code == 409
        assert teacher.get("/api/v1/me/quota").json()["data"] == quota

        def cleaned():
            with sessions() as session:
                output = Repository(session, tenant).require(
                    ResourceOutput, ResourceOutput.task_id == infected
                )
                assert not output.ready
                return output.deleted_at is not None

        wait_until(cleaned)
        assert (
            store.client.list_objects_v2(
                Bucket=store.profile.bucket, Prefix=f"{tenant}/resources/{infected}/"
            ).get("KeyCount")
            == 0
        )
        report = admin.get("/api/v1/admin/statistics").json()["data"]
        assert report["resourceOutputs"]["ppt"] == 1 and report["summary"]["consumedCredits"] == 7
