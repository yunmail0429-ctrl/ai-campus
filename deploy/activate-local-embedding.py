"""Enable the verified local BGE model through the administrator API."""

import os

import httpx


def main():
    password = os.environ.get("DEPLOY_ADMIN_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_ADMIN_PASSWORD to the current administrator password.")
    origin = "http://localhost:3100"
    with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=90, trust_env=False) as client:
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]

        data("POST", "/auth/login", json={"username": "admin", "password": password})
        try:
            current = data("GET", "/admin/embedding")["config"]
            payload = {
                "provider": "bge-large-zh-local",
                "model": "BAAI/bge-large-zh-v1.5",
                "dimensions": 1024,
                "requestDimensions": False,
                "maxInputTokens": 1600,
                "timeoutSeconds": 90,
                "minScore": 0.35,
                "active": True,
                "expectedRevision": current["revision"] if current else 0,
            }
            saved = data("PUT", "/admin/embedding", json=payload)["config"]
            checked = data("POST", "/admin/embedding/check")
            assert saved["active"] and saved["dimensions"] == 1024
            assert checked["connected"] and checked["dimensions"] == 1024
            print("PASS: administrator activated local BGE, connection check returned 1024 dimensions")
        finally:
            data("POST", "/auth/logout")


if __name__ == "__main__":
    main()
