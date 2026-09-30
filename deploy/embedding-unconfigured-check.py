"""Check the deployed knowledge flow when no embedding model is configured."""

import json
import os
import time
import uuid

import httpx


def main():
    origin = "http://localhost:3100"
    password = os.getenv("DEPLOY_TEACHER_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEACHER_PASSWORD before running.")
    with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin},
                      timeout=30, trust_env=False) as client:
        def request(method, path, **kwargs):
            return client.request(method, path, **kwargs)

        login = request("POST", "/auth/login", json={"username": "teacher", "password": password})
        login.raise_for_status()
        document = None
        try:
            before = request("GET", "/me/quota").json()["data"]
            bases = request("GET", "/knowledge-bases?limit=50").json()["data"]["items"]
            private = next((item for item in bases if item["scope"] == "private"), None)
            if private is None:
                raise RuntimeError("Teacher has no existing private knowledge base")
            upload = request(
                "POST", f"/knowledge-bases/{private['id']}/documents",
                files={"file": ("embedding-audit.txt", b"Short classroom audit document.", "text/plain")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            upload.raise_for_status()
            document = upload.json()["data"]["id"]
            for _ in range(30):
                row = request("GET", f"/documents/{document}").json()["data"]
                if row["status"] in ("parsed", "ready", "failed"):
                    break
                time.sleep(1)
            assert row["status"] in ("parsed", "ready"), row["status"]
            denied = request("POST", f"/documents/{document}/reindex")
            assert denied.status_code == 503, denied.status_code
            assert denied.json()["code"] == "EMBEDDING_NOT_CONFIGURED"
            after = request("GET", "/me/quota").json()["data"]
            assert after == before
            report = {"status": "passed", "documentParsed": True,
                      "reindexHttp": denied.status_code,
                      "reindexCode": denied.json()["code"], "teacherQuotaUnchanged": True}
            print(json.dumps(report))
        finally:
            if document:
                removed = request("DELETE", f"/documents/{document}")
                if removed.status_code != 200:
                    raise RuntimeError("Audit document cleanup failed")
            request("POST", "/auth/logout")


if __name__ == "__main__":
    main()
