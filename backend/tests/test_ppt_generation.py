import asyncio
import io
import json

import pytest
from pptx import Presentation
from test_knowledge import knowledge  # noqa: F401
from test_model_runtime import enable_chat, model_input, provider  # noqa: F401

from app.errors import AppError
from app.models import ResourceOutput
from app.ppt_generation import PptAdapter
from app.ppt_quality import PptQualityError
from app.repository import Repository
from app.resource_contracts import ResourceInput
from app.resource_execution import process_resource
from app.storage import ObjectStore


def test_model_output_becomes_editable_deck_with_administrator_rates(monkeypatch):
    monkeypatch.setattr("app.ppt_generation.snapshot_connection", lambda *_: None)
    monkeypatch.setattr("app.ppt_generation.inspect_pptx", lambda content, pages: None)
    config = {
        "provider": "fixture",
        "model": "fixture",
        "revision": 7,
        "contextTokens": 32000,
        "maxOutputTokens": 1000,
        "timeoutSeconds": 30,
        "inputPrice": 1000,
        "outputPrice": 2000,
    }
    data = ResourceInput(kind="ppt", prompt="函数教学", options={"pages": 1})
    adapter = PptAdapter(None, config)
    plan = adapter.prepare(data)
    config["outputPrice"] = 999999
    assert plan.snapshot["outputPrice"] == 2000
    assert plan.snapshot["outlineOutputTokens"] == 1000
    assert PptAdapter(None, {**config, "maxOutputTokens": 8192}).prepare(data).snapshot["outlineOutputTokens"] == 2400

    async def stream(self, spec, messages):
        yield {
            "event": "delta",
            "delta": json.dumps({"slides": [{"title": "函数", "purpose": "观察函数变化", "points": ["观察图像"]}]}
                if "规划恰好" in messages[-1]["content"] else
                {"slides": [{"title": "函数", "points": ["观察图像"], "notes": "让学生描述变化。"}]}),
        }
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.ppt_generation.ChatAdapter.stream", stream)
    outline, outline_credits = asyncio.run(adapter.generate_outline(plan.snapshot))
    assert outline["slides"][0]["purpose"] == "观察函数变化"
    snapshot = {**plan.snapshot, "approvedOutline": outline, "outlineCredits": outline_credits}
    result = asyncio.run(adapter.execute(data, snapshot))
    assert result.credits == 2 and result.credits <= plan.maxCredits
    assert len(Presentation(io.BytesIO(result.artifact.content)).slides) == 1

    async def invalid(*_):
        yield {"event": "delta", "delta": "not valid JSON"}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.ppt_generation.ChatAdapter.stream", invalid)
    with pytest.raises(AppError) as error:
        asyncio.run(adapter.execute(data, snapshot))
    assert error.value.code == "PPT_GENERATION_FAILED"


def test_quality_gate_repairs_only_the_failed_slide(monkeypatch):
    monkeypatch.setattr("app.ppt_generation.snapshot_connection", lambda *_: None)
    checks = []

    def inspect(content, pages):
        checks.append(content)
        if len(checks) == 1:
            raise PptQualityError("Slide text touches edge", 0)

    monkeypatch.setattr("app.ppt_generation.inspect_pptx", inspect)

    async def stream(self, spec, messages):
        yield {"event": "delta", "delta": json.dumps({"slides": [{"title": "修复后", "points": ["保留教学内容"]}]})}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.ppt_generation.ChatAdapter.stream", stream)
    adapter = PptAdapter(None, {
        "provider": "fixture", "model": "fixture", "revision": 1,
        "contextTokens": 32000, "maxOutputTokens": 1000, "timeoutSeconds": 30,
        "inputPrice": 1000, "outputPrice": 2000,
    })
    data = ResourceInput(kind="ppt", prompt="函数教学", options={"pages": 1})
    snapshot = adapter.prepare(data).snapshot
    snapshot.update({"approvedOutline": {"slides": [{
        "title": "函数", "purpose": "观察", "points": ["保留教学内容"],
    }]}, "outlineCredits": 1})
    result = asyncio.run(adapter.execute(data, snapshot))
    assert len(checks) == 2 and result.credits == 3
    assert Presentation(io.BytesIO(result.artifact.content)).slides[0].shapes[0].text == "修复后"


def test_ppt_capability_tracks_missing_provider_credentials(knowledge, provider):  # noqa: F811
    env = knowledge
    enable_chat(env)
    teacher = env["client"]()
    path = "/api/v1/resource-tasks/capabilities"
    assert next(row for row in teacher.get(path).json()["data"] if row["kind"] == "ppt")["available"]
    env["app"].state.settings.model_api_keys = {}
    assert not next(row for row in teacher.get(path).json()["data"] if row["kind"] == "ppt")["available"]
    before = teacher.get("/api/v1/me/quota").json()["data"]
    response = teacher.post(
        "/api/v1/resource-tasks",
        headers={"Idempotency-Key": "missing-credentials"},
        json={"kind": "ppt", "prompt": "函数课件"},
    )
    assert response.status_code == 503
    assert teacher.get("/api/v1/me/quota").json()["data"] == before


def test_native_ppt_submission_snapshot_settlement_download_and_duplicate(knowledge, provider, monkeypatch):  # noqa: F811
    env = knowledge
    model, _ = enable_chat(env)
    admin, teacher = env["client"]("admin"), env["client"]()
    monkeypatch.setattr("app.resource_execution.scan", lambda *_: None)
    monkeypatch.setattr("app.ppt_generation.inspect_pptx", lambda content, pages: None)
    calls = []

    async def stream(self, spec, messages):
        calls.append(spec.model)
        content = {"slides": [{"title": "函数", "purpose": "观察变化", "points": ["观察图像"]}]} if "规划恰好" in messages[-1]["content"] else {"slides": [{"title": "函数", "points": ["观察图像"]}]}
        yield {"event": "delta", "delta": json.dumps(content)}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.ppt_generation.ChatAdapter.stream", stream)
    payload = {"kind": "ppt", "prompt": "生成函数课件", "options": {"pages": 1}}
    headers = {"Idempotency-Key": "native-ppt"}
    created = teacher.post("/api/v1/resource-tasks", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    task = created.json()["data"]
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == task["maxCredits"]
    changed = admin.put(
        f"/api/v1/admin/models/{model['id']}",
        json={**model_input(inputPrice=100000, outputPrice=100000), "expectedRevision": 1},
    )
    assert changed.status_code == 200
    process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], task["id"])
    path = f"/api/v1/resource-tasks/{task['id']}"
    awaiting = teacher.get(path).json()["data"]
    assert awaiting["status"] == "awaiting_outline"
    assert env["client"]("peer").post(path + "/approve-outline", json=awaiting["outline"]).status_code == 404
    assert teacher.post(path + "/approve-outline", json=awaiting["outline"]).status_code == 200
    process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], task["id"])
    assert teacher.get(path).json()["data"]["status"] == "succeeded"
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    assert quota["totalConsumed"] == 2 and quota["balance"] == 98 and quota["reserved"] == 0
    assert calls == ["test-chat", "test-chat"]
    assert (
        teacher.post("/api/v1/resource-tasks", json=payload, headers=headers).json()["data"]["id"]
        == task["id"]
    )
    assert teacher.get(path + "/download", follow_redirects=False).status_code == 303
    assert env["client"]("peer").get(path + "/download").status_code == 404
    with env["app"].state.sessions() as session:
        output = Repository(session, env["tenant_id"]).require(
            ResourceOutput, ResourceOutput.task_id == task["id"]
        )
        content = ObjectStore(env["app"].state.settings).read(output.object_key, 1000000)
        assert Presentation(io.BytesIO(content)).slides[0].shapes[0].text == "函数"


def test_cancelling_after_outline_settles_only_outline_usage(knowledge, provider, monkeypatch):  # noqa: F811
    env = knowledge
    enable_chat(env)
    teacher = env["client"]()

    async def stream(self, spec, messages):
        yield {"event": "delta", "delta": json.dumps({"slides": [
            {"title": "函数", "purpose": "引入课题", "points": ["观察图像"]}
        ]})}
        yield {"event": "usage", "inputTokens": 100, "outputTokens": 200}

    monkeypatch.setattr("app.ppt_generation.ChatAdapter.stream", stream)
    response = teacher.post("/api/v1/resource-tasks", headers={"Idempotency-Key": "outline-cancel"}, json={
        "kind": "ppt", "prompt": "函数课件", "options": {"pages": 1},
    })
    assert response.status_code == 201, response.text
    identity = response.json()["data"]["id"]
    process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)
    assert teacher.get(f"/api/v1/resource-tasks/{identity}").json()["data"]["status"] == "awaiting_outline"
    assert teacher.post(f"/api/v1/resource-tasks/{identity}/cancel").status_code == 200
    process_resource(env["app"].state.sessions, env["app"].state.settings, env["tenant_id"], identity)
    result = teacher.get(f"/api/v1/resource-tasks/{identity}").json()["data"]
    assert result["status"] == "cancelled" and result["credits"] >= 0
    assert teacher.get("/api/v1/me/quota").json()["data"]["reserved"] == 0
