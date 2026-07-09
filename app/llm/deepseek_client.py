from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from app.config import settings


ChatMessage = Mapping[str, str]


class DeepSeekError(RuntimeError):
    """Base error for DeepSeek client failures."""


class DeepSeekConfigError(DeepSeekError):
    """Raised when required DeepSeek configuration is missing."""


class DeepSeekAPIError(DeepSeekError):
    """Raised when the DeepSeek HTTP API returns an error."""

    def __init__(self, status_code: int | None, message: str, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DeepSeekResponseError(DeepSeekError):
    """Raised when the DeepSeek response shape is not usable."""


@dataclass(frozen=True)
class DeepSeekResponse:
    content: str
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class DeepSeekClient:
    """Small OpenAI-compatible DeepSeek chat client.

    This mirrors the LLM wrapper used by the chiller-station agent so the travel
    agent can share the same environment variables and failure behavior.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        opener: Any | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.deepseek_timeout_seconds
        )
        self.max_retries = (
            max_retries if max_retries is not None else settings.deepseek_max_retries
        )
        self._opener = opener or build_opener()

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = 1024,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> DeepSeekResponse:
        self._ensure_configured()
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        if extra_body:
            payload.update(extra_body)

        raw = self._post_with_retries(payload)
        return self._parse_chat_response(raw)

    def chat_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        json_messages = self._ensure_json_instruction(messages)
        response = self.chat(
            json_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        try:
            return json.loads(_strip_code_fence(response.content))
        except json.JSONDecodeError as exc:
            raise DeepSeekResponseError(
                f"DeepSeek returned non-JSON content: {response.content[:200]}"
            ) from exc

    def _ensure_configured(self) -> None:
        if not self.api_key:
            raise DeepSeekConfigError(
                "DEEPSEEK_API_KEY is not set. Set it in your environment before using LLM features."
            )

    def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: DeepSeekError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._post_once(payload)
            except DeepSeekAPIError as exc:
                last_error = exc
                if not _is_retriable_status(exc.status_code) or attempt >= self.max_retries:
                    raise
            except DeepSeekError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
            time.sleep(min(2**attempt, 8) * 0.25)
        raise last_error or DeepSeekAPIError(None, "DeepSeek request failed")

    def _post_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.chat_completions_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
            try:
                response_body = response.read().decode("utf-8")
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        except HTTPError as exc:
            error_body = _read_error_body(exc)
            raise DeepSeekAPIError(
                exc.code,
                f"DeepSeek API returned HTTP {exc.code}: {exc.reason}",
                error_body,
            ) from exc
        except URLError as exc:
            raise DeepSeekAPIError(None, f"DeepSeek API network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise DeepSeekAPIError(None, "DeepSeek API request timed out") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise DeepSeekResponseError(
                f"DeepSeek returned invalid JSON response: {response_body[:200]}"
            ) from exc

    def _parse_chat_response(self, raw: dict[str, Any]) -> DeepSeekResponse:
        try:
            choice = raw["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekResponseError(f"Unexpected DeepSeek response shape: {raw}") from exc
        if not isinstance(content, str):
            raise DeepSeekResponseError(f"DeepSeek content is not a string: {content!r}")
        return DeepSeekResponse(
            content=content,
            model=raw.get("model"),
            usage=raw.get("usage") or {},
            raw=raw,
        )

    def _ensure_json_instruction(self, messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
        normalized = [dict(message) for message in messages]
        has_json_instruction = any(
            "json" in message.get("content", "").lower() for message in normalized
        )
        if has_json_instruction:
            return normalized
        return [
            {
                "role": "system",
                "content": "Return valid JSON only. Do not wrap JSON in markdown.",
            },
            *normalized,
        ]


def _is_retriable_status(status_code: int | None) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _read_error_body(exc: HTTPError) -> str | None:
    try:
        raw = exc.read()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped
