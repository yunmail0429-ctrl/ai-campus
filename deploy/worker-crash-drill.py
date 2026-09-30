"""Verify Docker restarts the local worker after its main process exits."""

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "compose.app.yaml"]


def run(*args, timeout=30):
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        check=False,
    )


def state(container):
    response = run("docker", "inspect", "--format", "{{json .State}}", container)
    if response.returncode:
        raise RuntimeError("Could not inspect worker")
    return json.loads(response.stdout)


def main():
    container = run(*COMPOSE, "ps", "-q", "worker").stdout.strip()
    if not container:
        raise RuntimeError("Worker container is missing")
    before = state(container)
    assert before["Running"] and before["Health"]["Status"] == "healthy"
    before_count_result = run("docker", "inspect", "--format", "{{.RestartCount}}", container)
    if before_count_result.returncode:
        raise RuntimeError("Could not inspect initial restart count")
    before_count = int(before_count_result.stdout.strip())
    hostname = run(*COMPOSE, "exec", "-T", "worker", "hostname").stdout.strip()
    if not hostname:
        raise RuntimeError("Could not identify worker")
    # Celery closes its own main process. A timeout waiting for its reply is
    # expected because the recipient exits; the restart count is authoritative.
    run(*COMPOSE, "exec", "-T", "backend", "celery", "-A", "app.worker", "control", "shutdown",
        "--destination", f"celery@{hostname}", "--timeout", "5", timeout=20)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = state(container)
            restart = run("docker", "inspect", "--format", "{{.RestartCount}}", container)
            if restart.returncode:
                raise RuntimeError("Could not inspect worker restart count")
            restart_count = int(restart.stdout.strip())
            if restart_count > before_count and current["Running"] and current["Health"]["Status"] == "healthy":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Worker did not automatically become healthy")
        probe = run("py", "-3", "deploy/check-dependencies.py", timeout=90)
        assert probe.returncode == 0
        report = json.loads((ROOT / "docs/DEPENDENCY-PROBE.json").read_text(encoding="utf-8"))
        assert all(item["ok"] for item in report["checks"].values())
        result = {"restartCountBefore": before_count, "restartCountAfter": restart_count,
                  "health": current["Health"]["Status"], "checks": report["checks"]}
        (ROOT / "docs/WORKER-CRASH-DRILL.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("PASS: automatic worker restart", json.dumps(result))
    finally:
        current = state(container)
        if not current["Running"] or current["Health"]["Status"] != "healthy":
            recovery = run(*COMPOSE, "up", "-d", "worker", timeout=120)
            if recovery.returncode:
                raise RuntimeError("Worker recovery failed; inspect docker compose ps")


if __name__ == "__main__":
    main()
