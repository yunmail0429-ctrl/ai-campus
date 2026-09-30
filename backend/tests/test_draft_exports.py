import io
import zipfile

from docx import Document
from pypdf import PdfReader

from app.draft_exports import render
from app.models import AuditLog
from app.repository import Repository

CONTENT = """# 函数的单调性

## 教学目标
理解**递增**与*递减*，比较 x₁ 与 x₂ 的函数值。

1. 观察图像，提出猜想。
2. 使用定义说明理由。

| 环节 | 教师活动 | 学生活动 |
| --- | --- | --- |
| 导入 | 展示变化图像 | 观察并描述 |
| 探究 | 提出问题 | 小组讨论 |

```python
def lesson(x):
    return x + 1
```

<script>alert('literal only')</script>

[参考链接](https://example.invalid/private) 与 ![示意图](https://example.invalid/image.png)
"""


def test_export_formats_permissions_private_audit_and_no_credit_cost(environment, client):
    env = environment
    identity = client.post("/api/v1/conversations", json={"title": "函数教学草稿"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{identity}"
    client.put(path + "/draft", json={"content": "已保存的原稿", "revision": 0})
    before = client.get("/api/v1/me/quota").json()["data"]
    for format in ("md", "docx", "pdf"):
        response = client.post(path + "/export", json={"content": CONTENT, "format": format})
        assert response.status_code == 200, response.text[:100]
        assert response.headers["content-disposition"].endswith(f'.{format}"')
        assert response.headers["cache-control"] == "no-store"
        if format == "md":
            assert response.content.decode("utf-8") == CONTENT
        elif format == "docx":
            document = Document(io.BytesIO(response.content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            assert "函数的单调性" in text and "<script>" in text
            assert document.tables[0].cell(1, 2).text == "观察并描述"
            assert any(paragraph.style.name == "Heading 2" for paragraph in document.paragraphs)
            assert any(
                run.bold and run.text == "递增" for paragraph in document.paragraphs for run in paragraph.runs
            )
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                relations = archive.read("word/_rels/document.xml.rels")
                assert b'TargetMode="External"' not in relations
        else:
            assert response.content.startswith(b"%PDF-")
            pdf = PdfReader(io.BytesIO(response.content))
            text = "\n".join(page.extract_text() for page in pdf.pages)
            assert "函数的单调性" in text and "观察并描述" in text and "literal only" in text
            assert "₁" in text and "₂" in text and "lesson" in text
            embedded = [
                font.get_object()
                for font in pdf.pages[0]["/Resources"]["/Font"].values()
                if font.get_object().get("/Subtype") == "/TrueType"
            ]
            assert len(embedded) >= 2
            assert all("/FontFile2" in font["/FontDescriptor"] for font in embedded)
        for other in (env["client"]("peer"), env["client"]("admin"), env["client"]("teacher", "school-b")):
            assert (
                other.post(path + "/export", json={"content": CONTENT, "format": format}).status_code == 404
            )
    assert client.get(path + "/draft").json()["data"]["content"] == "已保存的原稿"
    assert client.get("/api/v1/me/quota").json()["data"] == before
    with env["app"].state.sessions() as session:
        rows = list(
            session.scalars(
                Repository(session, env["tenant_id"])
                .query(AuditLog)
                .where(AuditLog.action == "draft.exported")
            )
        )
        assert len(rows) == 3
        for row in rows:
            assert set(row.details) == {"format", "bytes"}
    client.delete(path)
    assert client.post(path + "/export", json={"content": CONTENT, "format": "docx"}).status_code == 404


def test_export_rejects_empty_controls_and_unsupported_formats(client):
    identity = client.post("/api/v1/conversations", json={"title": "导出验证"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{identity}/export"
    for content, format in (
        (" ", "pdf"),
        ("含\x00字符", "docx"),
        ("内容", "exe"),
        ("字" * 200001, "md"),
        ("字符\U0001fae0", "pdf"),
    ):
        assert client.post(path, json={"content": content, "format": format}).status_code in (413, 422)


def test_long_pdf_preserves_rows_across_pages_and_escapes_markup(tmp_path):
    content = "## 学习活动\n\n|编号|内容|\n|---|---|\n" + "\n".join(
        f"|{number}|第{number}项活动，观察函数图像并描述变化。|" for number in range(1, 81)
    )
    result = render("教学活动清单", content, "pdf")
    document = PdfReader(io.BytesIO(result))
    text = "".join(page.extract_text() for page in document.pages)
    assert 2 <= len(document.pages) <= 8
    assert "第1项活动" in text and "第80项活动" in text
    (tmp_path / "long.pdf").write_bytes(result)


def test_single_long_table_cell_splits_without_losing_content():
    phrase = "教学活动观察讨论"
    cell = "起始标记" + phrase * 800 + "结束标记"
    content = "|环节|教学内容|\n|---|---|\n|探究|" + cell + "|"
    result = render("长单元格验收", content, "pdf")
    document = PdfReader(io.BytesIO(result))
    text = "".join("".join(page.extract_text().split()) for page in document.pages)
    assert 2 <= len(document.pages) <= 100
    assert "起始标记" in text and "结束标记" in text
    # Repeated headers and page numbers interrupt words across page boundaries in extracted text.
    body = text.replace("环节教学内容", "")
    body = "".join(char for char in body if not char.isdigit())
    assert body.count(phrase) == 800
    word = Document(io.BytesIO(render("长单元格验收", content, "docx")))
    assert word.tables[0].cell(1, 1).text == cell
