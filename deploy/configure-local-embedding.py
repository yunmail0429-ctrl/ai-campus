"""Register the bundled local embedding endpoint without printing deployment secrets."""

import json
from pathlib import Path


def main():
    path = Path(__file__).resolve().parent / "backend.env"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("MODEL_PROVIDERS="):
            continue
        providers = json.loads(line.partition("=")[2])
        providers["bge-large-zh-local"] = {
            "label": "本机中文向量模型",
            "base_url": "http://embedding:80/v1",
            "tenant_slugs": ["xflj"],
        }
        lines[index] = "MODEL_PROVIDERS=" + json.dumps(providers, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Local BGE provider registered in backend.env")
        return
    raise RuntimeError("MODEL_PROVIDERS setting is missing")


if __name__ == "__main__":
    main()
