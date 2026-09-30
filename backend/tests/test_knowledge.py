import hashlib
import io
import os
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.config import StorageProfile
from app.errors import AppError
from app.ingestion import dispatch, parse, process_job, recover_jobs, scan
from app.models import Document, DocumentChunk, DocumentPage, IngestionJob, OutboxEvent, now
from app.repository import Repository


@pytest.fixture
def knowledge(environment, monkeypatch):
    with mock_aws():
        s3 = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
        )
        s3.create_bucket(Bucket="private-test")
        settings = environment["app"].state.settings
        settings.storage = StorageProfile(bucket="private-test", access_key="test", secret_key="test")
        # Protocol behavior is covered separately; no production bypass switch exists.
        monkeypatch.setattr("app.ingestion.scan", lambda *_: None)
        yield environment


def base(client, scope="private"):
    response = client.post("/api/v1/knowledge-bases", json={"name": "教学资料", "scope": scope})
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def upload(client, base_id, content=b"A lesson about mathematics.", key=None, name="lesson.txt"):
    return client.post(
        f"/api/v1/knowledge-bases/{base_id}/documents",
        files={"file": (name, content, "text/plain")},
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def execute(env, document_id):
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        job = repo.require(IngestionJob, IngestionJob.document_id == document_id)
        identity = job.id
    return process_job(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)


@pytest.mark.parametrize("name", ["worldbank-financial.pdf", "unicef-infographic.pdf"])
def test_public_real_pdf_upload_parse_and_persist(knowledge, name):
    fixture_dir = os.environ.get("XFLJ_REAL_DOCUMENT_DIR")
    if not fixture_dir:
        pytest.skip("Set XFLJ_REAL_DOCUMENT_DIR to run public-document smoke tests")
    content = (Path(fixture_dir) / name).read_bytes()
    client = knowledge["client"]()
    library = base(client)
    response = upload(client, library, content, name=name)
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]
    execute(knowledge, document_id)
    document = client.get(f"/api/v1/documents/{document_id}").json()["data"]
    assert document["status"] == "parsed", document
    assert document["chunkCount"] > 0
    chunks = client.get(f"/api/v1/documents/{document_id}/chunks").json()["data"]["items"]
    pages = client.get(f"/api/v1/documents/{document_id}/pages").json()["data"]["items"]
    assert chunks and pages
    assert all(chunk["content"].strip() for chunk in chunks)
    assert all(page["blocks"] for page in pages)
    with knowledge["app"].state.sessions() as session:
        repo = Repository(session, knowledge["tenant_id"])
        assert len(list(session.scalars(repo.query(DocumentChunk).where(DocumentChunk.document_id == document_id)))) == document["chunkCount"]
        assert len(list(session.scalars(repo.query(DocumentPage).where(DocumentPage.document_id == document_id)))) >= len(pages)


@pytest.mark.parametrize("length", [7000, 7100])
def test_chunk_preview_pagination_has_no_empty_next_page(knowledge, length):
    env = knowledge
    teacher = env["client"]()
    library = base(teacher)
    identity = upload(teacher, library, ("字" * length).encode()).json()["data"]["id"]
    execute(env, identity)
    path = f"/api/v1/documents/{identity}/chunks"
    expected_count = teacher.get(f"/api/v1/documents/{identity}").json()["data"]["chunkCount"]
    first = teacher.get(path).json()["data"]
    assert len(first["items"]) == 20
    assert [row["position"] for row in first["items"]] == list(range(20))
    collected = list(first["items"])
    cursor = first["nextCursor"]
    while cursor is not None:
        next_page = teacher.get(path, params={"offset": cursor}).json()["data"]
        collected.extend(next_page["items"])
        cursor = next_page["nextCursor"]
    assert len(collected) == expected_count
    assert [row["position"] for row in collected] == list(range(expected_count))
    assert len({row["id"] for row in collected}) == expected_count
    for other in (env["client"]("peer"), env["client"]("teacher", "school-b")):
        assert other.get(path + "?offset=20").status_code == 404


def test_private_upload_idempotency_and_scanned_parsing(knowledge):
    env = knowledge
    teacher = env["client"]()
    identity = base(teacher)
    key = str(uuid.uuid4())
    document = upload(teacher, identity, key=key).json()["data"]
    assert document["status"] == "queued"
    again = upload(teacher, identity, key=key)
    assert again.json()["data"]["id"] == document["id"]
    assert upload(teacher, identity, b"other", key=key).status_code == 409
    path = f"/api/v1/documents/{document['id']}"
    assert teacher.post(path + "/download").status_code == 409
    execute(env, document["id"])
    execute(env, document["id"])
    parsed = teacher.get(path).json()["data"]
    assert parsed["status"] == "parsed" and parsed["chunkCount"] == 1
    assert (
        teacher.get(path + "/chunks").json()["data"]["items"][0]["content"] == "A lesson about mathematics."
    )
    download = teacher.post(path + "/download").json()["data"]
    assert download["expiresIn"] <= 120 and "X-Amz-Signature" in download["url"]
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert len(list(session.scalars(repo.query(Document)))) == 1
        assert len(list(session.scalars(repo.query(DocumentChunk)))) == 1
        assert len(list(session.scalars(repo.query(DocumentPage)))) == 1
    page = teacher.get(path + "/pages").json()["data"]["items"][0]
    assert page["blocks"][0]["text"] == "A lesson about mathematics."
    assert teacher.post(path + "/reparse").status_code == 200
    execute(env, document["id"])
    assert teacher.get(path).json()["data"]["status"] == "parsed"
    assert len(teacher.get(path + "/pages").json()["data"]["items"]) == 1


def test_private_school_and_cross_tenant_permissions(knowledge):
    env = knowledge
    teacher, peer, admin, other = (
        env["client"](),
        env["client"]("peer"),
        env["client"]("admin"),
        env["client"]("teacher", "school-b"),
    )
    private = base(teacher)
    document = upload(teacher, private).json()["data"]["id"]
    execute(env, document)
    assert teacher.post(f"/api/v1/documents/{document}/download").status_code == 200
    for outsider in (peer, admin, other):
        assert outsider.get(f"/api/v1/knowledge-bases/{private}/documents").status_code == 404
        assert outsider.get(f"/api/v1/documents/{document}").status_code == 404
        assert outsider.delete(f"/api/v1/documents/{document}").status_code == 404
        assert outsider.post(f"/api/v1/documents/{document}/download").status_code == 404
        assert outsider.get(f"/api/v1/documents/{document}/chunks").status_code == 404
        assert outsider.get(f"/api/v1/documents/{document}/pages").status_code == 404
    assert (
        teacher.post("/api/v1/knowledge-bases", json={"name": "学校", "scope": "school"}).status_code == 403
    )
    shared = base(admin, "school")
    shared_doc = upload(admin, shared).json()["data"]["id"]
    assert teacher.get(f"/api/v1/documents/{shared_doc}").status_code == 200
    assert upload(teacher, shared).status_code == 403
    assert teacher.delete(f"/api/v1/documents/{shared_doc}").status_code == 403
    assert other.get(f"/api/v1/documents/{shared_doc}").status_code == 404


def test_scan_failure_quarantine_and_retry(knowledge, monkeypatch):
    env = knowledge
    teacher = env["client"]()
    identity = base(teacher)
    document = upload(teacher, identity).json()["data"]["id"]
    path = f"/api/v1/documents/{document}"

    def infected(*_):
        raise AppError(422, "FILE_INFECTED", "文件未通过安全扫描，已隔离。")

    monkeypatch.setattr("app.ingestion.scan", infected)
    execute(env, document)
    assert teacher.get(path).json()["data"]["status"] == "quarantined"
    assert teacher.post(path + "/download").status_code == 409
    assert teacher.post(path + "/retry").status_code == 409
    assert teacher.get(path + "/chunks").json()["data"]["items"] == []


def test_upload_validation_and_delete(knowledge):
    env = knowledge
    teacher = env["client"]()
    identity = base(teacher)
    assert upload(teacher, identity, b"", name="empty.txt").status_code == 422
    assert upload(teacher, identity, b"MZbinary", name="evil.exe").status_code == 422
    document = upload(teacher, identity).json()["data"]["id"]
    execute(env, document)
    assert teacher.delete(f"/api/v1/documents/{document}").status_code == 200
    assert teacher.get(f"/api/v1/documents/{document}").status_code == 404
    assert teacher.post(f"/api/v1/documents/{document}/download").status_code == 404
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert repo.one(OutboxEvent, OutboxEvent.kind == "document.delete")
        assert not list(session.scalars(repo.query(DocumentChunk)))


def test_storage_unavailable_preserves_retryable_upload(knowledge, monkeypatch):
    from app.storage import ObjectStore

    env = knowledge
    teacher = env["client"]()
    identity = base(teacher)
    key = str(uuid.uuid4())
    original = ObjectStore.put

    def fail(*_):
        raise AppError(503, "STORAGE_UNAVAILABLE", "文件存储暂时不可用。")

    monkeypatch.setattr(ObjectStore, "put", fail)
    assert upload(teacher, identity, key=key).status_code == 503
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        row = repo.require(Document, Document.operation_key == key)
        assert row.status == "uploading"
        assert row.sha256 == hashlib.sha256(b"A lesson about mathematics.").hexdigest()
    monkeypatch.setattr(ObjectStore, "put", original)
    assert upload(teacher, identity, key=key).json()["data"]["id"] == row.id


def test_outbox_survives_broker_failure_and_recovers_worker_lease(knowledge):
    env = knowledge
    client = env["client"]()
    document = upload(client, base(client)).json()["data"]["id"]

    def unavailable(*_):
        raise ConnectionError("test broker is unavailable")

    with pytest.raises(ConnectionError):
        dispatch(env["app"].state.sessions, env["tenant_id"], unavailable)
    deliveries = []
    assert dispatch(env["app"].state.sessions, env["tenant_id"], lambda *args: deliveries.append(args)) == 1
    assert dispatch(env["app"].state.sessions, env["tenant_id"], lambda *args: deliveries.append(args)) == 0
    with env["app"].state.sessions.begin() as session:
        repo = Repository(session, env["tenant_id"])
        job = repo.require(IngestionJob, IngestionJob.document_id == document)
        job.status, job.lease_token, job.lease_until = (
            "running",
            str(uuid.uuid4()),
            now() - timedelta(seconds=1),
        )
        job.attempts = 1
        repo.require(Document, Document.id == document).status = "scanning"
    recover_jobs(env["app"].state.sessions, env["tenant_id"])
    execute(env, document)
    assert client.get(f"/api/v1/documents/{document}").json()["data"]["status"] == "parsed"


def test_missing_scanner_manual_retry_and_delete_during_parse(knowledge, monkeypatch):
    env = knowledge
    client = env["client"]()
    document = upload(client, base(client)).json()["data"]["id"]
    monkeypatch.setattr("app.ingestion.scan", scan)
    env["app"].state.settings.clamav_host = None
    execute(env, document)
    path = f"/api/v1/documents/{document}"
    assert client.get(path).json()["data"]["errorCode"] == "SCANNER_NOT_CONFIGURED"
    assert client.get(path).json()["data"]["canRetry"] is True
    assert client.post(path + "/download").status_code == 409
    assert client.post(path + "/retry").status_code == 200
    monkeypatch.setattr("app.ingestion.scan", lambda *_: None)

    def delete_while_processing(*_):
        assert client.delete(path).status_code == 200
        return [{"location": "正文", "content": "Must never resurrect deleted document"}]

    monkeypatch.setattr("app.ingestion.parse", delete_while_processing)
    execute(env, document)
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert repo.require(Document, Document.id == document).status == "deleted"
        assert not list(session.scalars(repo.query(DocumentChunk)))


def test_old_ocr_failure_can_retry_but_invalid_file_cannot(knowledge):
    env = knowledge
    client = env["client"]()
    document = upload(client, base(client)).json()["data"]["id"]
    path = f"/api/v1/documents/{document}"
    with env["app"].state.sessions.begin() as session:
        row = Repository(session, env["tenant_id"]).require(Document, Document.id == document)
        row.status, row.error_code = "failed", "OCR_REQUIRED"
    output = client.get(path).json()["data"]
    assert output["errorCode"] == "OCR_REQUIRED" and output["canRetry"] is True
    assert client.post(path + "/retry").status_code == 200
    with env["app"].state.sessions.begin() as session:
        row = Repository(session, env["tenant_id"]).require(Document, Document.id == document)
        row.status, row.error_code = "failed", "INVALID_FILE"
    refused = client.post(path + "/retry")
    assert refused.status_code == 409 and refused.json()["code"] == "DOCUMENT_NOT_RETRYABLE"
    with env["app"].state.sessions.begin() as session:
        row = Repository(session, env["tenant_id"]).require(Document, Document.id == document)
        assert row.status == "failed" and row.error_code == "INVALID_FILE"
        row.error_code = "SCANNER_UNAVAILABLE"
    assert client.get(path).json()["data"]["canRetry"] is True
    assert client.post(path + "/retry").status_code == 200


def test_attachments_owner_validation_limits_and_conversation_deletion(knowledge):
    env = knowledge
    client, peer = env["client"](), env["client"]("peer")
    conversation = client.post("/api/v1/conversations", json={"title": "教学附件"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{conversation}/attachments"
    args = {
        "files": {"file": ("lesson.txt", b"lesson", "text/plain")},
        "headers": {"Idempotency-Key": "attachment-1"},
    }
    assert peer.post(path, **args).status_code == 404
    result = client.post(path, **args)
    assert result.status_code == 201
    document = result.json()["data"]["id"]
    assert client.get(path).json()["data"]["items"][0]["id"] == document
    env["app"].state.settings.max_upload_bytes = 1024
    large = {"files": {"file": ("large.txt", b"a" * 2048)}, "headers": {"Idempotency-Key": "large"}}
    assert client.post(path, **large).status_code == 413
    huge = {"files": {"file": ("large.txt", b"a" * 100_000)}, "headers": {"Idempotency-Key": "huge"}}
    assert client.post(path, **huge).status_code == 413
    assert client.delete(f"/api/v1/conversations/{conversation}").status_code == 200
    assert client.get(f"/api/v1/documents/{document}").status_code == 404
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert repo.require(Document, Document.id == document).status == "deleted"
        assert repo.require(IngestionJob, IngestionJob.document_id == document).status == "cancelled"


def test_public_bucket_rejected_and_document_tags_validated(knowledge):
    from app.storage import ObjectStore

    client = knowledge["client"]()
    identity = base(client)
    store = ObjectStore(knowledge["app"].state.settings)
    store.client.put_bucket_acl(Bucket=store.profile.bucket, ACL="public-read")
    assert upload(client, identity).json()["code"] == "STORAGE_NOT_PRIVATE"
    store.client.put_bucket_acl(Bucket=store.profile.bucket, ACL="private")
    document = upload(client, identity).json()["data"]["id"]
    path = f"/api/v1/documents/{document}"
    assert client.patch(path, json={"tags": ["数学", " 教案 ", "数学"]}).json()["data"]["tags"] == [
        "数学",
        "教案",
    ]
    assert client.patch(path, json={"tags": ["x" * 31]}).status_code == 422


def test_document_parsers_extract_word_ppt_pdf_html_and_refuse_invalid_files():
    from docx import Document as Word
    from pptx import Presentation

    word = Word()
    word.add_paragraph("函数的单调性")
    word.add_table(rows=1, cols=1).cell(0, 0).text = "表格中的教学目标"
    content = io.BytesIO()
    word.save(content)
    assert "表格中的教学目标" in parse(content.getvalue(), "课件.docx")[0]["content"]
    from app.ingestion import parse as parse_file

    ordered = parse_file(content.getvalue(), "课件.docx").pages[0]
    assert [block["kind"] for block in ordered["blocks"] if block["text"]] == ["paragraph", "table"]
    assert ordered["blocks"][1]["cells"] == [["表格中的教学目标"]]
    slides = Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[1])
    slide.shapes.title.text = "单调性定义"
    content = io.BytesIO()
    slides.save(content)
    assert parse(content.getvalue(), "课件.pptx")[0]["location"] == "第 1 张幻灯片"
    assert "单调性定义" in parse(content.getvalue(), "课件.pptx")[0]["content"]
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    chart_data = CategoryChartData()
    chart_data.categories = ["一班", "二班"]
    chart_data.add_series("平均分", [85, 91])
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(5), Inches(3), chart_data)
    content = io.BytesIO()
    slides.save(content)
    chart_pages = parse(content.getvalue(), "成绩.pptx").pages
    assert any(block["kind"] == "chart" and "一班 85" in block["text"] for block in chart_pages[0]["blocks"])
    html = b"<h1>Lesson</h1><script>danger()</script><p>Material</p>"
    assert "danger" not in parse(html, "lesson.html")[0]["content"]
    # A minimal real PDF text content stream; no renderer dependency needed.
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Lesson PDF) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    pdf = io.BytesIO()
    writer.write(pdf)
    assert "Lesson PDF" in parse(pdf.getvalue(), "lesson.pdf")[0]["content"]
    for name, content, code in [
        ("fake.pdf", b"not a PDF", "INVALID_FILE"),
        ("photo.png", b"image", "INVALID_FILE"),
        ("binary.txt", b"a\x00b", "INVALID_FILE"),
    ]:
        with pytest.raises(AppError) as caught:
            parse(content, name)
        assert caught.value.code == code


@pytest.mark.parametrize("suffix", ["docx", "pptx"])
def test_isolated_parser_rejects_macro_and_excessive_archive_entries(suffix):
    for excessive, expected in ((False, "INVALID_FILE"), (True, "PARSE_LIMIT")):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as package:
            if excessive:
                for index in range(2001):
                    package.writestr(f"entry-{index}.xml", "")
            else:
                package.writestr("word/vbaProject.bin", b"inert test bytes")
        with pytest.raises(AppError) as error:
            parse(content.getvalue(), f"rejected.{suffix}")
        assert error.value.code == expected


def test_isolated_parser_rejects_encrypted_pdf_and_excessive_text():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("test-only-password")
    content = io.BytesIO()
    writer.write(content)
    for payload, filename, expected in (
        (content.getvalue(), "encrypted.pdf", "ENCRYPTED_FILE"),
        (b"x" * 500001, "too-long.txt", "PARSE_LIMIT"),
    ):
        with pytest.raises(AppError) as error:
            parse(payload, filename)
        assert error.value.code == expected
