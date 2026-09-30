"""Provider-neutral chat events and the OpenAI-compatible HTTP transport.

Endpoint URLs and credentials come only from trusted deployment settings. Never
forward raw HTTP exceptions or upstream response bodies to users or audit logs.
"""

import asyncio
import json
from dataclasses import dataclass, field

import httpx

from .errors import AppError


@dataclass(frozen=True)
class Connection:
    base_url: str
    key: str = field(repr=False)
    token_limit_parameter: str = "max_completion_tokens"


@dataclass(frozen=True)
class ChatSpec:
    model: str
    max_output_tokens: int
    timeout_seconds: int


def connection_for(settings, provider_id):
    profile = settings.model_providers.get(provider_id)
    if profile is None or settings.tenant_slug not in profile.tenant_slugs:
        raise AppError(503, "PROVIDER_NOT_CONFIGURED", "此学校尚未配置该模型供应商。")
    secret = settings.model_api_keys.get(profile.secret_ref) if profile.secret_ref else None
    if profile.secret_ref and (secret is None or not secret.get_secret_value().strip()):
        raise AppError(503, "MODEL_CREDENTIAL_MISSING", "模型凭据尚未配置，请联系管理员。")
    return Connection(
        profile.base_url,
        secret.get_secret_value() if secret else "",
        profile.token_limit_parameter,
    )


def cost(input_tokens, output_tokens, input_price, output_price):
    """Integer credits per million tokens; round the combined cost up once."""
    values = (input_tokens, output_tokens, input_price, output_price)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Token counts and rates must be nonnegative integers")
    return (input_tokens * input_price + output_tokens * output_price + 999_999) // 1_000_000


def provider_error(status):
    if status in (401, 403):
        return AppError(503, "MODEL_AUTH_FAILED", "模型连接认证失败，请联系管理员检查凭据。")
    if status == 429:
        return AppError(503, "MODEL_RATE_LIMITED", "模型服务繁忙，请稍后重试。")
    return AppError(503, "MODEL_UNAVAILABLE", "模型服务暂时不可用，请联系管理员。")


def invalid_stream():
    return AppError(503, "INVALID_MODEL_RESPONSE", "模型响应不完整或缺少有效计量，未扣除积分。")


class ChatAdapter:
    def __init__(self, connection: Connection, transport=None):
        self.connection, self.transport = connection, transport

    async def stream(self, spec: ChatSpec, messages):
        headers = {"Accept": "text/event-stream"}
        if self.connection.key:
            headers["Authorization"] = "Bearer " + self.connection.key
        payload = {
            "model": spec.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            self.connection.token_limit_parameter: spec.max_output_tokens,
        }
        try:
            async with (
                asyncio.timeout(spec.timeout_seconds),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client,
                client.stream(
                    "POST",
                    self.connection.base_url + "/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response,
            ):
                if response.status_code != 200:
                    raise provider_error(response.status_code)
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise invalid_stream()
                buffer, data, total, output_size = "", [], 0, 0
                ended, usage_seen, finish_seen, text_seen = False, False, False, False
                async for chunk in response.aiter_text():
                    total += len(chunk)
                    buffer += chunk
                    if total > 4_000_000 or len(buffer) > 1_000_000:
                        raise invalid_stream()
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.removesuffix("\r")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                        elif line == "" and data:
                            raw, data = "\n".join(data), []
                            if raw == "[DONE]":
                                ended = True
                                break
                            try:
                                body = json.loads(raw)
                            except (ValueError, TypeError):
                                raise invalid_stream() from None
                            if not isinstance(body, dict) or body.get("error"):
                                raise invalid_stream()
                            choices = body.get("choices", [])
                            if not isinstance(choices, list) or len(choices) > 1:
                                raise invalid_stream()
                            for choice in choices:
                                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                                    raise invalid_stream()
                                delta = choice.get("delta", {})
                                if not isinstance(delta, dict):
                                    raise invalid_stream()
                                if delta.get("tool_calls") or delta.get("refusal"):
                                    raise AppError(
                                        503, "MODEL_UNSUPPORTED_OUTPUT", "模型未返回可用的教学文本。"
                                    )
                                content = delta.get("content")
                                if content is not None:
                                    if not isinstance(content, str) or finish_seen or usage_seen:
                                        raise invalid_stream()
                                    output_size += len(content)
                                    if output_size > 500_000:
                                        raise invalid_stream()
                                    if content:
                                        text_seen = True
                                        yield {"event": "delta", "delta": content}
                                reason = choice.get("finish_reason")
                                if reason is not None:
                                    if reason not in ("stop", "length") or finish_seen:
                                        raise invalid_stream()
                                    finish_seen = True
                            usage = body.get("usage")
                            if usage is not None:
                                if not isinstance(usage, dict) or usage_seen or not finish_seen:
                                    raise invalid_stream()
                                prompt, completion = (
                                    usage.get("prompt_tokens"),
                                    usage.get("completion_tokens"),
                                )
                                if (
                                    type(prompt) is not int
                                    or type(completion) is not int
                                    or prompt < 0
                                    or completion < 0
                                    or prompt > 10_000_000
                                    or completion > spec.max_output_tokens
                                ):
                                    raise invalid_stream()
                                usage_seen = True
                                yield {"event": "usage", "inputTokens": prompt, "outputTokens": completion}
                    if ended:
                        break
                if not (ended and usage_seen and finish_seen and text_seen):
                    raise invalid_stream()
        except (TimeoutError, httpx.TimeoutException):
            raise AppError(503, "MODEL_TIMEOUT", "模型响应超时，请稍后重试。") from None
        except httpx.HTTPError:
            raise AppError(503, "MODEL_CONNECTION_FAILED", "无法连接模型服务，请联系管理员。") from None
