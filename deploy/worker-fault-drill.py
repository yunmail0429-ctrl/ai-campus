"""Briefly stop the local worker, verify detection, and always restart it."""

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "compose.app.yaml"]


def run(*args, timeout=90):
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        check=False,
    )


def probe():
    result = run("py", "-3", "deploy/check-dependencies.py")
    report = json.loads((ROOT / "docs/DEPENDENCY-PROBE.json").read_text(encoding="utf-8"))
    return result.returncode, report["checks"]


def main():
    baseline_code, baseline = probe()
    assert baseline_code == 0 and all(item["ok"] for item in baseline.values())
    print("baseline", {key: value["ok"] for key, value in baseline.items()})
    stopped = run(*COMPOSE, "stop", "worker", timeout=60)
    if stopped.returncode:
        raise RuntimeError("Could not stop the worker for the drill")
    try:
        failure_code, failure = probe()
        print("stopped", {key: value["ok"] for key, value in failure.items()})
        assert failure_code != 0
        assert not failure["workerRespondsAndSubscribes"]["ok"]
        assert failure["redisPing"]["ok"] and failure["privateBucket"]["ok"]
    finally:
        started = run(*COMPOSE, "start", "worker", timeout=90)
        if started.returncode:
            raise RuntimeError("Worker restart failed; inspect docker compose ps")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = run(*COMPOSE, "ps", "--format", "json", "worker")
        if status.returncode == 0:
            rows = [json.loads(line) for line in status.stdout.splitlines() if line.strip()]
            if any(row.get("Health") == "healthy" for row in rows):
                break
        time.sleep(3)
    else:
        raise RuntimeError("Worker did not become healthy after restart")
    recovered_code, recovered = probe()
    print("recovered", {key: value["ok"] for key, value in recovered.items()})
    assert recovered_code == 0 and all(item["ok"] for item in recovered.values())
    result = {"baseline": baseline, "stopped": failure, "recovered": recovered}
    (ROOT / "docs/WORKER-FAULT-DRILL.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("PASS: worker failure detected and restart recovered")


if __name__ == "__main__":
    main()
