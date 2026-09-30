"""Rebuild bundled PDF fonts from a pinned OFL upstream; never runs during export."""

import hashlib
import io
import json
from pathlib import Path

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

REVISION = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
BASE = f"https://raw.githubusercontent.com/notofonts/noto-cjk/{REVISION}/Sans"
SOURCE = BASE + "/Variable/TTF/Subset/NotoSansSC-VF.ttf"
LATIN_REVISION = "ffebf8c1ee449e544955a7e813c54f9b73848eac"
LATIN_BASE = f"https://raw.githubusercontent.com/notofonts/noto-fonts/{LATIN_REVISION}"


def main():
    root = Path(__file__).resolve().parents[1] / "app" / "assets" / "fonts"
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        font = client.get(SOURCE)
        font.raise_for_status()
        license = client.get(BASE + "/LICENSE")
        license.raise_for_status()
    (root / "OFL.txt").write_text(license.text, encoding="utf-8")
    outputs = {}
    for weight, style in ((400, "Regular"), (700, "Bold")):
        source = TTFont(io.BytesIO(font.content))
        built = instantiateVariableFont(source, {"wght": weight}, inplace=True)
        names = {
            1: "Campus Export Sans",
            2: style,
            3: f"CampusExportSans-{style}",
            4: f"Campus Export Sans {style}",
            6: f"CampusExportSans-{style}",
            16: "Campus Export Sans",
            17: style,
        }
        for record in built["name"].names:
            if record.nameID in names:
                record.string = names[record.nameID].encode(record.getEncoding())
        target = root / f"CampusExportSans-{style}.ttf"
        built.save(target)
        outputs[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"Built {style}: {target.stat().st_size} bytes", flush=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for style in ("Regular", "Bold"):
            name = f"NotoSans-{style}.ttf"
            response = client.get(f"{LATIN_BASE}/hinted/ttf/NotoSans/{name}")
            response.raise_for_status()
            (root / name).write_bytes(response.content)
            outputs[name] = hashlib.sha256(response.content).hexdigest()
        response = client.get(f"{LATIN_BASE}/LICENSE")
        response.raise_for_status()
        (root / "NotoSans-LICENSE.txt").write_text(response.text, encoding="utf-8")
    (root / "provenance.json").write_text(
        json.dumps(
            {
                "source": SOURCE,
                "latinSource": LATIN_BASE + "/hinted/ttf/NotoSans/",
                "revision": REVISION,
                "sourceSha256": hashlib.sha256(font.content).hexdigest(),
                "license": "SIL Open Font License 1.1",
                "modifications": "Static 400/700 instances; renamed Campus Export Sans",
                "files": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
