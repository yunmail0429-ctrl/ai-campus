"""Exercise batch document ingestion, mirror synchronization, retrieval, and deletion."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

ORIGIN = "http://localhost:3100"


def login(username, password):
    client = httpx.Client(
        base_url=ORIGIN + "/api/v1", headers={"Origin": ORIGIN}, timeout=120, trust_env=False
    )
    client.post("/auth/login", json={"username": username, "password": password}).raise_for_status()
    return client


def data(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()["data"]


def until(label, predicate, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(2)
    raise AssertionError(f"Timed out waiting for {label}")


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD")
    admin, teacher = login("admin", password), login("teacher", password)
    documents = []
    try:
        school = next(
            row["id"] for row in data(admin, "GET", "/knowledge-bases")["items"]
            if row["scope"] == "school"
        )
        quota = data(teacher, "GET", "/me/quota")
        for position in range(8):
            marker = f"批量索引{position}号" + uuid.uuid4().hex[:10]
            content = f"学校图书角第{position}组核验标记为 {marker}。教师检索该标记时应找到此文档。"
            document = data(
                admin, "POST", f"/knowledge-bases/{school}/documents",
                files={"file": (f"batch-{position}.txt", content.encode(), "text/plain")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )["id"]
            documents.append((document, marker))

        def all_ready():
            statuses = [data(admin, "GET", f"/documents/{identity}")["status"] for identity, _ in documents]
            assert all(status not in ("failed", "index_failed") for status in statuses), statuses
            return all(status == "ready" for status in statuses)

        until("eight documents ready", all_ready)

        def all_synced():
            state = data(admin, "GET", "/admin/embedding/qdrant")
            assert state["connected"], state
            return state["pendingDocuments"] == 0

        until("Qdrant mirror backlog empty", all_synced)
        for identity, marker in documents:
            items = data(teacher, "POST", "/knowledge/search", json={"query": marker})["items"]
            assert identity in {row["documentId"] for row in items}, (identity, marker, items)

        def concurrent_search(entry):
            identity, marker = entry
            started = time.monotonic()
            items = data(teacher, "POST", "/knowledge/search", json={"query": marker})["items"]
            assert identity in {row["documentId"] for row in items}, (identity, marker, items)
            return time.monotonic() - started

        with ThreadPoolExecutor(max_workers=8) as pool:
            latencies = list(pool.map(concurrent_search, documents))
        assert data(teacher, "GET", "/me/quota") == quota

        for identity, _ in documents:
            data(admin, "DELETE", f"/documents/{identity}")
        for identity, marker in documents:
            items = data(teacher, "POST", "/knowledge/search", json={"query": marker})["items"]
            assert identity not in {row["documentId"] for row in items}
        documents.clear()
        assert data(teacher, "GET", "/me/quota") == quota
        print(
            "PASS: eight-document ingestion, Qdrant sync, 8 concurrent teacher searches, "
            f"deletion, zero teacher charges; max search latency {max(latencies):.2f}s"
        )
    finally:
        for identity, _ in documents:
            data(admin, "DELETE", f"/documents/{identity}")
        for client in (admin, teacher):
            client.post("/auth/logout")
            client.close()


if __name__ == "__main__":
    main()
