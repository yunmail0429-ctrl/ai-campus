"""Real-provider test on a labelled temporary account. No secrets in output."""

import json
import os
import secrets
import uuid
from pathlib import Path

import httpx

origin = "http://localhost:3100"
password = os.environ.get("DEPLOY_ADMIN_PASSWORD") or (
    Path(__file__).parent / "admin.env"
).read_text().strip().split("=", 1)[1]
report = {}
with httpx.Client(
    base_url=origin + "/api/v1",
    headers={"Origin": origin},
    timeout=180,
    trust_env=False,
) as admin:

    def call(client, method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(
                f"{path}: HTTP {response.status_code}, code={response.json().get('code')}"
            )
        return response.json()["data"]

    call(admin, "POST", "/auth/login", json={"username": "admin", "password": password})
    model = call(
        admin,
        "POST",
        "/admin/models",
        json={
            "name": "MiMo v2.5 联调专用（正式价格待确认）",
            "provider": "mimo",
            "model": "mimo-v2.5",
            "active": True,
            "contextTokens": 32768,
            "maxOutputTokens": 2048,
            "timeoutSeconds": 120,
            "inputPrice": 1000,
            "outputPrice": 1000,
        },
    )
    user = None
    try:
        call(admin, "POST", f"/admin/models/{model['id']}/default")
        report["connection"] = call(admin, "POST", f"/admin/models/{model['id']}/check")
        username, secret = (
            "mimo-test-" + uuid.uuid4().hex[:8],
            secrets.token_urlsafe(24),
        )
        user = call(
            admin,
            "POST",
            "/admin/users",
            json={
                "username": username,
                "password": secret,
                "name": "MiMo联调测试账号",
                "subject": "数学",
                "grade": "高一",
                "role": "teacher",
            },
        )
        accounts = call(admin, "GET", "/admin/quota/accounts", params={"q": username})[
            "items"
        ]
        account = next(row for row in accounts if row["username"] == username)
        call(
            admin,
            "POST",
            f"/admin/quota/accounts/{account['id']}/adjustments",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"amount": 100, "reason": "MiMo真实接口联调测试额度，非正式教师定价"},
        )
        with httpx.Client(
            base_url=origin + "/api/v1",
            headers={"Origin": origin},
            timeout=180,
            trust_env=False,
        ) as teacher:
            call(
                teacher,
                "POST",
                "/auth/login",
                json={"username": username, "password": secret},
            )
            chat = call(
                teacher,
                "POST",
                "/conversations",
                json={"title": "MiMo真实教学回答验证"},
            )["id"]
            payload = {
                "content": "请为高一学生解释函数单调递增，给出一个正确例子和一道附答案的练习。控制在200字内。",
                "useKnowledge": False,
            }
            key = str(uuid.uuid4())
            response = teacher.post(
                f"/conversations/{chat}/messages",
                json=payload,
                headers={"Idempotency-Key": key},
            )
            assert response.status_code == 200
            events = []
            for block in response.text.split("\n\n"):
                event, data = None, None
                for line in block.splitlines():
                    if line.startswith("event: "):
                        event = line[7:]
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                if event:
                    events.append((event, data))
            errors = [data.get("code") for event, data in events if event == "error"]
            assert not errors, errors
            messages = call(teacher, "GET", f"/conversations/{chat}/messages")["items"]
            answer = next(row for row in messages if row["role"] == "assistant")
            assert answer["status"] == "completed" and len(answer["content"]) > 30
            before = call(teacher, "GET", "/me/quota")
            assert before["totalConsumed"] > 0 and before["reserved"] == 0
            replay = teacher.post(
                f"/conversations/{chat}/messages",
                json=payload,
                headers={"Idempotency-Key": key},
            )
            assert replay.status_code == 200
            after = call(teacher, "GET", "/me/quota")
            assert (
                after["totalConsumed"] == before["totalConsumed"]
                and after["reserved"] == 0
            )
            report.update(
                {
                    "answer": answer["content"],
                    "usage": [data for event, data in events if event == "usage"],
                    "testCreditsConsumed": before["totalConsumed"],
                    "idempotentReplay": True,
                    "modelId": model["id"],
                }
            )
            call(teacher, "POST", "/auth/logout")
            call(
                admin,
                "POST",
                f"/admin/quota/accounts/{account['id']}/adjustments",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={
                    "amount": -after["balance"],
                    "reason": "联调结束收回未使用测试额度",
                },
            )
        Path("docs/MIMO-REAL-TEST.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "PASS: real MiMo connection, teaching answer, token settlement and no duplicate charge"
        )
    finally:
        call(
            admin,
            "PATCH",
            f"/admin/models/{model['id']}/state",
            json={"active": False, "expectedRevision": model["revision"]},
        )
        if user:
            call(admin, "PATCH", f"/admin/users/{user['id']}", json={"active": False})
