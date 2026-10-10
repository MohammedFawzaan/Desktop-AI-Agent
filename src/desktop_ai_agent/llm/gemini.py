from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any, TypeVar, cast

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

_STT_PROMPT = (
    "Transcribe the speech in this audio verbatim. Output only the transcript, nothing else. "
    "If there is no intelligible speech, output exactly: <silence>"
)
_NO_SPEECH = {"silence", "no speech"}

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


R = TypeVar("R")


def _rate_from_mime(mime: str | None) -> int:
    """'audio/l16; codec=pcm; rate=24000' -> 24000 (the TTS models default to 24 kHz)."""
    for part in (mime or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == "rate" and value.isdigit():
            return int(value)
    return 24_000


def _audio_of(resp: types.GenerateContentResponse) -> tuple[bytes, str | None] | None:
    cand = resp.candidates[0] if resp.candidates else None
    for part in (cand.content.parts if cand and cand.content else None) or []:
        if part.inline_data is not None and part.inline_data.data:
            return part.inline_data.data, part.inline_data.mime_type
    return None


def _usage_of(resp: types.GenerateContentResponse) -> Usage | None:
    meta = resp.usage_metadata
    if meta is None:
        return None
    return Usage(
        meta.prompt_token_count or 0,
        meta.candidates_token_count or 0,
        meta.thoughts_token_count or 0,
    )


class TtsStream:
    """16-bit mono PCM from the text-to-speech model, handed over while it is still generating."""

    def __init__(
        self,
        *,
        first: bytes,
        sample_rate: int,
        rest: AsyncIterator[types.GenerateContentResponse],
        model: str,
        started: float,
        usage: Usage | None,
    ) -> None:
        self.sample_rate = sample_rate
        self.first_audio_s = time.monotonic() - started
        self._first = first
        self._rest = rest
        self._model = model
        self._started = started
        self._usage = usage or Usage()
        self._finished: float | None = None

    @property
    def info(self) -> CallInfo:
        end = self._finished if self._finished is not None else time.monotonic()
        return CallInfo(self._usage, self._model, end - self._started)

    async def chunks(self) -> AsyncIterator[bytes]:
        yield self._first
        async for resp in self._rest:
            usage = _usage_of(resp)
            if usage is not None:
                self._usage = usage
            audio = _audio_of(resp)
            if audio is not None:
                yield audio[0]
        self._finished = time.monotonic()


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
        stt_models: Sequence[str] = (),
        tts_models: Sequence[str] = (),
        tts_voice: str = "Kore",
        attempts_per_model: int = 2,
        hedge_after_s: float | None = 7.0,
        request_timeout_s: float = 30.0,
        cooldown_s: float = 60.0,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(request_timeout_s * 1000))
        )
        self._chain = [model, *fallback_models]
        self._stt_chain = list(stt_models)
        self._tts_chain = list(tts_models)
        self._tts_voice = tts_voice
        self._attempts = attempts_per_model
        self._hedge_after_s = hedge_after_s
        self._cooldown_s = cooldown_s
        self._cooling_until: dict[str, float] = {}
        self.hedged = 0  # how many slow calls were raced with a duplicate

    def _config(self, system: str, thinking: Thinking) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=types.ThinkingConfig(thinking_level=_LEVELS[thinking]),
        )

    def _order(self, chain: Sequence[str]) -> list[str]:
        now = time.monotonic()
        healthy = [m for m in chain if self._cooling_until.get(m, 0.0) <= now]
        cooling = [m for m in chain if m not in healthy]
        return healthy + cooling

    def _hedge_delay(self, config: types.GenerateContentConfig) -> float | None:
        """How long to wait before sending a duplicate request; long thinking is not hedged."""
        if self._hedge_after_s is None:
            return None
        level = config.thinking_config.thinking_level if config.thinking_config else None
        if level == types.ThinkingLevel.HIGH:
            return None
        return self._hedge_after_s * (1.7 if level == types.ThinkingLevel.MEDIUM else 1.0)

    async def _hedged(
        self, model: str, contents: Sequence[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        """Gemini occasionally takes 15+ s on a call that normally takes 2 s. If a request is
        unusually slow, send the same request again and use whichever answers first."""

        def request() -> asyncio.Task[types.GenerateContentResponse]:
            return asyncio.ensure_future(
                self._client.aio.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                    model=model, contents=list(contents), config=config
                )
            )

        first = request()
        delay = self._hedge_delay(config)
        if delay is None:
            return await first
        done, _ = await asyncio.wait({first}, timeout=delay)
        if done:
            return first.result()
        self.hedged += 1
        pending: set[asyncio.Task[types.GenerateContentResponse]] = {first, request()}
        error: BaseException | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() is None:
                        return task.result()
                    error = task.exception()
        finally:
            for task in pending:
                task.cancel()
        assert error is not None
        raise error

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
                resp = await self._hedged(model, contents, config)
                return _parse(resp, model)
        raise LLMError("unreachable")

    async def _across(self, chain: Sequence[str], run: Callable[[str], Awaitable[R]]) -> R:
        """Run `run(model)` down the fallback chain, cooling down models that are unavailable."""
        failures: list[str] = []
        for model in self._order(chain):
            try:
                return await run(model)
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

    async def _request(
        self,
        contents: Sequence[types.Content],
        config: types.GenerateContentConfig,
        chain: Sequence[str] | None = None,
    ) -> LLMResponse:
        started = time.monotonic()

        async def run(model: str) -> LLMResponse:
            resp = await self._attempt(model, contents, config)
            info = replace(resp.info, latency_s=time.monotonic() - started)
            return replace(resp, info=info)

        return await self._across(chain or self._chain, run)

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

    # ---- voice ------------------------------------------------------------------------------

    async def transcribe(self, wav: bytes) -> tuple[str, CallInfo]:
        """Speech to text. Returns an empty string when there is no intelligible speech."""
        if not self._stt_chain:
            raise LLMError("No speech-to-text model configured.")
        content = types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                types.Part(text=_STT_PROMPT),
            ],
        )
        resp = await self._request(
            [content],
            self._config("You are a precise speech transcriber.", Thinking.LOW),
            self._stt_chain,
        )
        text = resp.text.strip()
        return ("" if text.lower().strip("<>[]. ") in _NO_SPEECH else text), resp.info

    def _tts_config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._tts_voice)
                )
            ),
        )

    async def synthesize(self, text: str) -> tuple[bytes, CallInfo]:
        """Text to speech, whole file at once (WAV). Prefer `synthesize_stream` for latency."""
        if not self._tts_chain:
            raise LLMError("No text-to-speech model configured.")
        content = types.Content(role="user", parts=[types.Part(text=text)])
        resp = await self._request([content], self._tts_config(), self._tts_chain)
        for part in resp.content.parts or []:
            if part.inline_data is not None and part.inline_data.data:
                return part.inline_data.data, resp.info
        raise LLMError("The text-to-speech model returned no audio.")

    async def synthesize_stream(self, text: str) -> TtsStream:
        """Text to speech that can start playing after the first ~1 s instead of after all of it."""
        if not self._tts_chain:
            raise LLMError("No text-to-speech model configured.")
        content = types.Content(role="user", parts=[types.Part(text=text)])
        config = self._tts_config()

        async def open_stream(model: str) -> TtsStream:
            started = time.monotonic()
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._attempts),
                wait=wait_exponential_jitter(initial=0.5, max=4),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    stream = await self._client.aio.models.generate_content_stream(  # pyright: ignore[reportUnknownMemberType]
                        model=model, contents=[content], config=config
                    )
                    iterator = stream.__aiter__()
                    async for resp in iterator:
                        audio = _audio_of(resp)
                        if audio is not None:
                            return TtsStream(
                                first=audio[0],
                                sample_rate=_rate_from_mime(audio[1]),
                                rest=iterator,
                                model=model,
                                started=started,
                                usage=_usage_of(resp),
                            )
                    raise TransientLLMError("The text-to-speech stream ended without audio.")
            raise LLMError("unreachable")

        return await self._across(self._tts_chain, open_stream)
