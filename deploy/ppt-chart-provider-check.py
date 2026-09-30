"""Verify that the real text model emits an editable function chart deck."""

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pptx import Presentation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.ppt_generation import PptAdapter
from app.resource_contracts import ResourceInput


async def main():
    settings = Settings(_env_file=ROOT / "deploy/backend.env")
    config = {"provider": "mimo", "model": "mimo-v2.5", "revision": 0,
              "contextTokens": 32000, "maxOutputTokens": 8192, "timeoutSeconds": 120,
              "inputPrice": 1000, "outputPrice": 1000}
    adapter = PptAdapter(settings, config)
    data = ResourceInput(
        kind="ppt",
        prompt="生成高中数学二次函数 y=x² 的 2 页课件。第一页解释最低点与对称轴，第二页必须绘制 y=x² 的可编辑数值曲线图，包含至少 9 个准确采样点、x/y 坐标轴与观察问题。",
        options={"pages": 2, "style": "简洁教学"},
    )
    plan = adapter.prepare(data)
    result = await adapter.execute(data, plan.snapshot)
    deck = Presentation(io.BytesIO(result.artifact.content))
    charts = [shape.chart for slide in deck.slides for shape in slide.shapes if shape.has_chart]
    assert len(deck.slides) == 2 and charts
    assert all(len(series.points) >= 9 for chart in charts for series in chart.series)
    with zipfile.ZipFile(io.BytesIO(result.artifact.content)) as archive:
        chart_name = next(name for name in archive.namelist()
                          if name.startswith("ppt/charts/chart") and name.endswith(".xml"))
        root = ElementTree.fromstring(archive.read(chart_name))
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
    x_values = [float(node.text) for node in root.findall(
        ".//c:xVal/c:numRef/c:numCache/c:pt/c:v", ns
    )]
    y_values = [float(node.text) for node in root.findall(
        ".//c:yVal/c:numRef/c:numCache/c:pt/c:v", ns
    )]
    assert len(x_values) == len(y_values) >= 9
    max_error = max(abs(y - x * x) for x, y in zip(x_values, y_values))
    assert max_error < 0.001
    output = ROOT / "local-data/mimo-ppt-chart-validation.pptx"
    output.write_bytes(result.artifact.content)
    report = {"status": "passed", "pages": len(deck.slides), "editableCharts": len(charts),
              "chartSeriesPoints": [len(series.points) for chart in charts for series in chart.series],
              "maxQuadraticValueError": max_error,
              "teacherCharged": False, "visualReview": "pending"}
    (ROOT / "docs/PPT-CHART-PROVIDER-TEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
