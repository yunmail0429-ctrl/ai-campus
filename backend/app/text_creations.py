"""Text-first creative outputs, ready to feed future image and video models."""

import logging

from .adapters import ChatAdapter, ChatSpec, cost
from .errors import AppError
from .model_connections import snapshot_connection
from .resource_contracts import ResourceArtifact, ResourcePlan, ResourceState

logger = logging.getLogger(__name__)


class TextCreationAdapter:
    supports_start_recovery = False

    def __init__(self, settings, kind, config=None):
        self.settings, self.kind, self.config = settings, kind, config

    def prepare(self, data):
        if not self.config:
            raise AppError(503, "RESOURCE_NOT_CONFIGURED", "请管理员启用并设置默认文本模型。")
        snapshot_connection(self.settings, self.config)
        instruction = (
            "你是教学视觉设计师。根据教师需求生成可直接交给图片生成模型的中文提示词。"
            "用 Markdown 输出：主题与用途、正向提示词、画面构图、风格与色彩、画幅建议、"
            "负向提示词、文字与事实核查提醒。不得声称图片已经生成。"
            if self.kind == "image" else
            "你是教学视频编导。根据教师需求生成可供后续视频模型逐镜头生成的中文脚本。"
            "用 Markdown 输出：主题、受众、建议时长、逐镜头编号及每镜头的时长、画面、"
            "旁白/字幕、镜头运动、音效、视频模型提示词，最后列出素材与事实核查提醒。"
            "不得声称视频已经生成。"
        )
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": f"风格：{data.options.style}\n需求：{data.prompt}"},
        ]
        bound = sum(len(item["content"].encode()) + 128 for item in messages)
        if bound + self.config["maxOutputTokens"] > self.config["contextTokens"]:
            raise AppError(422, "CONTEXT_TOO_LONG", "创作需求超过模型上下文，请缩短需求。")
        return ResourcePlan(
            snapshot={**self.config, "adapter": f"{self.kind}-text", "messages": messages, "inputBound": bound},
            maxCredits=cost(bound, self.config["maxOutputTokens"], self.config["inputPrice"], self.config["outputPrice"]),
        )

    async def execute(self, data, snapshot):
        try:
            adapter = ChatAdapter(snapshot_connection(self.settings, snapshot))
            spec = ChatSpec(snapshot["model"], snapshot["maxOutputTokens"], snapshot["timeoutSeconds"])
            chunks, usage = [], None
            async for event in adapter.stream(spec, snapshot["messages"]):
                if event["event"] == "delta":
                    chunks.append(event["delta"])
                elif event["event"] == "usage":
                    usage = event
            content = "".join(chunks).strip()
            if not content or len(content.encode("utf-8")) > 1024 * 1024 or usage is None or usage["inputTokens"] > snapshot["inputBound"]:
                raise ValueError("Invalid text generation result")
            return ResourceState(
                status="succeeded",
                credits=cost(usage["inputTokens"], usage["outputTokens"], snapshot["inputPrice"], snapshot["outputPrice"]),
                artifact=ResourceArtifact(content=(content + "\n").encode("utf-8"), media_type="text/markdown"),
            )
        except Exception as exc:
            logger.warning("text creation failed kind=%s type=%s", self.kind, type(exc).__name__)
            raise AppError(502, "TEXT_CREATION_FAILED", "文案生成失败，本次预占积分将释放。") from exc

    async def cancel(self, job_id, snapshot, key):
        return True
