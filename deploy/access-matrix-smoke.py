"""Non-destructive deployed access checks; no payloads or credentials in the report."""

import json
import os
import uuid
from pathlib import Path

import httpx

ORIGIN = "http://localhost:3100"


def main():
    options = {"base_url": ORIGIN + "/api/v1", "headers": {"Origin": ORIGIN}, "trust_env": False}
    results = []
    with httpx.Client(**options) as admin, httpx.Client(**options) as teacher, httpx.Client(**options) as anonymous:
        for client, username, password in (
            (admin, "admin", os.environ["DEPLOY_ADMIN_PASSWORD"]),
            (teacher, "teacher", os.environ["DEPLOY_TEACHER_PASSWORD"]),
        ):
            assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 200
        paths = ["/admin/users", "/admin/quota/accounts", "/admin/quota/summary", "/admin/models",
                 "/admin/model-providers", "/admin/embedding", "/admin/video", "/admin/statistics",
                 "/admin/audit-logs"]
        for path in paths:
            for client, role, expected in ((admin, "admin", 200), (teacher, "teacher", 403), (anonymous, "anonymous", 401)):
                status = client.get(path).status_code
                results.append({"path": path, "role": role, "expected": expected, "actual": status, "passed": status == expected})
        for path in ["/me", "/me/quota", "/me/quota/transactions", "/conversations", "/skills", "/resource-tasks"]:
            status = anonymous.get(path).status_code
            results.append({"path": path, "role": "anonymous", "expected": 401, "actual": status, "passed": status == 401})
        before = teacher.get("/me/quota").json()["data"]
        capabilities = teacher.get("/resource-tasks/capabilities").json()["data"]
        for entry in capabilities:
            if entry["available"]:
                continue
            response = teacher.post("/resource-tasks", headers={"Idempotency-Key": str(uuid.uuid4())},
                                    json={"kind": entry["kind"], "prompt": "服务未配置状态验收"})
            results.append({"kind": entry["kind"], "check": "unavailable service refuses task",
                            "actual": response.status_code, "passed": response.status_code == 503})
        after = teacher.get("/me/quota").json()["data"]
        results.append({"check": "unavailable media does not change teacher ledger totals", "passed": before == after})
        models = admin.get("/admin/models").json()["data"]
        rows = models["items"] if isinstance(models, dict) else models
        test_models = [row for row in rows if row["name"].startswith("PPT闭环测试-")]
        results.append({"check": "PPT test models disabled", "passed": all(not row["active"] for row in test_models)})
        for client in (admin, teacher):
            client.post("/auth/logout")
            assert client.get("/me").status_code == 401
    report = {"checks": results, "passed": sum(row["passed"] for row in results),
              "failed": sum(not row["passed"] for row in results)}
    Path("docs/DEPLOYED-ACCESS-TEST.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"]}))
    return bool(report["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
