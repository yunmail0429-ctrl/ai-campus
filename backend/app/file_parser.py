"""Isolated document extraction. No URLs, macros, or embedded programs are executed."""

import io
import json
import re
import sys
import zipfile
from pathlib import Path, PurePath

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def normalize_pdf_text(text):
    """Remove layout spaces inserted between adjacent Chinese characters."""
    return re.sub(r"(?<=[\u3400-\u9fff])[ \u3000]+(?=[\u3400-\u9fff])", "", text)


def chart_text(chart):
    try:
        categories = [str(category.label) for category in chart.plots[0].categories]
    except (AttributeError, IndexError, TypeError, ValueError):
        categories = []
    lines = []
    for series in chart.series:
        values = list(series.values)
        entries = (
            [f"{label} {value}" for label, value in zip(categories, values, strict=True)]
            if len(categories) == len(values)
            else [str(value) for value in values]
        )
        lines.append(f"{series.name}: " + ", ".join(entries))
    return "\n".join(lines)


def extract(content, suffix, details=None):
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        from ocr import recognize_image

        if not (
            (suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix in (".jpg", ".jpeg") and content.startswith(b"\xff\xd8\xff"))
            or (suffix == ".webp" and content.startswith(b"RIFF") and content[8:12] == b"WEBP")
        ):
            raise ValueError("INVALID_FILE")

        try:
            lines = recognize_image(content)
        except ValueError as exc:
            if str(exc) != "OCR_UNAVAILABLE":
                raise
            lines = []
            if details is not None:
                details["ocr_unavailable"] = True
        if sum(len(line["text"]) for line in lines) > MAX_TEXT:
            raise ValueError("PARSE_LIMIT")
        if details is not None:
            details[1] = lines
        return [
            {
                "location": "图片",
                "content": line["text"],
                "structure": {"kind": "ocr_line", "page": 1, "bbox": line["bbox"], "confidence": line["confidence"]},
            }
            for line in lines
        ]
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
            text = normalize_pdf_text(page.extract_text() or "")
            if not text.strip() and list(page.images):
                from ocr import recognize_image, render_pdf_page

                try:
                    lines = recognize_image(render_pdf_page(content, index + 1))
                except ValueError as exc:
                    if str(exc) != "OCR_UNAVAILABLE":
                        raise
                    lines = []
                    if details is not None:
                        details["ocr_unavailable"] = True
                if details is not None:
                    details[index + 1] = lines
                text = "\n".join(line["text"] for line in lines)
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
            from docx.oxml.ns import qn
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            from ocr import recognize_image

            document = Document(io.BytesIO(content))
            ordered = []
            for element in document.element.body.iterchildren():
                if element.tag == qn("w:p"):
                    paragraph = Paragraph(element, document)
                    ordered.append(paragraph.text)
                    for blip in element.xpath(".//a:blip"):
                        reference = blip.get(qn("r:embed"))
                        if reference in document.part.related_parts:
                            try:
                                ordered.extend(
                                    line["text"]
                                    for line in recognize_image(document.part.related_parts[reference].blob)
                                )
                            except ValueError:
                                pass
                elif element.tag == qn("w:tbl"):
                    table = Table(element, document)
                    ordered.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
            text = "\n".join(ordered)
            pages = [("正文与表格", text)]
        else:
            from ocr import recognize_image
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
                    if shape.has_chart:
                        try:
                            texts.append(chart_text(shape.chart))
                        except (AttributeError, ValueError):
                            pass
                    if shape.shape_type == 13:
                        try:
                            texts.extend(line["text"] for line in recognize_image(shape.image.blob))
                        except ValueError:
                            pass
                pages.append((f"第 {index + 1} 张幻灯片", "\n".join(texts)))
    else:
        raise ValueError("UNSUPPORTED_FILE")
    chunks, total = [], 0
    for location, text in pages:
        text = re.sub(r"[\t\r ]+", " ", text).strip()
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError("PARSE_LIMIT")
        for start in range(0, len(text), 350):
            excerpt = text[start : start + 400].strip()
            if excerpt:
                chunks.append({"location": location, "content": excerpt})
    if not chunks and suffix == ".pdf" and any(list(page.images) for page in reader.pages):
        return []
    if not chunks:
        raise ValueError("NO_EXTRACTABLE_TEXT")
    return chunks


def structured_pages(content, suffix, chunks, details=None):
    """Preserve reading order and source location without changing the public chunk API."""
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        blocks = [
            {"kind": "image", "text": item["content"], "source": "ocr",
             "bbox": item["structure"]["bbox"], "confidence": item["structure"]["confidence"],
             "needs_review": True}
            for item in chunks
        ]
        if not blocks:
            blocks = [{"kind": "image", "text": "", "source": "native", "needs_review": True}]
        return [{"label": "图片", "blocks": blocks,
                 "assembled_text": "\n".join(block["text"] for block in blocks if block["text"]),
                 "needs_review": True}]
    if suffix in (".txt", ".md", ".html", ".htm"):
        text = content.decode("utf-8-sig")
        if suffix in (".html", ".htm"):
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            for element in soup(["script", "style", "noscript", "iframe", "object"]):
                element.decompose()
            text = soup.get_text("\n", strip=True)
        text = re.sub(r"[\t\r ]+", " ", text).strip()
        return [{"label": "正文", "blocks": [{"kind": "paragraph", "text": text, "source": "native"}],
                 "assembled_text": text, "needs_review": False}]
    if suffix == ".pdf":
        from ocr import recognize_image, render_pdf_page
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        result = []
        for index, source_page in enumerate(reader.pages):
            text = normalize_pdf_text(source_page.extract_text() or "")
            has_images = bool(list(source_page.images))
            if text.strip():
                blocks = [{"kind": "paragraph", "text": text, "source": "native"}]
            elif has_images:
                lines = (details or {}).get(index + 1)
                if lines is None:
                    lines = recognize_image(render_pdf_page(content, index + 1))
                blocks = [
                    {"kind": "paragraph", "text": line["text"], "source": "ocr",
                     "bbox": line["bbox"], "confidence": line["confidence"]}
                    for line in lines
                ]
            else:
                blocks = []
            if has_images:
                blocks.append({"kind": "image", "text": "", "source": "native", "needs_review": True})
            result.append({
                "label": f"第 {index + 1} 页", "blocks": blocks,
                "assembled_text": "\n".join(block["text"] for block in blocks if block["text"]),
                "needs_review": has_images,
            })
        return result
    if suffix == ".docx":
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from ocr import recognize_image

        document = Document(io.BytesIO(content))
        blocks = []
        for element in document.element.body.iterchildren():
            if element.tag == qn("w:p"):
                paragraph = Paragraph(element, document)
                if paragraph.text.strip():
                    blocks.append({"kind": "paragraph", "text": paragraph.text, "source": "native"})
                for blip in element.xpath(".//a:blip"):
                    reference = blip.get(qn("r:embed"))
                    lines = []
                    if reference in document.part.related_parts:
                        try:
                            lines = recognize_image(document.part.related_parts[reference].blob)
                        except ValueError:
                            pass
                    blocks.append({
                        "kind": "image", "text": "\n".join(line["text"] for line in lines),
                        "source": "ocr" if lines else "native", "needs_review": True,
                    })
            elif element.tag == qn("w:tbl"):
                table = Table(element, document)
                cells = [[cell.text for cell in row.cells] for row in table.rows]
                groups = {}
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row.cells):
                        groups.setdefault(id(cell._tc), []).append((row_index, column_index))
                merges = [
                    {"row": min(row for row, _ in positions),
                     "column": min(column for _, column in positions),
                     "rowSpan": max(row for row, _ in positions) - min(row for row, _ in positions) + 1,
                     "colSpan": max(column for _, column in positions) - min(column for _, column in positions) + 1}
                    for positions in groups.values() if len(positions) > 1
                ]
                blocks.append(
                    {
                        "kind": "table",
                        "text": "\n".join(" | ".join(row) for row in cells),
                        "cells": cells,
                        "merges": merges,
                        "source": "native",
                    }
                )
        return [
            {
                "label": "正文与表格",
                "blocks": blocks,
                "assembled_text": "\n".join(block["text"] for block in blocks if block["text"]),
                "needs_review": any(block.get("needs_review") for block in blocks),
            }
        ]
    if suffix == ".pptx":
        from ocr import recognize_image
        from pptx import Presentation

        presentation = Presentation(io.BytesIO(content))
        result = []
        for index, slide in enumerate(presentation.slides):
            blocks = []
            for shape in slide.shapes:
                bbox = [int(shape.left), int(shape.top), int(shape.left + shape.width), int(shape.top + shape.height)]
                if shape.has_text_frame and shape.text.strip():
                    blocks.append({"kind": "paragraph", "text": shape.text, "source": "native", "bbox": bbox})
                if shape.has_table:
                    cells = [[cell.text for cell in row.cells] for row in shape.table.rows]
                    merges = [
                        {"row": row_index, "column": column_index,
                         "rowSpan": cell.span_height, "colSpan": cell.span_width}
                        for row_index, row in enumerate(shape.table.rows)
                        for column_index, cell in enumerate(row.cells)
                        if cell.is_merge_origin
                    ]
                    blocks.append({
                        "kind": "table", "cells": cells, "merges": merges,
                        "text": "\n".join(" | ".join(row) for row in cells),
                        "source": "native", "bbox": bbox,
                    })
                if shape.has_chart:
                    try:
                        chart = shape.chart
                        chart_content = chart_text(chart)
                        blocks.append({
                            "kind": "chart", "text": chart_content, "source": "native",
                            "bbox": bbox, "needs_review": not bool(chart_content),
                        })
                    except (AttributeError, ValueError):
                        blocks.append({"kind": "chart", "text": "", "source": "native", "bbox": bbox,
                                       "needs_review": True})
                if shape.shape_type == 13:
                    try:
                        lines = recognize_image(shape.image.blob)
                    except ValueError:
                        lines = []
                    blocks.append({"kind": "image", "text": "\n".join(line["text"] for line in lines),
                                   "source": "ocr" if lines else "native", "bbox": bbox, "needs_review": True})
            blocks.sort(key=lambda block: (block.get("bbox", [0, 0])[1], block.get("bbox", [0, 0])[0]))
            result.append({
                "label": f"第 {index + 1} 张幻灯片", "blocks": blocks,
                "assembled_text": "\n".join(block["text"] for block in blocks if block["text"]),
                "needs_review": any(block.get("needs_review") for block in blocks),
            })
        return result
    grouped = {}
    for chunk in chunks:
        location = chunk["location"]
        detail = chunk.get("structure", {})
        grouped.setdefault(location, []).append(
            {
                "kind": detail.get("kind", "paragraph"),
                "text": chunk["content"],
                "source": "ocr" if detail else "native",
                **({"bbox": detail["bbox"]} if "bbox" in detail else {}),
                **({"confidence": detail["confidence"]} if "confidence" in detail else {}),
            }
        )
    result = [
        {
            "label": location,
            "blocks": blocks,
            "assembled_text": "\n".join(block["text"] for block in blocks),
            "needs_review": any(block.get("confidence", 1) < 0.75 for block in blocks),
        }
        for location, blocks in grouped.items()
    ]
    return result


def semantic_chunks(pages):
    """Keep paragraph/table boundaries, repeating table headers after a split."""
    result = []
    for page in pages:
        buffer = ""

        def flush(label=page["label"]):
            nonlocal buffer
            if buffer.strip():
                result.append({"location": label, "content": buffer.strip()})
            buffer = ""

        def add_piece(piece):
            nonlocal buffer
            piece = piece.strip()
            if not piece:
                return
            if len(buffer) + len(piece) + 1 > 400:
                flush()
            buffer = f"{buffer}\n{piece}".strip()

        for block in page["blocks"]:
            content = block.get("text", "").strip()
            if block.get("kind") == "table" and block.get("cells"):
                rows = [" | ".join(row) for row in block["cells"]]
                header = rows[0]
                part = header
                for row in rows[1:]:
                    if len(part) + len(row) + 1 > 350 and part != header:
                        add_piece(part)
                        part = header
                    part += "\n" + row
                for start in range(0, len(part), 350):
                    add_piece(part[start : start + 350])
            elif content:
                for start in range(0, len(content), 350):
                    add_piece(content[start : start + 350])
        flush()
    return result


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (55, 55))
    try:
        content = sys.stdin.buffer.read(100 * 1024 * 1024 + 1)
        suffix = PurePath(sys.argv[1]).suffix.lower()
        details = {}
        chunks = extract(content, suffix, details)
        pages = structured_pages(content, suffix, chunks, details)
        if suffix in (".docx", ".pptx"):
            chunks = semantic_chunks(pages)
        result = {"chunks": [{k: v for k, v in chunk.items() if k != "structure"} for chunk in chunks],
                  "pages": pages,
                  "ocr_unavailable": details.get("ocr_unavailable", False)}
    except Exception as exc:  # noqa: BLE001 - isolated process must sanitize parser errors
        code = str(exc) if isinstance(exc, ValueError) else "INVALID_FILE"
        if code not in {
            "OCR_REQUIRED",
            "OCR_UNAVAILABLE",
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
