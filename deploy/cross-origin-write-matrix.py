"""Check every documented deployed write route rejects a foreign Origin."""

import json
import os
import re
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://localhost:3100"
BAD_ORIGIN = "https://untrusted.example"
METHODS = ("post", "put", "patch", "delete")


def main():
    teacher_password = os.environ.get("DEPLOY_TEST_PASSWORD")
    admin_password = os.environ.get("DEPLOY_ADMIN_PASSWORD")
    if not teacher_password or not admin_password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD and DEPLOY_ADMIN_PASSWORD to current passwords.")
    schema = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    operations = [
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        if path.startswith("/api/v1/")
        for method in METHODS
        if method in methods
    ]
    results = []
    options = {"base_url": ORIGIN, "headers": {"Origin": ORIGIN}, "timeout": 20, "trust_env": False}
    with httpx.Client(**options) as teacher, httpx.Client(**options) as admin:
        assert teacher.post("/api/v1/auth/login", json={"username": "teacher", "password": teacher_password}).status_code == 200
        assert admin.post("/api/v1/auth/login", json={"username": "admin", "password": admin_password}).status_code == 200
        before = {role: client.get("/api/v1/me/quota").json()["data"] for role, client in (("teacher", teacher), ("admin", admin))}
        for method, template in operations:
            role = "admin" if template.startswith("/api/v1/admin/") else "teacher"
            client = admin if role == "admin" else teacher
            path = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000000", template)
            response = client.request(
                method, path, json={},
                headers={"Origin": BAD_ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            )
            code = response.json().get("code")
            results.append({"method": method, "path": template, "role": role, "status": response.status_code,
                            "code": code, "passed": response.status_code == 403 and code == "INVALID_ORIGIN"})
        after = {role: client.get("/api/v1/me/quota").json()["data"] for role, client in (("teacher", teacher), ("admin", admin))}
        assert before == after
        assert teacher.post("/api/v1/auth/logout").status_code == 200
        assert admin.post("/api/v1/auth/logout").status_code == 200
    report = {"scope": "Foreign Origin on documented write routes with matching teacher/admin session and empty JSON payload",
              "checked": len(results), "passed": sum(row["passed"] for row in results),
              "failed": [row for row in results if not row["passed"]]}
    (ROOT / "docs/DEPLOYED-CROSS-ORIGIN-WRITES.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"checked": report["checked"], "passed": report["passed"], "failed": len(report["failed"])},
                     ensure_ascii=False))
    return bool(report["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
