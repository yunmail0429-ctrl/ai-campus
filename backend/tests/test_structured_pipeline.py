import asyncio
import io
from types import SimpleNamespace

import httpx
from PIL import Image

from app.file_parser import normalize_pdf_text, semantic_chunks, structured_pages
from app.ingestion import ParsedChunks, bound_chunks
from app.ocr import recognize_image
from app.reranking import rerank
from app.visual_extraction import enrich


def test_chinese_chunks_fit_embedding_limit_without_losing_text():
    original = "学校教学计划与课程安排。" * 110
    pieces = bound_chunks([{"location": "第 1 页", "content": original}], 1000)
    assert len(pieces) > 1
    assert "".join(piece["content"] for piece in pieces) == original
    assert all(len(piece["content"].encode("utf-8")) <= 1000 for piece in pieces)
    assert all(piece["location"] == "第 1 页" for piece in pieces)


def test_pdf_layout_spaces_between_chinese_characters_are_removed():
    source = "中 华 人 民 共 和 国 教 育 部\n2022-11-30 发 布 Digital literacy of teachers"
    assert normalize_pdf_text(source) == "中华人民共和国教育部\n2022-11-30 发布 Digital literacy of teachers"


def test_ocr_keeps_text_confidence_and_position(monkeypatch):
    picture = io.BytesIO()
    Image.new("RGB", (100, 40), "white").save(picture, "PNG")
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t2\t3\t20\t10\t95\tLesson\n"
        "5\t1\t1\t1\t1\t25\t3\t15\t10\t85\t123\n"
    )
    monkeypatch.setattr(
        "app.ocr.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=tsv.encode()),
    )
    assert recognize_image(picture.getvalue()) == [
        {"text": "Lesson 123", "confidence": 0.9, "bbox": [2, 3, 40, 13]}
    ]


def test_image_without_ocr_text_keeps_a_visual_fallback_marker():
    page = structured_pages(b"", ".png", [])[0]
    assert page["needs_review"] and page["blocks"][0]["kind"] == "image"


def test_unavailable_ocr_keeps_image_for_vision_fallback(monkeypatch):
    import ocr

    from app.file_parser import extract

    monkeypatch.setattr(ocr, "recognize_image", lambda *_: (_ for _ in ()).throw(ValueError("OCR_UNAVAILABLE")))
    details = {}
    assert extract(b"\x89PNG\r\n\x1a\ncontent", ".png", details) == []
    assert details["ocr_unavailable"] is True


def test_word_merged_table_keeps_cell_spans():
    from docx import Document

    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "教学目标"
    table.cell(1, 0).text = "理解概念"
    content = io.BytesIO()
    document.save(content)
    page = structured_pages(content.getvalue(), ".docx", [])[0]
    assert page["blocks"][0]["merges"] == [{"row": 0, "column": 0, "rowSpan": 1, "colSpan": 2}]


def test_long_table_chunks_repeat_column_headers():
    rows = [["班级", "教学目标"]] + [[f"第{i}班", "x" * 260] for i in range(8)]
    page = {"label": "第 1 页", "blocks": [{"kind": "table", "text": "", "cells": rows}]}
    pieces = semantic_chunks([page])
    assert len(pieces) > 1
    assert all("班级" in piece["content"] for piece in pieces)


def test_visual_enrichment_replaces_image_marker_and_keeps_ocr_when_provider_fails(monkeypatch):
    from app import visual_extraction

    spec = {"visionProvider": "test", "visionModel": "vision"}
    parsed = ParsedChunks(
        [{"location": "图片", "content": "85"}],
        [{"label": "图片", "blocks": [{"kind": "image", "text": "85", "needs_review": True}],
          "assembled_text": "85", "needs_review": True}],
    )
    monkeypatch.setattr(visual_extraction, "targets", lambda *_: iter([(0, b"image")]))
    monkeypatch.setattr(
        visual_extraction, "describe",
        lambda *_: [{"kind": "chart", "text": "一班 85 分", "cells": [], "source": "vision"}],
    )
    result = enrich(SimpleNamespace(), spec, b"image", "chart.png", parsed)
    assert result.pages[0]["blocks"][0]["kind"] == "chart"
    assert result[-1]["content"] == "一班 85 分"
    no_ocr = ParsedChunks([], [{"label": "图片", "blocks": [{"kind": "image", "text": "",
        "needs_review": True}], "assembled_text": "", "needs_review": True}])
    assert enrich(SimpleNamespace(), spec, b"image", "chart.png", no_ocr)[0]["content"] == "一班 85 分"

    monkeypatch.setattr(visual_extraction, "describe", lambda *_: (_ for _ in ()).throw(ValueError()))
    old = ParsedChunks([], [{"label": "图片", "blocks": [{"kind": "image", "text": "85"}],
                             "assembled_text": "85", "needs_review": True}])
    assert enrich(SimpleNamespace(), spec, b"image", "chart.png", old).pages[0]["assembled_text"] == "85"


def test_vision_response_turns_table_cells_into_searchable_text(monkeypatch):
    from app import visual_extraction

    monkeypatch.setattr(visual_extraction, "connection_for", lambda *_: SimpleNamespace(
        base_url="https://example.test", key="", auth_header="authorization"
    ))

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, _url, json, headers):
            assert json["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content":
                    '{"blocks":[{"kind":"table","text":"","cells":[["班级","成绩"],["一班","85"]]}]}'
                }}]},
                request=httpx.Request("POST", "https://example.test/chat/completions"),
            )

    monkeypatch.setattr(visual_extraction.httpx, "Client", lambda **_: Client())
    blocks = visual_extraction.describe(SimpleNamespace(), {"visionProvider": "test", "visionModel": "vision"},
                                         b"\x89PNG\r\n\x1a\n")
    assert blocks[0]["text"] == "班级 | 成绩\n一班 | 85"


def test_reranker_orders_valid_indices_and_falls_back_on_bad_response(monkeypatch):
    from app import reranking

    settings = SimpleNamespace()
    spec = {"rerankProvider": "test", "rerankModel": "rank"}
    candidates = [{"excerpt": "A", "chunkId": "a"}, {"excerpt": "B", "chunkId": "b"}]
    monkeypatch.setattr(reranking, "connection_for", lambda *_: SimpleNamespace(
        base_url="https://example.test", key="", auth_header="authorization"
    ))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={"results": [{"index": 1, "relevance_score": 0.9},
                                  {"index": 0, "relevance_score": 0.1}]},
                request=httpx.Request("POST", "https://example.test/rerank"),
            )

    monkeypatch.setattr(reranking.httpx, "AsyncClient", lambda **_: Client())
    assert [item["chunkId"] for item in asyncio.run(rerank(settings, spec, "question", candidates))] == ["b", "a"]

    class InvalidClient(Client):
        async def post(self, *_args, **_kwargs):
            return httpx.Response(
                200, json={"results": [{"index": 99, "relevance_score": 0.9}]},
                request=httpx.Request("POST", "https://example.test/rerank"),
            )

    monkeypatch.setattr(reranking.httpx, "AsyncClient", lambda **_: InvalidClient())
    assert asyncio.run(rerank(settings, spec, "question", candidates)) == candidates
