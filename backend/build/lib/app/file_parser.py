"""Isolated document extraction. No URLs, macros, or embedded programs are executed."""

import io
import json
import re
import sys
import zipfile
from pathlib import PurePath

MAX_TEXT = 500_000
TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def extract(content, suffix):
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("OCR_REQUIRED")
    pages = []
    if suffix in (".txt", ".md", ".html", ".htm"):
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("INVALID_FILE")
        if suffix in (".html", ".htm"):
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            for element in soup(["script", "style", "noscript", "iframe", "object"]):
                element.decompose()
            text = soup.get_text("\n", strip=True)
        pages = [("正文", text)]
    elif suffix == ".pdf":
        from pypdf import PdfReader

        if not content.startswith(b"%PDF-"):
            raise ValueError("INVALID_FILE")
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("ENCRYPTED_FILE")
        if len(reader.pages) > 500:
            raise ValueError("PARSE_LIMIT")
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            # Do not silently claim that an image-only page was read.
            if not text.strip() and list(page.images):
                raise ValueError("OCR_REQUIRED")
            pages.append((f"第 {index + 1} 页", text))
    elif suffix in (".docx", ".pptx"):
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            entries = package.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 50 * 1024 * 1024:
                raise ValueError("PARSE_LIMIT")
            if any(e.flag_bits & 1 or "vbaproject" in e.filename.lower() for e in entries):
                raise ValueError("INVALID_FILE")
        if suffix == ".docx":
            from docx import Document

            document = Document(io.BytesIO(content))
            text = "\n".join(p.text for p in document.paragraphs)
            for table in document.tables:
                text += "\n" + "\n".join(" | ".join(cell.text for cell in row.cells) for row in table.rows)
            pages = [("正文与表格", text)]
        else:
            from pptx import Presentation

            presentation = Presentation(io.BytesIO(content))
            if len(presentation.slides) > 500:
                raise ValueError("PARSE_LIMIT")
            for index, slide in enumerate(presentation.slides):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text)
                    if shape.has_table:
                        texts.extend(" | ".join(c.text for c in r.cells) for r in shape.table.rows)
                pages.append((f"第 {index + 1} 张幻灯片", "\n".join(texts)))
    else:
        raise ValueError("UNSUPPORTED_FILE")
    chunks, total = [], 0
    for location, text in pages:
        text = re.sub(r"[\t\r ]+", " ", text).strip()
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError("PARSE_LIMIT")
        for start in range(0, len(text), 1000):
            excerpt = text[start : start + 1200].strip()
            if excerpt:
                chunks.append({"location": location, "content": excerpt})
    if not chunks:
        raise ValueError("NO_EXTRACTABLE_TEXT")
    return chunks


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (55, 55))
    try:
        content = sys.stdin.buffer.read(100 * 1024 * 1024 + 1)
        result = {"chunks": extract(content, PurePath(sys.argv[1]).suffix.lower())}
    except Exception as exc:  # noqa: BLE001 - isolated process must sanitize parser errors
        code = str(exc) if isinstance(exc, ValueError) else "INVALID_FILE"
        if code not in {
            "OCR_REQUIRED",
            "INVALID_FILE",
            "ENCRYPTED_FILE",
            "PARSE_LIMIT",
            "UNSUPPORTED_FILE",
            "NO_EXTRACTABLE_TEXT",
        }:
            code = "INVALID_FILE"
        result = {"error": code}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
