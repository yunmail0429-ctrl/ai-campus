"""Text-model generation of editable teaching decks using administrator prices."""

import logging
import traceback

from pydantic import BaseModel, ConfigDict, Field

from .adapters import ChatAdapter, ChatSpec, cost
from .errors import AppError
from .model_connections import connection_snapshot, snapshot_connection
from .models import ModelConfig, ModelRoute
from .ppt_quality import PptQualityError, inspect_pptx
from .ppt_render import DeckContent, render_deck
from .resource_contracts import ResourceArtifact, ResourcePlan, ResourceState

logger = logging.getLogger(__name__)


class OutlineSlide(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=48)
    purpose: str = Field(min_length=1, max_length=120)
    points: list[str] = Field(min_length=1, max_length=4)


class PptOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slides: list[OutlineSlide] = Field(min_length=1, max_length=60)

    def checked(self, pages):
        if len(self.slides) != pages or any(
            not point.strip() or len(point) > 100
            for slide in self.slides for point in slide.points
        ):
            raise ValueError("Invalid PPT outline")
        return self


def model_snapshot(repo):
    route = repo.one(ModelRoute, ModelRoute.purpose == "chat")
    model = (
        repo.one(ModelConfig, ModelConfig.id == route.model_id, ModelConfig.active.is_(True))
        if route
        else None
    )
    if not model:
        return None
    return {
        "provider": model.provider,
        **connection_snapshot(model),
        "model": model.model,
        "revision": model.revision,
        "contextTokens": model.context_tokens,
        "maxOutputTokens": model.max_output_tokens,
        "timeoutSeconds": min(model.timeout_seconds, 120),
        "inputPrice": model.input_price,
        "outputPrice": model.output_price,
    }


class PptAdapter:
    supports_start_recovery = False

    def __init__(self, settings, config=None):
        self.settings, self.config = settings, config

    def prepare(self, data):
        if not self.config:
            raise AppError(503, "RESOURCE_NOT_CONFIGURED", "请管理员启用并设置默认文本模型后生成 PPT。")
        snapshot_connection(self.settings, self.config)
        pages = data.options.pages or 10
        outline_messages = [
            {"role": "system", "content": (
                '你是教学课件规划师。只返回 JSON：{"slides":[{"title":"页标题",'
                '"purpose":"本页教学作用","points":["关键内容"]}]}。'
                '恰好按要求页数规划；每页1至4条简短要点。安排引入、讲解、活动、练习和总结；'
                '不要编造来源、事实、数值或公式。'
            )},
            {"role": "user", "content": f"规划恰好 {pages} 页教学课件。风格：{data.options.style}\n需求：{data.prompt}"},
        ]
        messages = [
            {
                "role": "system",
                "content": '你是教学课件设计师。仅返回 JSON，不使用代码围栏。结构为 {"slides":[{"title":"标题",'
                '"points":["要点"],"notes":"教师讲解备注"}]}。每页标题1至48字，1至5条要点，'
                "每条1至100字，备注不超过3000字。内容准确，适合课堂，包含讲解、活动和练习。"
                "如果需求需要函数图像或连续数值关系，应在相应页添加可编辑的 chart 字段："
                '{"x_axis":"横轴名","y_axis":"纵轴名","series":[{"name":"曲线名",'
                '"points":[[-2,4],[-1.5,2.25],[-1,1],[-0.5,0.25],[0,0],[0.5,0.25],[1,1],[1.5,2.25],[2,4]]}]}。'
                "图表页最多3条要点，每条不超过50字；每条曲线9至30个按 x 递增且不重复的真实数值点，"
                "最多2条曲线。不得编造与公式不符的数据；非数值主题不要添加 chart。",
            },
            {
                "role": "user",
                "content": f"生成恰好 {pages} 页课件。风格：{data.options.style}\n需求：{data.prompt}",
            },
        ]
        bound = sum(len(item["content"].encode()) + 128 for item in messages) + pages * 2200 + 128
        outline_bound = sum(len(item["content"].encode()) + 128 for item in outline_messages)
        # Reasoning models may spend part of the output budget before emitting JSON.
        # A 900-token cap truncated real one-page MiMo outlines.
        outline_output = min(self.config["maxOutputTokens"], max(2400, pages * 220))
        repair_output = min(self.config["maxOutputTokens"], 1600)
        repair_input_bound = 5000
        if max(bound + self.config["maxOutputTokens"], outline_bound + outline_output, repair_input_bound + repair_output) > self.config["contextTokens"]:
            raise AppError(422, "CONTEXT_TOO_LONG", "课件需求超过模型上下文，请缩短需求。")
        return ResourcePlan(
            snapshot={
                **self.config,
                "adapter": "editable-ppt",
                "messages": messages,
                "pages": pages,
                "inputBound": bound,
                "outlineMessages": outline_messages,
                "outlineInputBound": outline_bound,
                "outlineOutputTokens": outline_output,
                "repairOutputTokens": repair_output,
                "repairInputBound": repair_input_bound,
            },
            maxCredits=cost(bound, self.config["maxOutputTokens"], self.config["inputPrice"], self.config["outputPrice"])
            + cost(outline_bound, outline_output, self.config["inputPrice"], self.config["outputPrice"])
            + 2 * cost(repair_input_bound, repair_output, self.config["inputPrice"], self.config["outputPrice"]),
        )

    async def repair_slide(self, snapshot, outline_slide, slide, reason):
        messages = [
            {"role": "system", "content": (
                '你是教学课件修复助手。只返回单页 JSON：{"slides":[{"title":"标题",'
                '"points":["要点"],"notes":"讲解备注"}]}。保持原有教学目标和事实，'
                '缩短过密文字，避免页面空白、边缘裁切或越界。'
            )},
            {"role": "user", "content": (
                f"已确认大纲：{outline_slide.model_dump_json()}\n"
                f"当前页面：{slide.model_dump_json()}\n问题：{reason}"
            )},
        ]
        adapter = ChatAdapter(snapshot_connection(self.settings, snapshot))
        spec = ChatSpec(snapshot["model"], snapshot["repairOutputTokens"], snapshot["timeoutSeconds"])
        text, usage = "", None
        async for event in adapter.stream(spec, messages):
            if event["event"] == "delta":
                text += event["delta"]
            elif event["event"] == "usage":
                usage = event
        if usage is None or usage["inputTokens"] > snapshot["repairInputBound"]:
            raise ValueError("Invalid repair usage")
        repaired = DeckContent.model_validate_json(text)
        if len(repaired.slides) != 1:
            raise ValueError("Repair must return one slide")
        return repaired.slides[0], cost(
            usage["inputTokens"], usage["outputTokens"], snapshot["inputPrice"], snapshot["outputPrice"]
        )

    async def generate_outline(self, snapshot):
        try:
            adapter = ChatAdapter(snapshot_connection(self.settings, snapshot))
            spec = ChatSpec(snapshot["model"], snapshot["outlineOutputTokens"], snapshot["timeoutSeconds"])
            text, usage = "", None
            async for event in adapter.stream(spec, snapshot["outlineMessages"]):
                if event["event"] == "delta":
                    text += event["delta"]
                elif event["event"] == "usage":
                    usage = event
            if usage is None or usage["inputTokens"] > snapshot["outlineInputBound"]:
                raise ValueError("Invalid outline usage")
            outline = PptOutline.model_validate_json(text).checked(snapshot["pages"])
            credits = cost(usage["inputTokens"], usage["outputTokens"], snapshot["inputPrice"], snapshot["outputPrice"])
            return outline.model_dump(), credits
        except AppError:
            raise
        except Exception as exc:
            logger.warning("ppt outline failed type=%s", type(exc).__name__)
            raise AppError(502, "PPT_GENERATION_FAILED", "课件大纲生成失败，预占积分将释放。") from exc

    async def execute(self, data, snapshot):
        try:
            adapter = ChatAdapter(snapshot_connection(self.settings, snapshot))
            spec = ChatSpec(snapshot["model"], snapshot["maxOutputTokens"], snapshot["timeoutSeconds"])
            text, usage = "", None
            outline = PptOutline.model_validate(snapshot["approvedOutline"]).checked(snapshot["pages"])
            messages = [*snapshot["messages"], {
                "role": "user", "content": "严格按已确认的逐页大纲生成，不增删页或虚构事实：" + outline.model_dump_json(),
            }]
            async for event in adapter.stream(spec, messages):
                if event["event"] == "delta":
                    text += event["delta"]
                elif event["event"] == "usage":
                    usage = event
            if usage is None or usage["inputTokens"] > snapshot["inputBound"]:
                raise ValueError("Invalid usage")
            deck = DeckContent.model_validate_json(text)
            if len(deck.slides) != snapshot["pages"]:
                raise ValueError("Incorrect page count")
            content = render_deck(deck)
            repair_credits = 0
            for attempt in range(3):
                try:
                    inspect_pptx(content, snapshot["pages"])
                    break
                except PptQualityError as issue:
                    if issue.page_index is None or attempt == 2:
                        raise
                    index = issue.page_index
                    replacement, credits = await self.repair_slide(
                        snapshot, outline.slides[index], deck.slides[index], str(issue)
                    )
                    repair_credits += credits
                    deck.slides[index] = replacement
                    content = render_deck(deck)
            return ResourceState(
                status="succeeded",
                credits=snapshot["outlineCredits"] + repair_credits + cost(
                    usage["inputTokens"],
                    usage["outputTokens"],
                    snapshot["inputPrice"],
                    snapshot["outputPrice"],
                ),
                artifact=ResourceArtifact(
                    content=content,
                    media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ),
            )
        except Exception as exc:
            locations = [f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)[-4:]]
            logger.warning("ppt generation failed type=%s locations=%s", type(exc).__name__, locations)
            raise AppError(502, "PPT_GENERATION_FAILED", "PPT 生成或页面检查失败；已生成的大纲按实际用量结算，其余预占积分将释放。") from exc

    async def cancel(self, job_id, snapshot, key):
        return True
