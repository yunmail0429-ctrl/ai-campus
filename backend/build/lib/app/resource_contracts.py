"""Contracts for asynchronous media adapters; no production demo generator."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .errors import AppError

ResourceKind = Literal["image", "ppt", "video", "music", "avatar", "3d"]
KINDS = ("image", "ppt", "video", "music", "avatar", "3d")


class ResourceOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    style: str = Field(default="", max_length=80)
    pages: int | None = Field(default=None, strict=True, ge=1, le=60)


class ResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: ResourceKind
    prompt: str = Field(min_length=1, max_length=10000)
    options: ResourceOptions = Field(default_factory=ResourceOptions)


class ResourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Adapter-owned immutable, non-secret parameters used by the future worker.
    snapshot: dict
    maxCredits: int = Field(strict=True, ge=0, le=2_000_000_000)


class ResourceAdapter(Protocol):
    # True only when the provider guarantees repeat start(key) returns the same job.
    supports_start_recovery: bool

    def prepare(self, data: ResourceInput) -> ResourcePlan: ...

    async def start(self, data: ResourceInput, snapshot: dict, key: str) -> str: ...

    async def poll(self, job_id: str, snapshot: dict) -> "ResourceState": ...

    # True means confirmed stopped remotely, not just a cancelled HTTP request.
    async def cancel(self, job_id: str | None, snapshot: dict, key: str) -> bool: ...


class ResourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: bytes = Field(min_length=1, max_length=100 * 1024 * 1024)
    media_type: str = Field(max_length=120)


class ResourceState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["running", "succeeded", "failed", "cancelled"]
    progress: int = Field(default=0, ge=0, le=100)
    credits: int = Field(default=0, ge=0, le=2_000_000_000)
    artifact: ResourceArtifact | None = None


# Factories must be implemented against a real provider contract before registration.
# Tests replace this registry explicitly; there is no environment-controlled mock mode.
FACTORIES = {}


def adapter_for(settings, kind):
    factory = FACTORIES.get(kind)
    if factory is None:
        raise AppError(503, "RESOURCE_NOT_CONFIGURED", "该创作类型尚未接入生成服务。")
    return factory(settings)
