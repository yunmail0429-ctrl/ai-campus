"""Local or hosted OpenAI-compatible embeddings; no teacher quota mutation."""

import asyncio
import hashlib
import json
import math
from datetime import timedelta

import httpx

from .adapters import connection_for, provider_error
from .errors import AppError
from .models import EmbeddingConfig, EmbeddingUsage, now
from .repository import Repository
from .safety import redact


def snapshot(row):
    return {
        "provider": row.provider,
        "model": row.model,
        "dimensions": row.dimensions,
        "requestDimensions": row.request_dimensions,
        "maxInputTokens": row.max_input_tokens,
        "timeoutSeconds": row.timeout_seconds,
        "minScore": row.min_score,
        "revision": row.revision,
    }


def active_config(repo):
    row = repo.one(EmbeddingConfig, EmbeddingConfig.active.is_(True))
    return snapshot(row) if row else None


def index_key(settings, spec):
    connection = connection_for(settings, spec["provider"])
    # Changing endpoint/model/dimensions invalidates vectors; rotating a secret does not.
    return hashlib.sha256(
        json.dumps(
            [
                connection.base_url,
                spec["provider"],
                spec["model"],
                spec["dimensions"],
                spec["requestDimensions"],
                "redaction-v1",
            ]
        ).encode()
    ).hexdigest()


def invalid():
    return AppError(503, "INVALID_EMBEDDING_RESPONSE", "向量模型返回的数据或计量无效，请联系管理员。")


class EmbeddingAdapter:
    def __init__(self, connection, transport=None):
        self.connection, self.transport = connection, transport

    async def embed(self, spec, texts):
        texts = [redact(text).strip() for text in texts]
        if (
            not texts
            or len(texts) > 16
            or any(not text or len(text.encode()) > spec["maxInputTokens"] for text in texts)
        ):
            raise AppError(422, "EMBEDDING_INPUT_TOO_LONG", "资料分段或检索问题超过向量模型输入限制。")
        headers = {"Accept": "application/json"}
        if self.connection.key:
            headers["Authorization"] = "Bearer " + self.connection.key
        payload = {"model": spec["model"], "input": texts, "encoding_format": "float"}
        if spec["requestDimensions"]:
            payload["dimensions"] = spec["dimensions"]
        try:
            async with (
                asyncio.timeout(spec["timeoutSeconds"]),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(spec["timeoutSeconds"], connect=5),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client,
                client.stream(
                    "POST", self.connection.base_url + "/embeddings", headers=headers, json=payload
                ) as response,
            ):
                if response.status_code != 200:
                    raise provider_error(response.status_code)
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > 8 * 1024 * 1024:
                        raise invalid()
                data = json.loads(raw)
            rows = data["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise invalid()
            vectors = [None] * len(texts)
            for row in rows:
                position, vector = row["index"], row["embedding"]
                if (
                    type(position) is not int
                    or not 0 <= position < len(texts)
                    or vectors[position] is not None
                ):
                    raise invalid()
                if (
                    not isinstance(vector, list)
                    or len(vector) != spec["dimensions"]
                    or any(
                        type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in vector
                    )
                ):
                    raise invalid()
                norm = math.hypot(*vector)
                if norm <= 1e-12:
                    raise invalid()
                vectors[position] = [v / norm for v in vector]
            tokens = data["usage"]["prompt_tokens"]
            if type(tokens) is not int or not 0 < tokens <= sum(len(t.encode()) + 128 for t in texts):
                raise invalid()
            return vectors, tokens
        except (TimeoutError, httpx.TimeoutException):
            raise AppError(503, "EMBEDDING_TIMEOUT", "向量模型请求超时，请稍后重试。") from None
        except httpx.HTTPError:
            raise AppError(503, "EMBEDDING_UNAVAILABLE", "向量模型暂时不可用，请稍后重试。") from None
        except (ValueError, KeyError, TypeError, OverflowError):
            raise invalid() from None


async def measured_embed(sessions, settings, tenant_id, spec, texts, purpose, reference):
    connection = connection_for(settings, spec["provider"])
    with sessions.begin() as session:
        usage = Repository(session, tenant_id).add(
            EmbeddingUsage,
            purpose=purpose,
            reference_id=reference,
            provider=spec["provider"],
            model=spec["model"],
            revision=spec["revision"],
            deadline_at=now() + timedelta(seconds=spec["timeoutSeconds"] + 30),
        )
        identity = usage.id
    result, failure = None, None
    try:
        result = await EmbeddingAdapter(connection).embed(spec, texts)
        return result[0]
    except AppError as exc:
        failure = exc.code
        raise
    finally:
        # No prompt/file text or provider exception is written to this accounting table.
        with sessions.begin() as session:
            row = Repository(session, tenant_id).require(EmbeddingUsage, EmbeddingUsage.id == identity)
            row.status = "completed" if result else ("failed" if failure else "unknown")
            row.input_tokens = result[1] if result else None
            row.error_code = failure
