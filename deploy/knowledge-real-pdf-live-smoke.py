"""Exercise real 26-page PDF parsing, BGE indexing, retrieval, and billing."""

import os
import time
import uuid
from pathlib import Path

import httpx

ORIGIN = "http://localhost:3100"
PDF = Path(__file__).resolve().parents[1] / "local-data/real-document-smoke/worldbank-financial.pdf"


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


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD")
    admin, teacher = login("admin", password), login("teacher", password)
    document = os.environ.get("EXISTING_DOCUMENT_ID")
    try:
        school = next(
            row["id"] for row in data(admin, "GET", "/knowledge-bases")["items"]
            if row["scope"] == "school"
        )
        quota = data(teacher, "GET", "/me/quota")
        if not document:
            document = data(
                admin, "POST", f"/knowledge-bases/{school}/documents",
                files={"file": (PDF.name, PDF.read_bytes(), "application/pdf")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )["id"]
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            row = data(admin, "GET", f"/documents/{document}")
            if row["status"] in ("ready", "failed", "index_failed"):
                break
            time.sleep(3)
        else:
            raise AssertionError("Long PDF did not finish indexing within 10 minutes")
        assert row["status"] == "ready", row
        assert row["chunkCount"] >= 20, row
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            mirror = data(admin, "GET", "/admin/embedding/qdrant")
            if mirror["connected"] and mirror["pendingDocuments"] == 0:
                break
            time.sleep(2)
        else:
            raise AssertionError(f"Qdrant mirror did not catch up: {mirror}")
        query = "FY24 top 2 donors GFATM United Kingdom percentage total contributions"
        result = data(teacher, "POST", "/knowledge/search", json={"query": query})
        hits = [item for item in result["items"] if item["documentId"] == document]
        assert hits, result
        assert "50%" in " ".join(item["excerpt"] for item in hits), hits
        assert data(teacher, "GET", "/me/quota") == quota
        print(
            f"PASS: real 26-page PDF, {row['chunkCount']} chunks, BGE/Qdrant retrieval "
            f"found 50% fact in {len(hits)} hits, no teacher charge"
        )
    finally:
        if document:
            data(admin, "DELETE", f"/documents/{document}")
        for client in (admin, teacher):
            client.post("/auth/logout")
            client.close()


if __name__ == "__main__":
    main()
