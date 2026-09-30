"""Generate one real MiMo lesson storyboard without changing school data or credits."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.resource_contracts import ResourceInput, adapter_for
from app.resource_execution import extension


async def main():
    settings = Settings(_env_file=ROOT / "deploy/backend.env")
    config = {
        "provider": "mimo", "model": "mimo-v2.5", "revision": 0,
        "contextTokens": 32000, "maxOutputTokens": 4096,
        "timeoutSeconds": 120, "inputPrice": 0, "outputPrice": 0,
    }
    data = ResourceInput(kind="video", prompt="给我生成高中赤壁赋课文的讲课视频", options={"style": "清晰教学"})
    adapter = adapter_for(settings, "video", config)
    result = await adapter.execute(data, adapter.prepare(data).snapshot)
    assert extension("video", result.artifact) == "md"
    path = ROOT / "local-data/赤壁赋-小米MiMo-分镜脚本.md"
    path.write_bytes(result.artifact.content)
    print(f"saved={path} bytes={len(result.artifact.content)}")


if __name__ == "__main__":
    asyncio.run(main())
