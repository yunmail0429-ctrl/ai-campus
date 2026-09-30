"""Exercise the deployed local stack without logging credentials or signed URLs."""

import os
import time
import uuid

import httpx

origin = "http://localhost:3100"
password = os.environ.get("DEPLOY_ADMIN_PASSWORD")
if not password:
    raise SystemExit("Set DEPLOY_ADMIN_PASSWORD to the current administrator password before running smoke.py.")
with httpx.Client(
    base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=45, trust_env=False
) as client:

    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
        return response.json()["data"]

    call("POST", "/auth/login", json={"username": "admin", "password": password})
    assert "admin" in call("GET", "/me")["roles"]
    quota = call("GET", "/me/quota")
    conversation = call("POST", "/conversations", json={"title": "Docker 部署验证"})[
        "id"
    ]
    try:
        content = "# 部署验证\n\n中文教学草稿。"
        call(
            "PUT",
            f"/conversations/{conversation}/draft",
            json={"content": content, "revision": 0},
        )
        for fmt in ("md", "docx", "pdf"):
            response = client.post(
                f"/conversations/{conversation}/export",
                json={"content": content, "format": fmt},
            )
            assert response.status_code == 200 and len(response.content) > 20
        document = call(
            "POST",
            f"/conversations/{conversation}/attachments",
            files={
                "file": (
                    "deployment.txt",
                    "真实容器文件解析验证。".encode(),
                    "text/plain",
                )
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )["id"]
        for _ in range(120):
            rows = call("GET", f"/conversations/{conversation}/attachments")["items"]
            row = next(item for item in rows if item["id"] == document)
            if row["chunkCount"] > 0:
                break
            if row["status"] in ("failed", "quarantined"):
                raise RuntimeError(f"Document processing failed: {row['errorCode']}")
            time.sleep(2)
        else:
            raise RuntimeError("Document processing timeout")
        url = call("POST", f"/documents/{document}/download")["url"]
        with httpx.Client(trust_env=False) as download:
            result = download.get(url)
        assert (
            result.status_code == 200
            and result.content == "真实容器文件解析验证。".encode()
        )
        assert call("GET", "/me/quota")["totalConsumed"] == quota["totalConsumed"]
        print(
            "PASS: login, roles, draft, three exports, queue/scanning/parsing, signed download, no extra credits"
        )
    finally:
        # A keep-alive socket can close between the signed download and cleanup.
        # DELETE is safe to repeat; 404 means the first attempt already committed.
        for attempt in range(3):
            try:
                cleanup = client.delete(f"/conversations/{conversation}")
                if cleanup.status_code in (200, 404):
                    break
                raise RuntimeError(f"Conversation cleanup: HTTP {cleanup.status_code}")
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(1)
    call("POST", "/auth/logout")
    assert client.get("/me").status_code == 401
    print("PASS: cleanup and session revocation")
