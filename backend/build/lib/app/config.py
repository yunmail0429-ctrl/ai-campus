from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)
    secret_ref: str | None = Field(default=None, max_length=80)
    tenant_slugs: list[str] = Field(min_length=1)
    token_limit_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_completion_tokens"

    @model_validator(mode="after")
    def valid_endpoint(self):
        parts = urlsplit(self.base_url)
        if any(character.isspace() or ord(character) < 32 for character in self.base_url):
            raise ValueError("Provider URL cannot contain whitespace or control characters")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("Invalid provider port")
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "Provider URL must be an HTTP(S) API base without credentials, query or fragment"
            )
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Non-HTTPS model endpoints are only supported on loopback")
        self.base_url = self.base_url.rstrip("/")
        return self


class StorageProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]+[a-z0-9]$")
    endpoint_url: str | None = None
    region: str = "us-east-1"
    access_key: SecretStr = Field(repr=False)
    secret_key: SecretStr = Field(repr=False)
    encryption: Literal["AES256", "aws:kms"] | None = None

    @model_validator(mode="after")
    def endpoint(self):
        if self.endpoint_url:
            parts = urlsplit(self.endpoint_url)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.path not in ("", "/")
            ):
                raise ValueError("Storage endpoint must be an HTTP(S) origin without credentials")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_env: Literal["local", "test", "dev", "staging", "production"] = "local"
    database_url: str = "sqlite:///./local-data/xflj.db"
    tenant_slug: str = "xflj"
    cookie_name: str = "xflj_session"
    cookie_secure: bool = False
    session_hours: int = Field(default=24, ge=1, le=720)
    allowed_origins: list[str] = ["http://localhost:3100", "http://127.0.0.1:3100"]
    max_request_bytes: int = 2 * 1024 * 1024
    login_attempts: int = 10
    login_window_seconds: int = 600
    model_providers: dict[str, ProviderProfile] = Field(default_factory=dict)
    model_api_keys: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    storage: StorageProfile | None = Field(default=None, repr=False)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    clamav_host: str | None = None
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout: int = Field(default=45, ge=1, le=120)
    redis_url: SecretStr = Field(default=SecretStr("redis://127.0.0.1:56379/0"), repr=False)
    resource_payer: Literal["school", "teacher"] | None = "teacher"

    @model_validator(mode="after")
    def production_guard(self):
        if self.app_env in ("production", "staging"):
            if (
                self.storage
                and self.storage.endpoint_url
                and not self.storage.endpoint_url.startswith("https://")
            ):
                raise ValueError("staging/production storage endpoints require HTTPS")
            if any(
                not provider.base_url.startswith("https://") for provider in self.model_providers.values()
            ):
                raise ValueError("staging/production model endpoints require HTTPS")
            if not self.cookie_secure or not self.database_url.startswith("postgresql"):
                raise ValueError("staging/production require PostgreSQL and secure cookies")
            if any(not value.startswith("https://") for value in self.allowed_origins):
                raise ValueError("staging/production require HTTPS origins")
        return self
