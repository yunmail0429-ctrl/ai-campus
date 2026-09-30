import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.errors import AppError
from app.resource_contracts import ResourceInput
from app.video_admin import VideoSettings
from app.wanxiang import WanxiangAdapter


def setup_adapter(handler):
    config = VideoSettings(creditsPerSecond=3, active=True).model_dump()
    config["revision"] = 1
    adapter = WanxiangAdapter(
        Settings(wanxiang_api_key=SecretStr("fixture-key")), config, transport=httpx.MockTransport(handler)
    )
    data = ResourceInput(kind="video", prompt="水循环动态演示")
    return adapter, data, adapter.prepare(data).snapshot


def test_official_protocol_download_does_not_forward_credentials():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert request.headers["x-dashscope-async"] == "enable"
            assert json.loads(request.content)["parameters"]["duration"] == 5
            return httpx.Response(200, json={"output": {"task_id": "job-1", "task_status": "PENDING"}})
        if request.url.host.endswith("aliyuncs.com") and request.url.path.startswith("/api/"):
            assert request.headers["authorization"] == "Bearer fixture-key"
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_id": "job-1",
                        "task_status": "SUCCEEDED",
                        "video_url": "https://dashscope-result-sh.oss-accelerate.aliyuncs.com/video.mp4",
                    }
                },
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom" + b"x" * 30)

    adapter, data, snapshot = setup_adapter(handler)

    async def run():
        job = await adapter.start(data, snapshot, "key")
        result = await adapter.poll(job, snapshot)
        assert result.status == "succeeded" and result.credits == 15
        assert result.artifact.media_type == "video/mp4"
        assert await adapter.cancel(job, snapshot, "key")
        assert not await adapter.cancel(None, snapshot, "key")

    asyncio.run(run())
    assert len(calls) == 4
    assert "fixture-key" not in json.dumps(snapshot)
    assert not adapter.supports_start_recovery


@pytest.mark.parametrize(
    "state,expected",
    [
        ("PENDING", "running"),
        ("RUNNING", "running"),
        ("FAILED", "failed"),
        ("CANCELED", "cancelled"),
        ("UNKNOWN", "failed"),
    ],
)
def test_remote_states(state, expected):
    adapter, _, snapshot = setup_adapter(
        lambda request: httpx.Response(200, json={"output": {"task_id": "job-1", "task_status": state}})
    )
    assert asyncio.run(adapter.poll("job-1", snapshot)).status == expected


def test_bad_url_and_rejected_start():
    adapter, data, snapshot = setup_adapter(lambda request: httpx.Response(400, json={"message": "private"}))
    with pytest.raises(AppError) as error:
        asyncio.run(adapter.start(data, snapshot, "key"))
    assert error.value.code == "RESOURCE_REQUEST_REJECTED"
    assert "private" not in error.value.message
    adapter.transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "job-1",
                    "task_status": "SUCCEEDED",
                    "video_url": "http://127.0.0.1/private",
                }
            },
        )
    )
    with pytest.raises(ValueError, match="Untrusted"):
        asyncio.run(adapter.poll("job-1", snapshot))
    with pytest.raises(AppError) as error:
        adapter.prepare(ResourceInput(kind="video", prompt="字" * 5001))
    assert error.value.code == "VIDEO_PROMPT_TOO_LONG"


def test_admin_configuration_permissions_revision_and_credentials(environment):
    admin = environment["client"]("admin")
    teacher = environment["client"]()
    path = "/api/v1/admin/video"
    assert teacher.get(path).status_code == 403
    config = VideoSettings(creditsPerSecond=4).model_dump()
    response = admin.put(path, json={**config, "expectedRevision": 0})
    assert response.status_code == 200
    assert response.json()["data"]["config"]["revision"] == 1
    assert admin.put(path, json={**config, "expectedRevision": 0}).status_code == 409
    assert admin.put(path, json={**config, "active": True, "expectedRevision": 1}).status_code == 422
    environment["app"].state.settings.wanxiang_api_key = SecretStr("fixture-key")
    response = admin.put(path, json={**config, "active": True, "expectedRevision": 1})
    assert response.status_code == 200
    assert "fixture-key" not in response.text
    assert admin.get(path).json()["data"]["config"]["revision"] == 2
    environment["app"].state.settings.resource_payer = "teacher"
    response = teacher.get("/api/v1/resource-tasks/capabilities")
    video = next(item for item in response.json()["data"] if item["kind"] == "video")
    # The teacher video entry currently generates a text storyboard; media
    # settings remain stored for a later video-model stage.
    assert video["video"] is None
    assert not video["available"]
    assert "fixture-key" not in response.text and "dashscope" not in response.text
    other = environment["client"]("teacher", "school-b")
    assert (
        next(
            item
            for item in other.get("/api/v1/resource-tasks/capabilities").json()["data"]
            if item["kind"] == "video"
        )["video"]
        is None
    )
    assert environment["client"]("teacher", "school-b").get(path).status_code == 403
