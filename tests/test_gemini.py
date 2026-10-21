from __future__ import annotations

from typing import Any

import pytest
from google.genai import types

from friday.agent import gemini
from friday.agent.gemini import GeminiLLM
from friday.agent.llm import ToolSpec, user_text

_parse = gemini._parse  # pyright: ignore[reportPrivateUsage]
_rate_from_mime = gemini._rate_from_mime  # pyright: ignore[reportPrivateUsage]


def reply(text: str, **candidate: Any) -> types.GenerateContentResponse:
    content = types.Content(role="model", parts=[types.Part(text=text)])
    return types.GenerateContentResponse(candidates=[types.Candidate(content=content, **candidate)])


class FakeClient:
    """Stands in for `genai.Client`: records each request's config and answers "ok"."""

    def __init__(self) -> None:
        self.aio = self
        self.models = self
        self.configs: list[types.GenerateContentConfig] = []

    async def generate_content(self, **kw: Any) -> types.GenerateContentResponse:
        self.configs.append(kw["config"])
        return reply("ok")


def fake_gemini() -> tuple[GeminiLLM, FakeClient]:
    llm = GeminiLLM("key", ["model"], stt_model="stt", tts_model="tts", tts_voice="Kore")
    client = FakeClient()
    llm._client = client  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    return llm, client


def test_search_answers_carry_their_sources_without_duplicates() -> None:
    web = types.GroundingChunkWeb
    meta = types.GroundingMetadata(
        grounding_chunks=[
            types.GroundingChunk(web=web(title="xe.com", uri="https://a")),
            types.GroundingChunk(web=web(title="xe.com", uri="https://a")),
            types.GroundingChunk(web=web(domain="wise.com", uri="https://b")),
        ]
    )
    parsed = _parse(reply("96.78 INR", grounding_metadata=meta), "m", 0.1)
    assert parsed.text == "96.78 INR"
    assert [(s.title, s.url) for s in parsed.sources] == [
        ("xe.com", "https://a"),
        ("wise.com", "https://b"),
    ]


async def test_web_search_turns_on_google_search_and_is_never_mixed_with_tools() -> None:
    llm, client = fake_gemini()
    await llm.generate(system="s", contents=[user_text("q")], web_search=True)
    tools: list[Any] = list(client.configs[0].tools or [])  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    assert len(tools) == 1 and isinstance(tools[0], types.Tool) and tools[0].google_search
    spec = ToolSpec("t", "d", {"type": "object", "properties": {}})
    with pytest.raises(ValueError):
        await llm.generate(system="s", contents=[user_text("q")], tools=[spec], web_search=True)


def test_the_speech_sample_rate_is_read_from_the_audio_type() -> None:
    assert _rate_from_mime("audio/l16; codec=pcm; rate=24000") == 24_000
    assert _rate_from_mime("audio/L16;rate=16000;channels=1") == 16_000
    assert _rate_from_mime(None) == 24_000
