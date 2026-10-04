from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from desktop_ai_agent.llm.base import (
    CallInfo,
    LLMError,
    LLMResponse,
    T,
    Thinking,
    ToolCall,
    ToolSpec,
    TransientLLMError,
    Usage,
)

# The SDK warns about AFC on every call even though we never pass Python callables.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

_LEVELS = {
    Thinking.LOW: types.ThinkingLevel.LOW,
    Thinking.MEDIUM: types.ThinkingLevel.MEDIUM,
    Thinking.HIGH: types.ThinkingLevel.HIGH,
}

_BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}


def _is_retryable(exc: BaseException) -> bool:
    """Worth retrying the same model: brief server overload. Timeouts and 429s are not."""
    if isinstance(exc, TransientLLMError):
        return True
    return isinstance(exc, errors.ServerError) and exc.code != 504


def _is_unavailable(exc: BaseException) -> bool:
    """Worth trying another model: overload, rate limit, timeout, model not found."""
    if isinstance(exc, errors.ServerError):
        return True
    if isinstance(exc, errors.ClientError):
        return exc.code in (404, 408, 429)
    return isinstance(exc, httpx.TransportError | TimeoutError)


def _cooldown_for(exc: BaseException, default: float) -> float:
    """How long to avoid a model after it failed, honoring Google's quota hints on 429."""
    if not (isinstance(exc, errors.ClientError) and exc.code == 429):
        return default
    raw = cast(object, exc.details)  # pyright: ignore[reportUnknownMemberType]
    body = cast(dict[str, Any], raw if isinstance(raw, dict) else {})
    error = cast(dict[str, Any], body.get("error", {}))
    cooldown = default
    for item in cast(list[dict[str, Any]], error.get("details", [])):
        delay = str(item.get("retryDelay", "")).removesuffix("s")
        if delay.replace(".", "", 1).isdigit():
            cooldown = max(cooldown, float(delay))
        if "PerDay" in str(item.get("violations", "")):
            cooldown = max(cooldown, 3600.0)
    return cooldown


def _parse(resp: types.GenerateContentResponse, model: str) -> LLMResponse:
    cand = resp.candidates[0] if resp.candidates else None
    if cand is None or cand.content is None or not cand.content.parts:
        reason = cand.finish_reason if cand else None
        if reason in _BLOCKED:
            raise LLMError(f"Response blocked by Gemini ({reason}).")
        raise TransientLLMError(f"Empty response from Gemini (finish_reason={reason}).")

    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for part in cand.content.parts:
        if part.function_call is not None and part.function_call.name:
            call = part.function_call
            calls.append(ToolCall(call.name or "", dict(call.args or {}), call.id))
        elif part.text and not part.thought:
            text_parts.append(part.text)

    meta = resp.usage_metadata
    usage = Usage(
        prompt_tokens=(meta.prompt_token_count or 0) if meta else 0,
        output_tokens=(meta.candidates_token_count or 0) if meta else 0,
        thought_tokens=(meta.thoughts_token_count or 0) if meta else 0,
    )
    text = "".join(text_parts).strip()
    return LLMResponse(cand.content, text, tuple(calls), CallInfo(usage, model))


class GeminiLLM:
    """Gemini client with per-request timeouts, retries, and model fallback.

    If a model keeps failing with overload/availability errors it is put on a short cooldown and
    requests move down the fallback chain, so a capacity spike does not stall the agent.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        fallback_models: Sequence[str] = (),
        attempts_per_model: int = 2,
        request_timeout_s: float = 30.0,
        cooldown_s: float = 60.0,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(request_timeout_s * 1000))
        )
        self._chain = [model, *fallback_models]
        self._attempts = attempts_per_model
        self._cooldown_s = cooldown_s
        self._cooling_until: dict[str, float] = {}

    def _config(self, system: str, thinking: Thinking) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=types.ThinkingConfig(thinking_level=_LEVELS[thinking]),
        )

    def _order(self) -> list[str]:
        now = time.monotonic()
        healthy = [m for m in self._chain if self._cooling_until.get(m, 0.0) <= now]
        cooling = [m for m in self._chain if m not in healthy]
        return healthy + cooling

    async def _attempt(
        self, model: str, contents: Sequence[types.Content], config: types.GenerateContentConfig
    ) -> LLMResponse:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=wait_exponential_jitter(initial=1, max=8),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.aio.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                    model=model, contents=list(contents), config=config
                )
                return _parse(resp, model)
        raise LLMError("unreachable")

    async def _request(
        self, contents: Sequence[types.Content], config: types.GenerateContentConfig
    ) -> LLMResponse:
        failures: list[str] = []
        started = time.monotonic()
        for model in self._order():
            try:
                resp = await self._attempt(model, contents, config)
                info = replace(resp.info, latency_s=time.monotonic() - started)
                return replace(resp, info=info)
            except LLMError as exc:
                if not isinstance(exc, TransientLLMError):
                    raise
                failures.append(f"{model}: {exc}")
                cooldown = self._cooldown_s
            except Exception as exc:
                if not _is_unavailable(exc):
                    raise LLMError(f"{type(exc).__name__}: {exc}") from exc
                failures.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
                cooldown = _cooldown_for(exc, self._cooldown_s)
            self._cooling_until[model] = time.monotonic() + cooldown
        raise LLMError("All models unavailable. " + " | ".join(failures))

    async def generate(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
    ) -> LLMResponse:
        config = self._config(system, thinking)
        if tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters_json_schema=t.parameters,
                        )
                        for t in tools
                    ]
                )
            ]
        return await self._request(contents, config)

    async def generate_structured(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        schema: type[T],
        thinking: Thinking = Thinking.LOW,
    ) -> tuple[T, CallInfo]:
        config = self._config(system, thinking)
        config.response_mime_type = "application/json"
        config.response_json_schema = schema.model_json_schema()
        last_error: Exception | None = None
        for _ in range(2):
            resp = await self._request(contents, config)
            try:
                return schema.model_validate_json(resp.text), resp.info
            except ValidationError as exc:
                last_error = exc
        raise LLMError(f"Model returned invalid {schema.__name__} JSON: {last_error}")
