"""Run a real PPT task using the administrator's current default model and teacher quota."""

import io
import json
import os
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from pptx import Presentation

ORIGIN = "http://localhost:3100"


def main():
    password = os.environ.get("DEPLOY_TEACHER_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEACHER_PASSWORD before running.")
    with httpx.Client(
        base_url=ORIGIN + "/api/v1", headers={"Origin": ORIGIN}, timeout=45, trust_env=False
    ) as client:

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()["data"]

        call("POST", "/auth/login", json={"username": "teacher", "password": password})
        capabilities = {row["kind"]: row for row in call("GET", "/resource-tasks/capabilities")}
        assert capabilities["ppt"]["available"], "PPT capability is unavailable"
        before = call("GET", "/me/quota")
        key = str(uuid.uuid4())
        pages = int(os.getenv("DEPLOY_PPT_PAGES", "2"))
        prompt = os.getenv(
            "DEPLOY_PPT_PROMPT",
            "高一数学函数单调性：概念、例题、课堂练习与答案。",
        )
        payload = {
            "kind": "ppt",
            "prompt": prompt,
            "options": {"pages": pages, "style": "简洁教学"},
        }
        task = call("POST", "/resource-tasks", json=payload, headers={"Idempotency-Key": key})
        path = f"/resource-tasks/{task['id']}"
        try:
            deadline = time.monotonic() + 210
            while time.monotonic() < deadline:
                task = call("GET", path)
                if task["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(2)
            assert task["status"] == "succeeded", task.get("errorCode") or task["status"]
            response = client.get(path + "/download", follow_redirects=False)
            assert response.status_code == 303
            with httpx.Client(timeout=45, trust_env=False) as download:
                artifact = download.get(response.headers["location"])
            assert artifact.status_code == 200
            with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
                names = archive.namelist()
                assert archive.testzip() is None
                assert "[Content_Types].xml" in names
                slides = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            assert len(slides) == pages
            presentation = Presentation(io.BytesIO(artifact.content))
            assert len(presentation.slides) == pages
            assert all(any(shape.has_text_frame and shape.text.strip() for shape in slide.shapes)
                       for slide in presentation.slides)
            after = call("GET", "/me/quota")
            assert after["totalConsumed"] - before["totalConsumed"] == task["credits"] > 0
            assert before["balance"] - after["balance"] == task["credits"]
            assert after["reserved"] == before["reserved"]
            replay = call("POST", "/resource-tasks", json=payload, headers={"Idempotency-Key": key})
            assert replay["id"] == task["id"]
            assert call("GET", "/me/quota") == after
            suffix = "multisubject" if os.getenv("DEPLOY_PPT_PROMPT") else "current-model"
            Path(f"local-data/alpine-{suffix}-validation.pptx").write_bytes(artifact.content)
            report = {
                "taskId": task["id"],
                "status": task["status"],
                "pages": len(slides),
                "bytes": len(artifact.content),
                "credits": task["credits"],
                "reservedRestored": True,
                "replayNoDuplicateCharge": True,
            }
            Path(f"docs/PPT-{suffix.upper()}-TEST.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report))
        finally:
            if task["status"] in ("queued", "running"):
                call("POST", path + "/cancel")
            call("POST", "/auth/logout")


if __name__ == "__main__":
    main()
