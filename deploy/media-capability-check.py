"""Verify deployed media capability claims and rejected-task billing without printing secrets."""

import json
import os
import uuid
from pathlib import Path

import httpx

ORIGIN = "http://localhost:3100"


def main():
    password = os.environ.get("DEPLOY_TEACHER_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEACHER_PASSWORD before running.")
    with httpx.Client(
        base_url=ORIGIN + "/api/v1", headers={"Origin": ORIGIN}, timeout=45, trust_env=False
    ) as client:
        login = client.post("/auth/login", json={"username": "teacher", "password": password})
        assert login.status_code == 200
        before_quota = client.get("/me/quota").json()["data"]
        before_tasks = client.get("/resource-tasks").json()["data"]["items"]
        capabilities = client.get("/resource-tasks/capabilities").json()["data"]
        rows = []
        for entry in capabilities:
            row = {"kind": entry["kind"], "available": entry["available"], "reason": entry["reason"]}
            if not entry["available"]:
                response = client.post(
                    "/resource-tasks",
                    json={"kind": entry["kind"], "prompt": "服务未配置状态验收"},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                body = response.json()
                row.update({"status": response.status_code, "code": body["code"]})
                row["passed"] = response.status_code == 503 and body["code"] != "OK"
            else:
                row["passed"] = True
            rows.append(row)
        after_quota = client.get("/me/quota").json()["data"]
        after_tasks = client.get("/resource-tasks").json()["data"]["items"]
        client.post("/auth/logout")
    report = {
        "capabilities": rows,
        "quotaUnchanged": before_quota == after_quota,
        "taskCountUnchanged": len(before_tasks) == len(after_tasks),
    }
    report["passed"] = all(row["passed"] for row in rows) and report["quotaUnchanged"] and report["taskCountUnchanged"]
    Path("docs/DEPLOYED-MEDIA-CAPABILITIES.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"kinds": len(rows), "unavailable": sum(not row["available"] for row in rows), "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
