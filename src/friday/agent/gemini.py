"""Google Gemini client: text and tool calls, structured output, web search, speech in and out.

Each request has a timeout and is retried on a brief overload; if a model stays unavailable the
next model in the list is tried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from friday.agent.llm import (
    CallInfo,
    LLMError,
    LLMResponse,
    Source,
    T,
    Thinking,
    ToolCall,
    ToolSpec,
    Usage,
    user_text,
)

R = TypeVar("R")

_STT_PROMPT = (
    "Transcribe the speech in this audio verbatim. Output only the transcript. "
    "If there is no intelligible speech, output exactly: <silence>"
)
_LEVELS = {Thinking.LOW: types.ThinkingLevel.LOW, Thinking.HIGH: types.ThinkingLevel.HIGH}
_BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}

# The SDK warns about automatic function calling on every call; we never use it.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)


class _EmptyResponse(Exception):
    """Gemini occasionally returns nothing; worth one more try."""


def _is_overloaded(exc: BaseException) -> bool:
    """Retry the same model: a brief server overload or an empty answer."""
    return isinstance(exc, _EmptyResponse) or (
        isinstance(exc, errors.ServerError) and exc.code != 504
    )


def _is_unavailable(exc: BaseException) -> bool:
    """Move on to the next model: overload, rate limit, timeout, or unknown model."""
    if isinstance(exc, _EmptyResponse | errors.ServerError | httpx.TransportError | TimeoutError):
        return True
    return isinstance(exc, errors.ClientError) and exc.code in (404, 408, 429)


def _usage_of(resp: types.GenerateContentResponse) -> Usage:
    meta = resp.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        meta.prompt_token_count or 0,
        meta.candidates_token_count or 0,
        meta.thoughts_token_count or 0,
    )


def _sources_of(cand: types.Candidate) -> tuple[Source, ...]:
    """The pages Google Search grounding used, without duplicates."""
    meta = cand.grounding_metadata
    sources: dict[str, Source] = {}
    for chunk in (meta.grounding_chunks if meta else None) or []:
        web = chunk.web
        if web is not None and web.uri and web.uri not in sources:
            sources[web.uri] = Source(web.title or web.domain or web.uri, web.uri)
    return tuple(sources.values())


def _parse(resp: types.GenerateContentResponse, model: str, latency_s: float) -> LLMResponse:
    cand = resp.candidates[0] if resp.candidates else None
    if cand is None or cand.content is None or not cand.content.parts:
        reason = cand.finish_reason if cand else None
        if reason in _BLOCKED:
            raise LLMError(f"Gemini blocked the response ({reason}).")
        raise _EmptyResponse(f"Empty response from Gemini (finish_reason={reason}).")

    texts: list[str] = []
    calls: list[ToolCall] = []
    for part in cand.content.parts:
        if part.function_call is not None and part.function_call.name:
            call = part.function_call
            calls.append(ToolCall(call.name or "", dict(call.args or {}), call.id))
        elif part.text and not part.thought:
            texts.append(part.text)
    info = CallInfo(_usage_of(resp), model, latency_s)
    return LLMResponse(cand.content, "".join(texts).strip(), tuple(calls), info, _sources_of(cand))


def _rate_from_mime(mime: str | None) -> int:
    """'audio/l16; codec=pcm; rate=24000' -> 24000 (the speech models default to 24 kHz)."""
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


class SpeechStream:
    """16-bit mono PCM from the speech model, handed over while it is still being generated."""

    def __init__(
        self,
        first: bytes,
        sample_rate: int,
        rest: AsyncIterator[types.GenerateContentResponse],
        model: str,
        started: float,
    ) -> None:
        self.sample_rate = sample_rate
        self.first_audio_s = time.monotonic() - started
        self._first = first
        self._rest = rest
        self._model = model
        self._started = started
        self._usage = Usage()

    @property
    def info(self) -> CallInfo:
        return CallInfo(self._usage, self._model, time.monotonic() - self._started)

    async def chunks(self) -> AsyncIterator[bytes]:
        yield self._first
        async for resp in self._rest:
            self._usage = _usage_of(resp)
            audio = _audio_of(resp)
            if audio is not None:
                yield audio[0]


class GeminiLLM:
    def __init__(
        self,
        api_key: str,
        models: Sequence[str],
        *,
        stt_model: str,
        tts_model: str,
        tts_voice: str,
        timeout_s: float = 30.0,
        attempts_per_model: int = 2,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )
        self._models = list(models)
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._attempts = attempts_per_model

    # ---- the agent's model calls -------------------------------------------------------------

    async def generate(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
        web_search: bool = False,
    ) -> LLMResponse:
        """`web_search=True` lets Gemini search Google and answer from live results (with
        `sources`); it is used on its own, never together with `tools`."""
        if web_search and tools:
            raise ValueError("web_search cannot be combined with function tools.")
        config = self._config(system, thinking)
        if web_search:
            config.tools = [types.Tool(google_search=types.GoogleSearch())]
        elif tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters_json_schema=t.parameters
                )
                for t in tools
            ]
            config.tools = [types.Tool(function_declarations=declarations)]
        return await self._generate(contents, config, self._models)

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
        error: ValidationError | None = None
        for _ in range(2):  # a malformed answer gets one more chance
            resp = await self._generate(contents, config, self._models)
            try:
                return schema.model_validate_json(resp.text), resp.info
            except ValidationError as exc:
                error = exc
        raise LLMError(f"Gemini returned an invalid {schema.__name__}: {error}")

    # ---- speech ------------------------------------------------------------------------------

    async def transcribe(self, wav: bytes) -> tuple[str, CallInfo]:
        """Speech to text. Returns an empty string when there is no intelligible speech."""
        audio = types.Part.from_bytes(data=wav, mime_type="audio/wav")
        content = types.Content(role="user", parts=[audio, types.Part(text=_STT_PROMPT)])
        config = self._config("You are a precise speech transcriber.", Thinking.LOW)
        resp = await self._generate([content], config, [self._stt_model])
        text = resp.text.strip()
        return ("" if text.lower().strip("<>[]. ") == "silence" else text), resp.info

    async def speak(self, text: str) -> SpeechStream:
        """Text to speech, returned as soon as the first audio arrives so playback starts early."""
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._tts_voice)
                )
            ),
        )

        async def open_stream(model: str) -> SpeechStream:
            started = time.monotonic()
            stream = await self._client.aio.models.generate_content_stream(  # pyright: ignore[reportUnknownMemberType]
                model=model, contents=[user_text(text)], config=config
            )
            responses = stream.__aiter__()
            async for resp in responses:
                audio = _audio_of(resp)
                if audio is not None:
                    rate = _rate_from_mime(audio[1])
                    return SpeechStream(audio[0], rate, responses, model, started)
            raise _EmptyResponse("The speech model returned no audio.")

        return await self._with_fallback([self._tts_model], open_stream)

    # ---- plumbing ----------------------------------------------------------------------------

    def _config(self, system: str, thinking: Thinking) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=types.ThinkingConfig(thinking_level=_LEVELS[thinking]),
        )

    async def _generate(
        self,
        contents: Sequence[types.Content],
        config: types.GenerateContentConfig,
        models: Sequence[str],
    ) -> LLMResponse:
        async def call(model: str) -> LLMResponse:
            started = time.monotonic()
            resp = await self._client.aio.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                model=model, contents=list(contents), config=config
            )
            return _parse(resp, model, time.monotonic() - started)

        return await self._with_fallback(models, call)

    async def _with_fallback(self, models: Sequence[str], call: Callable[[str], Awaitable[R]]) -> R:
        """Run `call(model)`, retrying brief overloads, then moving down the list of models."""
        failures: list[str] = []
        for model in models:
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(self._attempts),
                    wait=wait_exponential_jitter(initial=1, max=8),
                    retry=retry_if_exception(_is_overloaded),
                    reraise=True,
                ):
                    with attempt:
                        return await call(model)
            except LLMError:
                raise
            except Exception as exc:
                if not _is_unavailable(exc):
                    raise LLMError(f"{type(exc).__name__}: {exc}") from exc
                failures.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
        raise LLMError("No model is available right now. " + " | ".join(failures))
