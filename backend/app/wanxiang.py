"""Wan 2.7 official asynchronous text-to-video protocol. No automatic start retries."""

import json
import re
from urllib.parse import urlsplit

import httpx

from .errors import AppError
from .resource_contracts import ResourceArtifact, ResourcePlan, ResourceState
from .video_admin import VideoSettings, configured


class WanxiangAdapter:
    supports_start_recovery = False

    def __init__(self, settings, config=None, transport=None):
        self.settings, self.config, self.transport = settings, config, transport

    def prepare(self, data):
        if not self.config or not self.config.get("active") or not configured(self.settings):
            raise AppError(503, "VIDEO_NOT_CONFIGURED", "管理员尚未启用万相视频模型或配置凭据。")
        config = VideoSettings.model_validate({k: v for k, v in self.config.items() if k != "revision"})
        prompt = self.prompt(data)
        if len(prompt) > 5000:
            raise AppError(422, "VIDEO_PROMPT_TOO_LONG", "视频描述与风格合计不能超过 5000 字。")
        credits = config.duration * config.creditsPerSecond
        return ResourcePlan(
            snapshot={**self.config, "adapter": "wanxiang-v2.7", "credits": credits}, maxCredits=credits
        )

    @staticmethod
    def prompt(data):
        return data.prompt + ("\n风格：" + data.options.style if data.options.style else "")

    def client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def request(self, method, path, snapshot, payload=None):
        # Revalidate persisted endpoint before attaching credentials.
        endpoint = VideoSettings.model_fields["endpoint"].annotation.__args__
        if snapshot.get("endpoint") not in endpoint or not configured(self.settings):
            raise AppError(503, "VIDEO_NOT_CONFIGURED", "视频服务连接尚未配置。")
        headers = {"Authorization": "Bearer " + self.settings.wanxiang_api_key.get_secret_value()}
        if method == "POST":
            headers["X-DashScope-Async"] = "enable"
        async with (
            self.client() as client,
            client.stream(method, snapshot["endpoint"] + path, headers=headers, json=payload) as response,
        ):
            if method == "POST" and 400 <= response.status_code < 500 and response.status_code != 408:
                raise AppError(502, "RESOURCE_REQUEST_REJECTED", "视频服务拒绝请求，请管理员检查配置或额度。")
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1024 * 1024:
                    raise ValueError("Oversized video status response")
        result = json.loads(content)
        if not isinstance(result, dict) or not isinstance(result.get("output"), dict):
            raise TypeError("Invalid video status response")
        return result["output"]

    async def start(self, data, snapshot, key):
        output = await self.request(
            "POST",
            "/api/v1/services/aigc/video-generation/video-synthesis",
            snapshot,
            {
                "model": snapshot["model"],
                "input": {"prompt": self.prompt(data)},
                "parameters": {
                    "resolution": snapshot["resolution"],
                    "ratio": snapshot["ratio"],
                    "duration": snapshot["duration"],
                    "watermark": True,
                    "prompt_extend": True,
                },
            },
        )
        job = output.get("task_id")
        if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,200}", job):
            raise ValueError("Missing video task ID")
        return job

    async def status(self, job_id, snapshot):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,200}", job_id):
            raise ValueError("Invalid video task ID")
        output = await self.request("GET", "/api/v1/tasks/" + job_id, snapshot)
        if output.get("task_id") != job_id:
            raise ValueError("Video task ID mismatch")
        return output

    async def poll(self, job_id, snapshot):
        output = await self.status(job_id, snapshot)
        state = output.get("task_status")
        if state in ("PENDING", "RUNNING"):
            return ResourceState(status="running", progress=10 if state == "PENDING" else 40)
        if state in ("FAILED", "UNKNOWN"):
            return ResourceState(status="failed")
        if state == "CANCELED":
            return ResourceState(status="cancelled")
        if state != "SUCCEEDED":
            raise ValueError("Unknown video task state")
        url = output.get("video_url", "")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or not re.fullmatch(
                r"[a-z0-9-]+\.oss-(?:accelerate|[a-z0-9-]+)\.aliyuncs\.com", parts.hostname or ""
            )
        ):
            raise ValueError("Untrusted video artifact origin")
        content = bytearray()
        # Download with a fresh client: never forward the API credential to OSS.
        async with self.client() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 100 * 1024 * 1024:
                    raise ValueError("Video exceeds output size limit")
        return ResourceState(
            status="succeeded",
            progress=100,
            credits=snapshot["credits"],
            artifact=ResourceArtifact(content=bytes(content), media_type="video/mp4"),
        )

    async def cancel(self, job_id, snapshot, key):
        if not job_id:
            return False  # Submission result unknown; never claim remote cancellation.
        output = await self.status(job_id, snapshot)
        return output.get("task_status") in ("SUCCEEDED", "FAILED", "CANCELED")
