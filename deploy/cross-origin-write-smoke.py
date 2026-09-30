"""Check deployed teacher writes reject a foreign Origin without side effects."""

import os
import uuid

import httpx


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD to the current teacher password.")
    origin = "http://localhost:3100"
    with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=20, trust_env=False) as client:
        login = client.post("/auth/login", json={"username": "teacher", "password": password})
        assert login.status_code == 200
        before = client.get("/me/quota").json()["data"]
        marker = f"Cross-origin rejection {uuid.uuid4()}"
        denied = client.post("/conversations", json={"title": marker}, headers={"Origin": "https://untrusted.example"})
        assert denied.status_code == 403
        cursor = None
        while True:
            conversations = client.get("/conversations", params={"limit": 50, **({"cursor": cursor} if cursor else {})})
            assert conversations.status_code == 200
            page = conversations.json()["data"]
            assert all(row["title"] != marker for row in page["items"])
            cursor = page["nextCursor"]
            if not cursor:
                break
        after = client.get("/me/quota").json()["data"]
        assert after == before
        assert client.post("/auth/logout").status_code == 200
        print("PASS: foreign-Origin teacher write denied, no conversation or quota change")


if __name__ == "__main__":
    main()
