"""Read-only dependency checks through the configured backend container; no secrets printed."""
import json
import subprocess
from pathlib import Path

PROBE = r"""
import json
from redis import Redis
from app.worker import app, settings, QUEUE
from app.storage import ObjectStore
results = {}
def check(name, action):
    try:
        assert action()
        results[name] = {"ok": True}
    except Exception as exc:
        results[name] = {"ok": False, "errorType": type(exc).__name__}
check("redisPing", lambda: Redis.from_url(settings.redis_url.get_secret_value(), socket_connect_timeout=3, socket_timeout=3).ping())
def storage():
    store = ObjectStore(settings)
    store.client.head_bucket(Bucket=store.profile.bucket)
    store.private_bucket()
    return True
check("privateBucket", storage)
def worker():
    inspector = app.control.inspect(timeout=5)
    queues = inspector.active_queues() or {}
    pings = inspector.ping() or {}
    return any(pings.get(node, {}).get("ok") == "pong" and any(q.get("name") == QUEUE for q in subscriptions) for node, subscriptions in queues.items())
check("workerRespondsAndSubscribes", worker)
print(json.dumps(results))
"""

def main():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["docker", "compose", "-f", "compose.app.yaml", "exec", "-T", "backend", "python", "-c", PROBE], cwd=root, capture_output=True, text=True, timeout=90)
    if result.returncode:
        print("Dependency probe could not complete; container output withheld.")
        return 1
    checks = json.loads(result.stdout)
    report = {"scope": "Read-only point-in-time checks; not scheduler heartbeat, task execution or alerting", "checks": checks}
    (root / "docs/DEPENDENCY-PROBE.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if all(item["ok"] for item in checks.values()) else 1

if __name__ == "__main__":
    raise SystemExit(main())
