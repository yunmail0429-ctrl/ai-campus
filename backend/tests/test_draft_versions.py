from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.models import AuditLog, Conversation, DraftVersion
from app.repository import Repository


def test_draft_versions_conflicts_restore_pagination_and_privacy(environment, client):
    env = environment
    identity = client.post("/api/v1/conversations", json={"title": "版本测试"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{identity}/draft"
    before = client.get("/api/v1/me/quota").json()["data"]
    assert client.get(path).json()["data"] == {"content": "", "revision": 0}
    assert client.put(path, json={"content": "缺少版本号"}).status_code == 422
    first = client.put(path, json={"content": "私人原稿", "revision": 0}).json()["data"]
    assert first == {"content": "私人原稿", "revision": 1}
    assert client.put(path, json={"content": "私人原稿", "revision": 0}).json()["data"] == first
    assert client.put(path, json={"content": "第二稿", "revision": 1}).json()["data"]["revision"] == 2
    conflict = client.put(path, json={"content": "旧页面修改", "revision": 1})
    assert conflict.status_code == 409 and conflict.json()["code"] == "DRAFT_VERSION_CONFLICT"
    assert client.get(path).json()["data"]["content"] == "第二稿"
    restored = client.post(path + "/versions/1/restore", json={"revision": 2}).json()["data"]
    assert restored == {"content": "私人原稿", "revision": 3}
    assert client.post(path + "/versions/1/restore", json={"revision": 2}).json()["data"] == restored
    assert client.get(path + "/versions/3").json()["data"]["restoredFrom"] == 1
    latest = client.get(path + "/versions?limit=2").json()["data"]
    older = client.get(path + "/versions", params={"limit": 2, "cursor": latest["nextCursor"]}).json()["data"]
    assert [row["number"] for row in latest["items"] + older["items"]] == [3, 2, 1]
    assert "content" not in latest["items"][0] and older["nextCursor"] is None
    assert client.get(path + "/versions?cursor=invalid").status_code == 422
    for other in (env["client"]("peer"), env["client"]("admin"), env["client"]("teacher", "school-b")):
        for suffix in ("", "/versions", "/versions/1"):
            assert other.get(path + suffix).status_code == 404
        assert other.post(path + "/versions/1/restore", json={"revision": 3}).status_code == 404
    assert client.get("/api/v1/me/quota").json()["data"] == before
    with env["app"].state.sessions() as session:
        audit = list(
            session.scalars(
                Repository(session, env["tenant_id"])
                .query(AuditLog)
                .where(AuditLog.action.in_(("draft.saved", "draft.restored")))
            )
        )
        assert len(audit) == 3 and all("私人原稿" not in str(row.details) for row in audit)
    client.delete(f"/api/v1/conversations/{identity}")
    assert client.get(path + "/versions").status_code == 404
    assert client.post(path + "/versions/1/restore", json={"revision": 3}).status_code == 404


def test_concurrent_draft_saves_only_one_wins(environment, client):
    identity = client.post("/api/v1/conversations", json={"title": "并发版本"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{identity}/draft"
    second = environment["client"]()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(target.put, path, json={"content": content, "revision": 0})
            for target, content in ((client, "甲修改"), (second, "乙修改"))
        ]
        statuses = sorted(future.result().status_code for future in futures)
    assert statuses == [200, 409]
    assert len(client.get(path + "/versions").json()["data"]["items"]) == 1


def test_draft_migration_preserves_existing_content(environment, client):
    env = environment
    identity = client.post("/api/v1/conversations", json={"title": "旧草稿迁移"}).json()["data"]["id"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "5043e04bea9b")
    with env["app"].state.sessions.begin() as session:
        Repository(session, env["tenant_id"]).require(
            Conversation, Conversation.id == identity
        ).draft = "迁移前内容"
    command.upgrade(config, "head")
    path = f"/api/v1/conversations/{identity}/draft"
    assert client.get(path).json()["data"] == {"content": "迁移前内容", "revision": 1}
    assert client.put(path, json={"content": "新修改", "revision": 1}).json()["data"]["revision"] == 2
    with env["app"].state.sessions() as session:
        repo = Repository(session, env["tenant_id"])
        assert (
            repo.require(
                DraftVersion, DraftVersion.conversation_id == identity, DraftVersion.number == 1
            ).content
            == "迁移前内容"
        )
