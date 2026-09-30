"""Real provider artifact validation; does not change school settings or teacher balances."""

import asyncio
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pptx import Presentation

from app.config import Settings
from app.ppt_generation import PptAdapter
from app.resource_contracts import ResourceInput
from app.resource_execution import extension


async def main():
    settings = Settings(_env_file=ROOT / "deploy/backend.env")
    config = dict(provider="mimo", model="mimo-v2.5", revision=0, contextTokens=32000,
                  maxOutputTokens=8192, timeoutSeconds=120, inputPrice=1000, outputPrice=1000)
    adapter = PptAdapter(settings, config)
    data = ResourceInput(kind="ppt", prompt="高中数学函数单调性：图像观察、定义证明、课堂练习。",
                         options={"pages": 3, "style": "简洁教学"})
    try:
        plan = adapter.prepare(data)
        result = await adapter.execute(data, plan.snapshot)
    except Exception as error:
        print(json.dumps({"status": "failed", "type": type(error).__name__,
                          "causeType": type(error.__cause__).__name__}, ensure_ascii=False))
        return 1
    assert extension("ppt", result.artifact) == "pptx"
    deck = Presentation(io.BytesIO(result.artifact.content))
    assert len(deck.slides) == 3
    output = ROOT / "local-data/mimo-ppt-validation.pptx"
    output.write_bytes(result.artifact.content)
    report = {"status": "passed", "pages": len(deck.slides), "bytes": len(result.artifact.content),
              "testEstimatedCredits": plan.maxCredits, "testCalculatedCredits": result.credits,
              "teacherCharged": False, "visualReview": "pending",
              "titles": [slide.shapes[0].text for slide in deck.slides]}
    (ROOT / "docs/PPT-PROVIDER-TEST.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
