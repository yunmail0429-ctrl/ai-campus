import os
import time

import httpx
import pytest
from celery.contrib.testing.worker import start_worker
from test_knowledge import base, upload

from app.ingestion import dispatch
from app.models import Document
from app.repository import Repository
from app.storage import ObjectStore

pytestmark = pytest.mark.skipif(
    os.environ.get("XFLJ_TEST_KNOWLEDGE_SERVICES") != "1",
    reason="Run tests/run_knowledge_services.py for real S3/Redis/ClamAV",
)


def test_real_storage_queue_scanner_parsing_and_deletion(environment, monkeypatch):
    from app import worker

    env = environment
    monkeypatch.setattr(worker, "settings", env["app"].state.settings)
    client = env["client"]()
    identity = base(client)
    content = "高一数学教学资料：函数的单调性，使用图像和定义证明。".encode()
    document = upload(client, identity, content).json()["data"]["id"]

    def publish(kind, tenant, target, event):
        worker.app.send_task(
            "xflj.ingest" if kind == "document.ingest" else "xflj.delete",
            args=[tenant, target],
            task_id=event,
        )

    def wait_status(doc, expected):
        for _ in range(80):
            row = client.get(f"/api/v1/documents/{doc}").json()["data"]
            if row["status"] == expected:
                return row
            if row["status"] in ("failed", "quarantined"):
                pytest.fail(f"Unexpected document state: {row['status']} / {row['errorCode']}")
            time.sleep(0.25)
        pytest.fail(f"Document did not reach {expected}")

    with start_worker(
        worker.app, pool="solo", queues=[worker.QUEUE], perform_ping_check=False, shutdown_timeout=15
    ):
        dispatch(env["app"].state.sessions, env["tenant_id"], publish)
        assert wait_status(document, "parsed")["chunkCount"] == 1
        with env["app"].state.sessions() as session:
            row = Repository(session, env["tenant_id"]).require(Document, Document.id == document)
            key = row.object_key
        store = ObjectStore(env["app"].state.settings)
        # Anonymous access is refused; a bounded authenticated signature returns the actual bytes.
        with httpx.Client(trust_env=False) as http:
            assert http.get(f"{store.profile.endpoint_url}/{store.profile.bucket}/{key}").status_code == 403
            url = client.post(f"/api/v1/documents/{document}/download").json()["data"]["url"]
            response = http.get(url)
            assert response.status_code == 200 and response.content == content
            assert "attachment" in response.headers["content-disposition"]
        # Standard harmless antivirus test string; ClamAV must quarantine it before parsing.
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        infected = upload(client, identity, eicar, name="scan-test.txt").json()["data"]["id"]
        dispatch(env["app"].state.sessions, env["tenant_id"], publish)
        wait_status(infected, "quarantined")
        assert client.post(f"/api/v1/documents/{infected}/download").status_code == 409
        assert client.delete(f"/api/v1/documents/{document}").status_code == 200
        dispatch(env["app"].state.sessions, env["tenant_id"], publish)
        for _ in range(80):
            with env["app"].state.sessions() as session:
                row = Repository(session, env["tenant_id"]).require(Document, Document.id == document)
                if row.object_deleted_at:
                    break
            time.sleep(0.25)
        else:
            pytest.fail("Object deletion did not complete")
        assert store.client.list_objects_v2(Bucket=store.profile.bucket, Prefix=key).get("KeyCount") == 0
