"""Resolve model-specific connections without exposing stored credentials."""

import ipaddress
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from .adapters import Connection, connection_for
from .config import ProviderProfile
from .errors import AppError


def cipher(settings):
    secret = settings.model_credential_key
    if secret is None:
        raise AppError(503, "MODEL_KEY_STORAGE_UNAVAILABLE", "模型密钥存储尚未配置。")
    try:
        return Fernet(secret.get_secret_value().encode())
    except (ValueError, TypeError) as exc:
        raise AppError(503, "MODEL_KEY_STORAGE_UNAVAILABLE", "模型密钥存储配置无效。") from exc


def validate_url(url, settings=None):
    try:
        profile = ProviderProfile(label="自定义", base_url=url, tenant_slugs=["custom"])
    except (ValidationError, ValueError) as exc:
        raise AppError(422, "INVALID_MODEL_API_URL", "请输入有效的 HTTPS API 基础地址。") from exc
    parts = urlsplit(profile.base_url)
    hostname = parts.hostname or ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not (address.is_global or (address.is_loopback and settings and settings.app_env in ("local", "dev", "test"))):
        raise AppError(422, "INVALID_MODEL_API_URL", "API 地址不能指向内部或保留网络地址。")
    if hostname == "localhost" and settings and settings.app_env not in ("local", "dev", "test"):
        raise AppError(422, "INVALID_MODEL_API_URL", "当前环境不能使用本机 API 地址。")
    if settings and settings.app_env in ("production", "staging") and parts.scheme != "https":
        raise AppError(422, "INVALID_MODEL_API_URL", "当前环境的 API 地址必须使用 HTTPS。")
    return profile.base_url


def encrypt_key(settings, value):
    return cipher(settings).encrypt(value.encode()).decode()


def model_connection(settings, config):
    if config.api_base_url:
        if not config.encrypted_api_key:
            raise AppError(503, "MODEL_CREDENTIAL_MISSING", "模型 API Key 尚未配置。")
        return snapshot_connection(settings, {
            "provider": config.provider,
            "apiBaseUrl": config.api_base_url,
            "encryptedApiKey": config.encrypted_api_key,
        })
    return connection_for(settings, config.provider)


def snapshot_connection(settings, snapshot):
    url = snapshot.get("apiBaseUrl")
    if not url:
        return connection_for(settings, snapshot["provider"])
    try:
        key = cipher(settings).decrypt(snapshot["encryptedApiKey"].encode()).decode()
    except (InvalidToken, KeyError, UnicodeDecodeError) as exc:
        raise AppError(503, "MODEL_CREDENTIAL_MISSING", "模型 API Key 无法读取，请管理员重新配置。") from exc
    return Connection(
        validate_url(url, settings),
        key,
        token_limit_parameter="max_tokens" if snapshot.get("provider") in ("deepseek", "doubao", "glm", "claude") else "max_completion_tokens",
    )


def connection_snapshot(config):
    return {"apiBaseUrl": config.api_base_url, "encryptedApiKey": config.encrypted_api_key}
