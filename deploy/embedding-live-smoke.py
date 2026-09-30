"""Verify live attachment indexing, citation, answer billing, and idempotency."""

import os
import subprocess
import time
import uuid

import httpx


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD to the current teacher password.")
    origin = "http://localhost:3100"
    outage = os.environ.get("QDRANT_OUTAGE_TEST") == "1"
    qdrant_stopped = False
    with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=180, trust_env=False) as client:
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]

        data("POST", "/auth/login", json={"username": "teacher", "password": password})
        before = data("GET", "/me/quota")
        conversation = data("POST", "/conversations", json={"title": "Live BGE attachment acceptance"})["id"]
        try:
            document = data(
                "POST", f"/conversations/{conversation}/attachments",
                files={"file": (
                    "campus-science-note.txt",
                    "校内科普角的蓝色水壶编号为 QH-7319。这个编号用于课堂观察记录。".encode(),
                    "text/plain",
                )},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                row = data("GET", f"/documents/{document}")
                if row["status"] in ("ready", "failed", "index_failed"):
                    break
                time.sleep(2)
            else:
                raise AssertionError("Document did not finish indexing")
            assert row["status"] == "ready", (row["status"], row.get("errorCode"))
            assert row["chunkCount"] > 0
            assert data("GET", "/me/quota") == before

            if outage:
                subprocess.run(["docker", "stop", "ai-campus-office-qdrant-1"], check=True)
                qdrant_stopped = True

            key = str(uuid.uuid4())
            payload = {
                "content": "根据附件，蓝色水壶的编号是什么？只回答编号。",
                "documentIds": [document],
                "useKnowledge": True,
            }

            def send():
                with client.stream(
                    "POST", f"/conversations/{conversation}/messages", json=payload,
                    headers={"Idempotency-Key": key},
                ) as response:
                    assert response.status_code == 200, response.status_code
                    return [line for line in response.iter_lines() if line.startswith("event:")]

            events = send()
            messages = data("GET", f"/conversations/{conversation}/messages")["items"]
            answer = next(item for item in messages if item["role"] == "assistant")
            assert "event: citation" in events, events
            assert "event: message.completed" in events, events
            assert answer["status"] == "completed" and "QH-7319" in answer["content"]
            after = data("GET", "/me/quota")
            assert after["totalConsumed"] > before["totalConsumed"]
            assert after["balance"] < before["balance"]
            assert after["reserved"] == before["reserved"]
            replay = send()
            assert "event: message.completed" in replay
            assert data("GET", "/me/quota") == after
            assert data("GET", f"/conversations/{conversation}/messages")["items"] == messages
            mode = "Qdrant stopped; pgvector fallback" if outage else "Qdrant available"
            print(f"PASS: {mode}; live BGE attachment, cited answer, generation-only billing, idempotent replay")
        finally:
            if qdrant_stopped:
                subprocess.run(["docker", "start", "ai-campus-office-qdrant-1"], check=True)
            data("DELETE", f"/conversations/{conversation}")
            data("POST", "/auth/logout")


if __name__ == "__main__":
    main()
