"""Bounded CPU OCR for scanned pages and uploaded images."""

import csv
import io
import subprocess

from PIL import Image, ImageOps, UnidentifiedImageError


def recognize_image(content: bytes, *, timeout: int = 30) -> list[dict]:
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("PARSE_LIMIT")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("PARSE_LIMIT")
            normalized = ImageOps.exif_transpose(image)
            output = io.BytesIO()
            normalized.save(output, format="PNG")
            content = output.getvalue()
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("PARSE_LIMIT")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("INVALID_FILE") from None
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "tsv"],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("OCR_UNAVAILABLE") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("OCR_UNAVAILABLE")
    lines: dict[tuple[str, str, str, str], list[dict]] = {}
    try:
        for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
            if row["level"] != "5" or not row["text"].strip():
                continue
            confidence = float(row["conf"])
            if confidence < 0:
                continue
            group = tuple(row[field] for field in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(group, []).append(
                {
                    "text": row["text"].strip(),
                    "confidence": confidence / 100,
                    "box": [int(row[field]) for field in ("left", "top", "width", "height")],
                }
            )
    except (KeyError, ValueError, UnicodeError):
        raise ValueError("OCR_UNAVAILABLE") from None
    blocks = []
    for words in lines.values():
        left = min(word["box"][0] for word in words)
        top = min(word["box"][1] for word in words)
        right = max(word["box"][0] + word["box"][2] for word in words)
        bottom = max(word["box"][1] + word["box"][3] for word in words)
        blocks.append(
            {
                "text": " ".join(word["text"] for word in words),
                "confidence": round(sum(word["confidence"] for word in words) / len(words), 3),
                "bbox": [left, top, right, bottom],
            }
        )
    return blocks


def render_pdf_page(content: bytes, page: int, *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-png", "-scale-to", "1800", "-"],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("OCR_UNAVAILABLE") from None
    if result.returncode or not result.stdout or len(result.stdout) > 20 * 1024 * 1024:
        raise ValueError("OCR_UNAVAILABLE")
    return result.stdout
