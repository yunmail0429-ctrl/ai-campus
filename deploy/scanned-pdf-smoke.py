"""Verify an image-only PDF is OCR-parsed without teacher credit charges."""

import io
import os
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image


def main():
    password = os.environ.get("DEPLOY_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set DEPLOY_TEST_PASSWORD to the current teacher password.")
    root = Path(__file__).resolve().parents[1]
    image_path = next((root / "local-data/mimo-ppt-task-review").glob("*1.PNG"))
    with Image.open(image_path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PDF")
    pdf = buffer.getvalue()
    origin = "http://localhost:3100"
    with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, timeout=30, trust_env=False) as client:
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()["data"]

        data("POST", "/auth/login", json={"username": "teacher", "password": password})
        before = data("GET", "/me/quota")
        conversation = data("POST", "/conversations", json={"title": "Scanned PDF OCR acceptance"})["id"]
        try:
            document = data(
                "POST", f"/conversations/{conversation}/attachments",
                files={"file": ("scanned-teaching-slide.pdf", pdf, "application/pdf")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )["id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                rows = data("GET", f"/conversations/{conversation}/attachments")["items"]
                row = next(item for item in rows if item["id"] == document)
                if row["status"] in ("failed", "ready", "parsed", "quarantined"):
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Scanned PDF processing timed out")
            assert row["status"] in ("parsed", "ready"), row
            chunks = data("GET", f"/documents/{document}/chunks")["items"]
            extracted = " ".join(chunk["content"] for chunk in chunks)
            assert len(extracted.strip()) >= 20
            after = data("GET", "/me/quota")
            assert before == after
            print("PASS: scanned PDF OCR parsed, no teacher credit change", len(pdf), len(extracted))
        finally:
            data("DELETE", f"/conversations/{conversation}")
            data("POST", "/auth/logout")


if __name__ == "__main__":
    main()
