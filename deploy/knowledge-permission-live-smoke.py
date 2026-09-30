"""Exercise live private/school knowledge visibility and billing."""

import os
import secrets
import time
import uuid

import httpx


def session(username, password):
    origin = "http://localhost:3100"
    client = httpx.Client(
        base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=120, trust_env=False
    )
    response = client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return client


def data(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()["data"]


def upload(client, base_id, marker):
    return data(
        client, "POST", f"/knowledge-bases/{base_id}/documents",
        files={"file": ("permission-probe.txt", f"课堂核验标记为 {marker}。".encode(), "text/plain")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )["id"]


def ready(client, document_id):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        row = data(client, "GET", f"/documents/{document_id}")
        if row["status"] in ("ready", "failed", "index_failed"):
            assert row["status"] == "ready", row
            return
        time.sleep(2)
    raise AssertionError(f"Document {document_id} did not become ready")


def matches(client, marker):
    return {
        item["documentId"]
        for item in data(client, "POST", "/knowledge/search", json={"query": marker})["items"]
    }


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD")
    teacher = session("teacher", password)
    admin = session("admin", password)
    peer = None
    peer_id = None
    documents = []
    try:
        if os.environ.get("TEST_SECOND_TEACHER") == "1":
            username = "probe_" + uuid.uuid4().hex[:12]
            peer_password = secrets.token_urlsafe(24)
            created = data(
                admin, "POST", "/admin/users",
                json={
                    "username": username,
                    "password": peer_password,
                    "name": "权限验收教师",
                    "subject": "数学",
                    "grade": "高一",
                    "role": "teacher",
                },
            )
            peer_id = created["id"]
            peer = session(username, peer_password)
        bases = data(teacher, "GET", "/knowledge-bases")["items"]
        private = next(row["id"] for row in bases if row["scope"] == "private")
        school = next(row["id"] for row in bases if row["scope"] == "school")
        quota = data(teacher, "GET", "/me/quota")
        private_marker = "私有隔离" + uuid.uuid4().hex[:12]
        school_marker = "学校共享" + uuid.uuid4().hex[:12]
        private_doc = upload(teacher, private, private_marker)
        documents.append((teacher, private_doc))
        school_doc = upload(admin, school, school_marker)
        documents.append((admin, school_doc))
        ready(teacher, private_doc)
        ready(admin, school_doc)
        reindexed = data(teacher, "POST", f"/documents/{private_doc}/reindex")
        assert reindexed["status"] in ("parsed", "indexing", "ready"), reindexed
        ready(teacher, private_doc)
        assert private_doc in matches(teacher, private_marker)
        assert private_doc not in matches(admin, private_marker)
        if peer:
            assert private_doc not in matches(peer, private_marker)
            assert peer.get(f"/documents/{private_doc}").status_code == 404
            assert peer.get(f"/knowledge-bases/{private}/documents").status_code == 404
        assert school_doc in matches(teacher, school_marker)
        assert school_doc in matches(admin, school_marker)
        if peer:
            assert school_doc in matches(peer, school_marker)
        assert admin.get(f"/documents/{private_doc}").status_code == 404
        assert teacher.delete(f"/documents/{school_doc}").status_code == 403
        assert data(teacher, "GET", "/me/quota") == quota
        for client, document_id in documents:
            data(client, "DELETE", f"/documents/{document_id}")
        documents.clear()
        assert private_doc not in matches(teacher, private_marker)
        assert school_doc not in matches(teacher, school_marker)
        if peer:
            assert school_doc not in matches(peer, school_marker)
        assert data(teacher, "GET", "/me/quota") == quota
        print("PASS: live private/school search permissions, denied access, deletion, school-paid indexing/search")
    finally:
        for client, document_id in documents:
            data(client, "DELETE", f"/documents/{document_id}")
        if peer_id:
            data(admin, "PATCH", f"/admin/users/{peer_id}", json={"active": False})
        for client in (teacher, admin, peer):
            if client is None:
                continue
            client.post("/auth/logout")
            client.close()


if __name__ == "__main__":
    main()
