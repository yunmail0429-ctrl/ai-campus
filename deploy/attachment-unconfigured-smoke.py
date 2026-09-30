"""Exercise the deployed teacher attachment path before embedding is configured."""

import os
import time
import uuid

import httpx


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD to the current teacher password.")
    origin = "http://localhost:3100"
    with httpx.Client(
        base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=30, trust_env=False
    ) as client:
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]

        data("POST", "/auth/login", json={"username": "teacher", "password": password})
        before = data("GET", "/me/quota")
        conversation = data("POST", "/conversations", json={"title": "Attachment indexing acceptance"})["id"]
        try:
            document = data(
                "POST",
                f"/conversations/{conversation}/attachments",
                files={"file": ("indexing-audit.txt", b"The classroom example is the water cycle.", "text/plain")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )["id"]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                row = data("GET", f"/documents/{document}")
                if row["status"] in ("parsed", "ready", "failed", "index_failed"):
                    break
                time.sleep(2)
            else:
                raise AssertionError("Document processing timed out")
            assert row["status"] == "parsed", row["status"]
            response = client.post(
                f"/conversations/{conversation}/messages",
                json={"content": "What is the example?", "documentIds": [document], "useKnowledge": True},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 409, response.status_code
            assert response.json()["code"] == "DOCUMENT_NOT_READY", response.json()["code"]
            assert data("GET", f"/conversations/{conversation}/messages")["items"] == []
            assert data("GET", "/me/quota") == before
            print("PASS: parsed attachment blocked before model call; teacher quota unchanged")
        finally:
            data("DELETE", f"/conversations/{conversation}")
            data("POST", "/auth/logout")


if __name__ == "__main__":
    main()
