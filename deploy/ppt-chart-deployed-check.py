"""Run a deployed teacher PPT chart through the worker, download, and credit ledger."""

import io
import json
import os
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import httpx
from pptx import Presentation

ORIGIN = "http://localhost:3100"
ROOT = Path(__file__).resolve().parents[1]


def main():
    password = os.environ.get("DEPLOY_TEACHER_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEACHER_PASSWORD before running.")
    with httpx.Client(base_url=ORIGIN + "/api/v1", headers={"Origin": ORIGIN},
                      timeout=45, trust_env=False) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()["data"]

        call("POST", "/auth/login", json={"username": "teacher", "password": password})
        task = None
        try:
            before = call("GET", "/me/quota")
            key = str(uuid.uuid4())
            payload = {
                "kind": "ppt",
                "prompt": "生成高中数学二次函数 y=x² 的 2 页课件。第一页解释最低点与对称轴，第二页必须绘制 y=x² 的可编辑数值曲线图，包含至少 9 个准确采样点、x/y 坐标轴与观察问题。",
                "options": {"pages": 2, "style": "简洁教学"},
            }
            task = call("POST", "/resource-tasks", json=payload,
                        headers={"Idempotency-Key": key})
            path = f"/resource-tasks/{task['id']}"
            deadline = time.monotonic() + 210
            while time.monotonic() < deadline:
                task = call("GET", path)
                if task["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(2)
            assert task["status"] == "succeeded", task.get("errorCode") or task["status"]
            redirect = client.get(path + "/download", follow_redirects=False)
            assert redirect.status_code == 303
            with httpx.Client(timeout=45, trust_env=False) as download:
                artifact = download.get(redirect.headers["location"])
            assert artifact.status_code == 200
            presentation = Presentation(io.BytesIO(artifact.content))
            charts = [shape.chart for slide in presentation.slides
                      for shape in slide.shapes if shape.has_chart]
            assert len(presentation.slides) == 2 and charts
            with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
                assert archive.testzip() is None
                chart_name = next(name for name in archive.namelist()
                                  if name.startswith("ppt/charts/chart") and name.endswith(".xml"))
                root = ElementTree.fromstring(archive.read(chart_name))
            ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
            xs = [float(node.text) for node in root.findall(
                ".//c:xVal/c:numRef/c:numCache/c:pt/c:v", ns
            )]
            ys = [float(node.text) for node in root.findall(
                ".//c:yVal/c:numRef/c:numCache/c:pt/c:v", ns
            )]
            assert len(xs) == len(ys) >= 9
            max_error = max(abs(y - x * x) for x, y in zip(xs, ys))
            assert max_error < 0.001
            after = call("GET", "/me/quota")
            assert after["totalConsumed"] - before["totalConsumed"] == task["credits"] > 0
            assert before["balance"] - after["balance"] == task["credits"]
            assert after["reserved"] == before["reserved"]
            replay = call("POST", "/resource-tasks", json=payload,
                          headers={"Idempotency-Key": key})
            assert replay["id"] == task["id"]
            assert call("GET", "/me/quota") == after
            (ROOT / "local-data/deployed-ppt-chart-validation.pptx").write_bytes(artifact.content)
            report = {"status": "passed", "taskId": task["id"],
                      "pages": len(presentation.slides), "editableCharts": len(charts),
                      "chartPoints": len(xs), "maxQuadraticValueError": max_error,
                      "credits": task["credits"], "replayNoDuplicateCharge": True}
            (ROOT / "docs/PPT-CHART-DEPLOYED-TEST.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report))
        finally:
            if task and task["status"] in ("queued", "running"):
                call("POST", f"/resource-tasks/{task['id']}/cancel")
            call("POST", "/auth/logout")


if __name__ == "__main__":
    main()
