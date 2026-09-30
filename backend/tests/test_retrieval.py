# ruff: noqa: F811 - pytest fixtures imported for this module's parametrized tests
import asyncio
import os
from datetime import timedelta
from threading import Event

import anyio
import anyio.lowlevel
import pytest
from test_knowledge import base, execute, knowledge, upload  # noqa: F401 - register fixture
from test_model_runtime import enable_chat, provider  # noqa: F401 - register fixture

from app.embeddings import active_config, index_key
from app.indexing import process_index, schedule
from app.models import ChunkVector, EmbeddingUsage, IndexJob, now
from app.qdrant_index import sync_ready_documents
from app.repository import Repository


def configure(env, **changes):
    admin = env["client"]("admin")
    current = admin.get("/api/v1/admin/embedding").json()["data"]["config"]
    result = admin.put(
        "/api/v1/admin/embedding",
        json={
            "provider": "test",
            "model": "test-embedding",
            "dimensions": 3,
            "requestDimensions": True,
            "active": True,
            "expectedRevision": current["revision"] if current else 0,
            **changes,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()["data"]


def index(env, document):
    schedule(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"])
    for _ in range(100):
        with env["app"].state.sessions() as session:
            row = Repository(session, env["tenant_id"]).require(IndexJob, IndexJob.document_id == document)
            if row.status in ("succeeded", "failed", "cancelled"):
                return row.status
            identity = row.id
        process_index(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)
    pytest.fail("Index did not terminate")


def add_document(env, client, base_id, text):
    result = upload(client, base_id, text.encode(), name="教学资料.txt")
    assert result.status_code == 201, result.text
    document = result.json()["data"]["id"]
    execute(env, document)
    assert index(env, document) == "succeeded"
    return document


@pytest.mark.skipif(not os.environ.get("XFLJ_TEST_QDRANT_URL"), reason="Requires a real Qdrant instance")
def test_qdrant_hybrid_mirror_and_sql_permission_recheck(knowledge, provider):
    env = knowledge
    env["app"].state.settings.qdrant_url = os.environ["XFLJ_TEST_QDRANT_URL"]
    configure(env)
    teacher, peer = env["client"](), env["client"]("peer")
    private = base(teacher)
    identity = add_document(env, teacher, private, "函数单调性教学目标与课堂活动安排")
    sync_ready_documents(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"])
    with env["app"].state.sessions() as session:
        row = Repository(session, env["tenant_id"]).require(IndexJob, IndexJob.document_id == identity)
        key = row.index_key
    from app.qdrant_index import QdrantIndex

    points = QdrantIndex(env["app"].state.settings, 3).hybrid(
        env["tenant_id"], key, "教学目标", [1.0, 0.0, 0.0]
    )
    assert points
    own = teacher.post("/api/v1/knowledge/search", json={"query": "教学目标"})
    assert identity in own.text
    forbidden = peer.post("/api/v1/knowledge/search", json={"query": "教学目标"})
    assert identity not in forbidden.text
    assert teacher.delete(f"/api/v1/documents/{identity}").status_code == 200
    assert identity not in teacher.post("/api/v1/knowledge/search", json={"query": "教学目标"}).text


def test_embedding_admin_revisions_check_and_school_accounting(knowledge, provider):
    env = knowledge
    teacher, admin = env["client"](), env["client"]("admin")
    assert teacher.get("/api/v1/admin/embedding").status_code == 403
    assert teacher.get("/api/v1/admin/embedding/qdrant").status_code == 403
    assert admin.get("/api/v1/admin/embedding/qdrant").json()["data"]["configured"] is False
    assert teacher.put("/api/v1/admin/embedding", json={}).status_code == 403
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    configure(env)
    assert admin.post("/api/v1/admin/embedding/check").json()["data"]["dimensions"] == 3
    assert configure(env)["config"]["revision"] == 2
    optional = configure(
        env, visionProvider="test", visionModel="vision-model",
        rerankProvider="test", rerankModel="rerank-model",
    )["config"]
    assert optional["visionModel"] == "vision-model" and optional["rerankModel"] == "rerank-model"
    assert admin.put(
        "/api/v1/admin/embedding",
        json={"provider": "test", "model": "test-embedding", "dimensions": 3,
              "visionProvider": "test", "expectedRevision": optional["revision"]},
    ).status_code == 422
    assert (
        admin.put(
            "/api/v1/admin/embedding",
            json={"provider": "test", "model": "test-embedding", "dimensions": 3, "expectedRevision": 1},
        ).status_code
        == 409
    )
    assert provider[0] not in admin.get("/api/v1/admin/embedding").text
    assert teacher.get("/api/v1/me/quota").json()["data"] == quota


def test_vector_search_permissions_redaction_and_idempotent_index(knowledge, provider):
    env = knowledge
    configure(env)
    teacher, peer, admin, other = (
        env["client"](),
        env["client"]("peer"),
        env["client"]("admin"),
        env["client"]("teacher", "school-b"),
    )
    own, private_peer, shared = base(teacher), base(peer), base(admin, "school")
    visible = add_document(env, teacher, own, "数学函数单调性课堂设计，联系13812345678。")
    hidden = add_document(env, peer, private_peer, "数学函数单调性，这是其他老师的私人方法。")
    school = add_document(env, admin, shared, "数学函数单调性教研规范。")
    count = len(provider[2])
    assert index(env, visible) == "succeeded"
    assert len(provider[2]) == count
    result = teacher.post("/api/v1/knowledge/search", json={"query": "函数单调性", "knowledgeBaseIds": []})
    assert result.status_code == 200, result.text
    assert {item["documentId"] for item in result.json()["data"]["items"]} == {visible, school}
    assert hidden not in result.text and "13812345678" not in str(provider[2])
    assert (
        teacher.post(
            "/api/v1/knowledge/search", json={"query": "数学", "knowledgeBaseIds": [private_peer]}
        ).status_code
        == 404
    )
    assert (
        other.post(
            "/api/v1/knowledge/search", json={"query": "数学", "knowledgeBaseIds": [shared]}
        ).status_code
        == 404
    )
    assert teacher.post("/api/v1/knowledge/search", json={"query": "语文阅读"}).json()["data"]["items"] == []
    assert teacher.get("/api/v1/me/quota").json()["data"]["totalConsumed"] == 0
    assert teacher.delete(f"/api/v1/documents/{visible}").status_code == 200
    assert visible not in teacher.post("/api/v1/knowledge/search", json={"query": "数学"}).text
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert not repo.one(ChunkVector, ChunkVector.document_id == visible)


@pytest.mark.parametrize("model", ["bad-dimension", "nonfinite", "embedding-no-usage"])
def test_invalid_embedding_cannot_make_document_ready(knowledge, provider, model):
    env = knowledge
    configure(env, model=model)
    client = env["client"]()
    document = upload(client, base(client), "数学资料".encode()).json()["data"]["id"]
    execute(env, document)
    assert index(env, document) == "failed"
    row = client.get(f"/api/v1/documents/{document}").json()["data"]
    assert row["status"] == "index_failed" and row["errorCode"] == "INVALID_EMBEDDING_RESPONSE"
    assert client.get("/api/v1/me/quota").json()["data"]["totalConsumed"] == 0


def test_model_change_rebuilds_without_mixing_vectors(knowledge, provider):
    env = knowledge
    configure(env)
    client = env["client"]()
    doc = add_document(env, client, base(client), "数学函数教学。")
    configure(env, model="test-embedding-v2")
    assert client.get(f"/api/v1/documents/{doc}").json()["data"]["status"] == "parsed"
    count = len(provider[2])
    assert client.post("/api/v1/knowledge/search", json={"query": "函数"}).json()["data"]["items"] == []
    assert len(provider[2]) == count
    assert index(env, doc) == "succeeded"
    assert client.get(f"/api/v1/documents/{doc}").json()["data"]["status"] == "ready"
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        key = index_key(env["app"].state.settings, active_config(repo))
        assert {v.index_key for v in session.scalars(repo.query(ChunkVector))} == {key}


def test_mixed_vector_dimensions_are_filtered_before_distance(knowledge, provider):
    env = knowledge
    configure(env)
    client = env["client"]()
    identity = base(client)
    old = add_document(env, client, identity, "数学旧索引")
    configure(env, dimensions=4)
    fresh = add_document(env, client, identity, "数学新索引")
    result = client.post("/api/v1/knowledge/search", json={"query": "数学"})
    assert result.status_code == 200, result.text
    assert fresh in result.text and old not in result.text
    with env["app"].state.sessions() as session:
        vectors = list(session.scalars(Repository(session, env["tenant_id"]).query(ChunkVector)))
        assert {len(row.embedding) for row in vectors} == {3, 4}


def test_chat_citations_persist_replay_and_only_generation_charges(knowledge, provider):
    env = knowledge
    configure(env)
    enable_chat(env, model="rag-chat")
    teacher = env["client"]()
    document = add_document(env, teacher, base(teacher), "函数单调性：通过定义和图像设计课堂活动。")
    chat = teacher.post("/api/v1/conversations", json={"title": "知识库教学"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{chat}/messages"
    payload = {"content": "如何讲函数单调性？", "documentIds": [document]}
    result = teacher.post(path, json=payload, headers={"Idempotency-Key": "rag-chat-1"})
    assert "event: citation" in result.text and "event: message.completed" in result.text, result.text
    history = teacher.get(path).json()["data"]["items"]
    citation = history[-1]["citations"][0]
    assert citation["documentId"] == document and citation["number"] == 1
    assert citation["excerpt"] == "函数单调性：通过定义和图像设计课堂活动。"
    assert citation["excerpt"] in str(provider[2][-1]["messages"])
    count = len(provider[2])
    replay = teacher.post(path, json=payload, headers={"Idempotency-Key": "rag-chat-1"})
    assert "event: citation" in replay.text and len(provider[2]) == count
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["totalConsumed"] == 1 and quota["reserved"] == 0
    assert teacher.delete(f"/api/v1/documents/{document}").status_code == 200
    withdrawn = teacher.get(path).json()["data"]["items"][-1]["citations"][0]
    assert withdrawn["unavailable"] and "excerpt" not in withdrawn


def test_disabled_knowledge_avoids_embedding_and_index_cancellation(knowledge, provider, monkeypatch):
    env = knowledge
    configure(env)
    enable_chat(env)
    client = env["client"]()
    document = upload(client, base(client), "函数单调性数学资料".encode()).json()["data"]["id"]
    execute(env, document)

    async def delete_during_embedding(*args):
        assert client.delete(f"/api/v1/documents/{document}").status_code == 200
        return [[1.0, 0.0, 0.0]]

    monkeypatch.setattr("app.indexing.measured_embed", delete_during_embedding)
    assert index(env, document) == "cancelled"
    with env["app"].state.sessions() as session:
        assert not list(session.scalars(Repository(session, env["tenant_id"]).query(ChunkVector)))
    chat = client.post("/api/v1/conversations", json={"title": "普通对话"}).json()["data"]["id"]
    count = len(provider[2])
    result = client.post(
        f"/api/v1/conversations/{chat}/messages",
        json={"content": "函数", "useKnowledge": False},
        headers={"Idempotency-Key": "plain-chat"},
    )
    assert "event: message.completed" in result.text and len(provider[2]) == count + 1


def test_school_usage_marks_unknown_on_cancel(environment, provider, monkeypatch):
    from app.embeddings import EmbeddingAdapter, measured_embed

    env = environment
    configure(env)
    with env["app"].state.sessions() as session:
        spec = active_config(Repository(session, env["tenant_id"]))
    assert asyncio.run(
        measured_embed(
            env["app"].state.sessions,
            env["app"].state.settings,
            env["tenant_id"],
            spec,
            ["数学"],
            "query",
            env["tenant_id"],
        )
    )
    with env["app"].state.sessions() as session:
        usage = Repository(session, env["tenant_id"]).require(EmbeddingUsage)
        assert usage.status == "completed" and usage.input_tokens > 0

    async def cancelled(*_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(EmbeddingAdapter, "embed", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            measured_embed(
                env["app"].state.sessions,
                env["app"].state.settings,
                env["tenant_id"],
                spec,
                ["数学"],
                "query",
                env["tenant_id"],
            )
        )
    with env["app"].state.sessions() as session:
        usage = Repository(session, env["tenant_id"]).require(
            EmbeddingUsage, EmbeddingUsage.status == "unknown"
        )
        assert usage.input_tokens is None


def test_bge_large_query_instruction_is_not_added_to_document_passages(environment, provider, monkeypatch):
    from app.embeddings import EmbeddingAdapter, measured_embed

    env = environment
    configure(env)
    with env["app"].state.sessions() as session:
        spec = {**active_config(Repository(session, env["tenant_id"])), "model": "BAAI/bge-large-zh-v1.5"}
    received = []

    async def capture(self, config, texts):
        received.append(texts)
        return [[1.0, 0.0]], 10

    monkeypatch.setattr(EmbeddingAdapter, "embed", capture)
    for purpose in ("index", "query"):
        asyncio.run(measured_embed(
            env["app"].state.sessions, env["app"].state.settings, env["tenant_id"],
            spec, ["光合作用"], purpose, env["tenant_id"],
        ))
    assert received[0] == ["光合作用"]
    assert received[1] == ["为这个句子生成表示以用于检索相关文章：光合作用"]


def test_usage_database_wait_does_not_block_event_loop(environment, provider, monkeypatch):
    from app.embeddings import measured_embed

    env = environment
    configure(env)
    with env["app"].state.sessions() as session:
        spec = active_config(Repository(session, env["tenant_id"]))
    released = Event()
    original = Repository.add

    def waiting_add(self, entity, **values):
        if entity is EmbeddingUsage:
            assert released.wait(2), "Database wait blocked event-loop transaction cleanup"
        return original(self, entity, **values)

    monkeypatch.setattr(Repository, "add", waiting_add)

    async def release_other_request():
        await asyncio.sleep(0.05)
        released.set()

    async def run():
        await asyncio.gather(
            release_other_request(),
            measured_embed(
                env["app"].state.sessions,
                env["app"].state.settings,
                env["tenant_id"],
                spec,
                ["数学"],
                "query",
                env["tenant_id"],
            ),
        )

    asyncio.run(run())


def test_request_cancel_scope_preserves_school_usage_finalization(environment, provider, monkeypatch):
    from app.embeddings import EmbeddingAdapter, measured_embed

    env = environment
    configure(env)
    with env["app"].state.sessions() as session:
        spec = active_config(Repository(session, env["tenant_id"]))

    async def run():
        with anyio.CancelScope() as scope:

            async def cancelled(*_):
                scope.cancel()
                await anyio.lowlevel.checkpoint()

            monkeypatch.setattr(EmbeddingAdapter, "embed", cancelled)
            await measured_embed(
                env["app"].state.sessions,
                env["app"].state.settings,
                env["tenant_id"],
                spec,
                ["数学"],
                "query",
                env["tenant_id"],
            )

    anyio.run(run)
    with env["app"].state.sessions() as session:
        usage = Repository(session, env["tenant_id"]).require(EmbeddingUsage)
        assert usage.status == "unknown" and usage.input_tokens is None


def test_index_batches_resume_after_expired_lease_without_repeating_committed_vectors(knowledge, provider):
    env = knowledge
    configure(env)
    client = env["client"]()
    document = upload(client, base(client), ("数学函数课堂活动。" * 2100).encode()).json()["data"]["id"]
    execute(env, document)
    sessions, settings, tenant = env["app"].state.sessions, env["app"].state.settings, env["tenant_id"]
    with sessions() as session:
        identity = Repository(session, tenant).require(IndexJob, IndexJob.document_id == document).id
    process_index(sessions, settings, tenant, identity)
    with sessions.begin() as session:
        repo = Repository(session, tenant)
        assert len(list(session.scalars(repo.query(ChunkVector)))) == 16
        job = repo.require(IndexJob, IndexJob.id == identity)
        assert job.status == "queued"
        # Simulate a worker dying after claiming the next batch, before committing vectors.
        job.status, job.attempts = "running", 1
        job.lease_token, job.lease_until = "expired-worker", now() - timedelta(seconds=1)
    schedule(sessions, settings, tenant)
    with sessions() as session:
        job = Repository(session, tenant).require(IndexJob, IndexJob.id == identity)
        assert job.status == "queued" and job.lease_token is None
    assert index(env, document) == "succeeded"
    inputs = [text for call in provider[2] if "input" in call for text in call["input"]]
    with sessions() as session:
        vectors = list(session.scalars(Repository(session, tenant).query(ChunkVector)))
        assert len(vectors) > 16 and len(inputs) == len(vectors)
    assert client.get("/api/v1/me/quota").json()["data"]["totalConsumed"] == 0
