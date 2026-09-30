"""Actual local deployment: MiMo, Celery, ClamAV, private storage and teacher ledger."""

import io
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import httpx
from pptx import Presentation

ORIGIN = "http://localhost:3100"


def main():
    options = dict(
        base_url=ORIGIN + "/api/v1",
        headers={"Origin": ORIGIN},
        timeout=30,
        trust_env=False,
    )
    report = {}
    with httpx.Client(**options) as admin, httpx.Client(**options) as teacher:

        def call(client, method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()["data"]

        call(
            admin,
            "POST",
            "/auth/login",
            json={"username": "admin", "password": os.environ["DEPLOY_ADMIN_PASSWORD"]},
        )
        models = call(admin, "GET", "/admin/models")
        rows = models["items"] if isinstance(models, dict) else models
        if any(row.get("default") for row in rows):
            raise RuntimeError(
                "Existing default model: refusing to replace administrator configuration"
            )
        suffix = uuid.uuid4().hex[:8]
        model = call(
            admin,
            "POST",
            "/admin/models",
            json={
                "name": "PPT闭环测试-" + suffix,
                "provider": "mimo",
                "model": "mimo-v2.5",
                "active": True,
                "contextTokens": 32000,
                "maxOutputTokens": 8192,
                "timeoutSeconds": 120,
                "inputPrice": 1000,
                "outputPrice": 1000,
            },
        )
        user = account = task = None
        try:
            call(admin, "POST", f"/admin/models/{model['id']}/default")
            username, password = "ppt-test-" + suffix, secrets.token_urlsafe(24)
            user = call(
                admin,
                "POST",
                "/admin/users",
                json={
                    "username": username,
                    "password": password,
                    "name": "PPT验收临时教师",
                    "role": "teacher",
                    "grade": "高一",
                    "subject": "数学",
                },
            )
            account = next(
                row
                for row in call(
                    admin, "GET", "/admin/quota/accounts", params={"q": username}
                )["items"]
                if row["username"] == username
            )
            call(
                admin,
                "POST",
                f"/admin/quota/accounts/{account['id']}/adjustments",
                headers={"Idempotency-Key": "grant-" + suffix},
                json={"amount": 100, "reason": "PPT闭环测试额度"},
            )
            call(
                teacher,
                "POST",
                "/auth/login",
                json={"username": username, "password": password},
            )
            payload = {
                "kind": "ppt",
                "prompt": "高中数学函数单调性：图像观察、定义证明、附答案课堂练习。",
                "options": {"pages": 3, "style": "简洁教学"},
            }
            headers = {"Idempotency-Key": "ppt-" + suffix}
            task = call(
                teacher, "POST", "/resource-tasks", json=payload, headers=headers
            )
            path = "/resource-tasks/" + task["id"]
            deadline = time.monotonic() + 210
            while time.monotonic() < deadline:
                task = call(teacher, "GET", path)
                if task["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(2)
            assert task["status"] == "succeeded", (
                task.get("errorCode") or task["status"]
            )
            response = teacher.get(path + "/download", follow_redirects=False)
            assert response.status_code == 303
            with httpx.Client(trust_env=False, timeout=30) as download:
                artifact = download.get(response.headers["location"])
            assert artifact.status_code == 200
            deck = Presentation(io.BytesIO(artifact.content))
            assert len(deck.slides) == 3
            Path("local-data/mimo-ppt-task-validation.pptx").write_bytes(
                artifact.content
            )
            before = call(teacher, "GET", "/me/quota")
            assert (
                before["reserved"] == 0
                and before["totalConsumed"] == task["credits"] > 0
            )
            replay = call(
                teacher, "POST", "/resource-tasks", json=payload, headers=headers
            )
            assert replay["id"] == task["id"]
            assert call(teacher, "GET", "/me/quota") == before
            report = {
                "status": "passed",
                "taskId": task["id"],
                "pages": len(deck.slides),
                "testCredits": task["credits"],
                "reserved": before["reserved"],
                "duplicateRequestNoCharge": True,
                "realQueueStorageScanner": True,
                "visualReview": "pending",
            }
            Path("docs/PPT-TASK-TEST.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(json.dumps(report))
        finally:
            if task and task["status"] in ("queued", "running"):
                call(teacher, "POST", f"/resource-tasks/{task['id']}/cancel")
            if account:
                rows = call(
                    admin, "GET", "/admin/quota/accounts", params={"q": username}
                )["items"]
                balance = next(row for row in rows if row["id"] == account["id"])
                available = balance["balance"] - balance["reserved"]
                if available:
                    call(
                        admin,
                        "POST",
                        f"/admin/quota/accounts/{account['id']}/adjustments",
                        headers={"Idempotency-Key": "reclaim-" + suffix},
                        json={"amount": -available, "reason": "回收PPT测试未使用额度"},
                    )
            call(
                admin,
                "PATCH",
                f"/admin/models/{model['id']}/state",
                json={"active": False, "expectedRevision": model["revision"]},
            )
            if user:
                call(
                    admin, "PATCH", f"/admin/users/{user['id']}", json={"active": False}
                )


if __name__ == "__main__":
    main()
