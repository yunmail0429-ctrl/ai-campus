"""One-off deployed teacher call check; never logs secrets or response text."""

import os
import uuid

import httpx


def main():
    origin = "http://localhost:3100"
    base_url = os.getenv("DEPLOY_BASE_URL", origin)
    with httpx.Client(base_url=base_url + "/api/v1", headers={"Origin": origin}, timeout=180, trust_env=False) as client:
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()["data"]

        data("POST", "/auth/login", json={"username": "teacher", "password": "123"})
        before = data("GET", "/me/quota")
        conversation = data("POST", "/conversations", json={"title": "Teacher live model acceptance"})["id"]
        try:
            key = str(uuid.uuid4())
            def send():
                with client.stream(
                    "POST",
                    f"/conversations/{conversation}/messages",
                    json={"content": "Please reply with one short sentence.", "useKnowledge": True},
                    headers={"Idempotency-Key": key},
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"Teacher model call: HTTP {response.status_code}")
                    return [line for line in response.iter_lines() if line.startswith("event:")]

            events = send()
            messages = data("GET", f"/conversations/{conversation}/messages")["items"]
            after = data("GET", "/me/quota")
            print("messages", [(m["role"], m["status"], len(m["content"])) for m in messages])
            print("quota_delta", after["totalConsumed"] - before["totalConsumed"])
            print("balance_delta", after["balance"] - before["balance"])
            assert "event: message.completed" in events
            assert any(m["role"] == "assistant" and m["status"] == "completed" and m["content"] for m in messages)
            assert after["totalConsumed"] > before["totalConsumed"]
            assert after["balance"] < before["balance"]
            replay_events = send()
            replay_messages = data("GET", f"/conversations/{conversation}/messages")["items"]
            replay_quota = data("GET", "/me/quota")
            assert "event: message.completed" in replay_events
            assert len(replay_messages) == len(messages)
            assert replay_quota["totalConsumed"] == after["totalConsumed"]
            assert replay_quota["balance"] == after["balance"]
            print("replay_no_duplicate_messages_or_charges", True)
        finally:
            data("DELETE", f"/conversations/{conversation}")
            data("POST", "/auth/logout")


if __name__ == "__main__":
    main()
