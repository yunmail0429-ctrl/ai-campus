import asyncio

import pytest

from app.errors import AppError
from app.resource_contracts import ResourceArtifact, ResourceInput, adapter_for
from app.resource_execution import extension
from test_knowledge import knowledge  # noqa: F401
from test_model_runtime import enable_chat, provider  # noqa: F401

from app.models import ResourceOutput
from app.repository import Repository
from app.resource_execution import process_resource
from app.storage import ObjectStore


@pytest.mark.parametrize("kind,expected", [("image", "图片生成模型"), ("video", "视频模型")])
def test_text_creation_is_downloadable_and_model_ready(kind, expected, monkeypatch):
    monkeypatch.setattr("app.text_creations.snapshot_connection", lambda *_: None)
    config = {
        "provider": "fixture", "model": "fixture", "revision": 1,
        "contextTokens": 32000, "maxOutputTokens": 1000, "timeoutSeconds": 30,
        "inputPrice": 1000, "outputPrice": 2000,
    }
    data = ResourceInput(kind=kind, prompt="七年级植物课", options={"style": "清晰"})
    adapter = adapter_for(None, kind, config)
    plan = adapter.prepare(data)
    assert expected in plan.snapshot["messages"][0]["content"]

    async def stream(*_):
        yield {"event": "delta", "delta": "# 教学创作\n\n内容"}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.text_creations.ChatAdapter.stream", stream)
    result = asyncio.run(adapter.execute(data, plan.snapshot))
    assert result.credits <= plan.maxCredits
    assert extension(kind, result.artifact) == "md"
    with pytest.raises(AppError):
        extension(kind, ResourceArtifact(content=b"\xff", media_type="text/markdown"))


@pytest.mark.parametrize("kind", ["image", "video"])
def test_text_creation_task_can_be_submitted_and_downloaded(kind, knowledge, provider, monkeypatch):
    env = knowledge
    enable_chat(env)
    teacher = env["client"]()
    monkeypatch.setattr("app.resource_execution.scan", lambda *_: None)

    async def stream(*_):
        yield {"event": "delta", "delta": "# 教学内容\n\n可用于后续模型"}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.text_creations.ChatAdapter.stream", stream)
    capabilities = teacher.get("/api/v1/resource-tasks/capabilities").json()["data"]
    assert next(row for row in capabilities if row["kind"] == kind)["available"]
    response = teacher.post(
        "/api/v1/resource-tasks",
        json={"kind": kind, "prompt": "为植物课准备内容"},
        headers={"Idempotency-Key": f"text-{kind}"},
    )
    assert response.status_code == 201, response.text
    task = response.json()["data"]
    process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], task["id"])
    assert teacher.get(f"/api/v1/resource-tasks/{task['id']}").json()["data"]["status"] == "succeeded"
    assert teacher.get(f"/api/v1/resource-tasks/{task['id']}/download", follow_redirects=False).status_code == 303
    with env["app"].state.sessions() as session:
        output = Repository(session, env["tenant_id"]).require(ResourceOutput, ResourceOutput.task_id == task["id"])
        assert output.filename.endswith(".md")
        assert "教学内容" in ObjectStore(env["app"].state.settings).read(output.object_key, 100000).decode("utf-8")
