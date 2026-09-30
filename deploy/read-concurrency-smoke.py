"""Bounded authenticated read concurrency, not a production capacity certification."""

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx


async def main():
    origin = "http://localhost:3100"
    paths = ["/me", "/me/quota", "/conversations", "/skills", "/resource-tasks"]
    records = []
    async with httpx.AsyncClient(base_url=origin + "/api/v1", headers={"Origin": origin},
                                trust_env=False, timeout=20) as client:
        response = await client.post("/auth/login", json={"username": "teacher", "password": os.environ["DEPLOY_TEACHER_PASSWORD"]})
        assert response.status_code == 200
        before_quota = (await client.get("/me/quota")).json()["data"]
        limit = asyncio.Semaphore(8)

        async def read(index):
            async with limit:
                path = paths[index % len(paths)]
                started = time.perf_counter()
                try:
                    result = await client.get(path)
                    passed = result.status_code == 200 and result.json().get("code") == "OK"
                    status = result.status_code
                except (httpx.HTTPError, ValueError):
                    passed, status = False, 0
                records.append({"path": path, "status": status, "passed": passed,
                                "milliseconds": round((time.perf_counter() - started) * 1000, 2)})

        await asyncio.gather(*(read(index) for index in range(100)))
        after_quota = (await client.get("/me/quota")).json()["data"]
        assert after_quota == before_quota
        assert (await client.post("/auth/logout")).status_code == 200
    durations = sorted(row["milliseconds"] for row in records)
    report = {"requests": len(records), "concurrency": 8,
              "failed": sum(not row["passed"] for row in records),
              "medianMs": statistics.median(durations), "p95Ms": durations[94], "maxMs": max(durations),
              "teacherQuotaUnchanged": True,
              "scope": "Localhost, one teacher, authenticated reads only; not a sustained capacity/load test",
              "records": records}
    Path("docs/READ-CONCURRENCY-TEST.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}))
    return bool(report["failed"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
