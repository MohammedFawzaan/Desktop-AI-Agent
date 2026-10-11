from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from google.genai import types

from friday.agent import llm as llm_module
from friday.agent.llm import GeminiLLM, Thinking, ToolSpec, user_text

_rate_from_mime = llm_module._rate_from_mime  # pyright: ignore[reportPrivateUsage]


class _SlowThenFast:
    """A fake Gemini endpoint whose first answer is stuck, like the 15 s spikes seen live."""

    def __init__(self) -> None:
        self.calls = 0
        self.aio = self
        self.models = self

    async def generate_content(self, **_: Any) -> types.GenerateContentResponse:
        self.calls += 1
        await asyncio.sleep(5 if self.calls == 1 else 0.01)
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=[types.Part(text="ok")]))
            ]
        )


async def test_a_stuck_request_is_raced_by_a_duplicate() -> None:
    llm = GeminiLLM("key", "model", hedge_after_s=0.05)
    fake = _SlowThenFast()
    llm._client = fake  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    started = time.monotonic()
    resp = await llm.generate(system="s", contents=[user_text("hi")], thinking=Thinking.LOW)
    assert resp.text == "ok" and time.monotonic() - started < 1.0
    assert fake.calls == 2 and llm.hedged == 1


async def test_long_thinking_calls_are_never_hedged() -> None:
    llm = GeminiLLM("key", "model", hedge_after_s=0.05)
    fake = _SlowThenFast()
    fake.calls = 1  # make the only call fast
    llm._client = fake  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    await llm.generate(system="s", contents=[user_text("hi")], thinking=Thinking.HIGH)
    assert llm.hedged == 0


def test_sample_rate_is_read_from_the_audio_mime_type() -> None:
    assert _rate_from_mime("audio/l16; codec=pcm; rate=24000") == 24_000
    assert _rate_from_mime("audio/L16;rate=16000;channels=1") == 16_000
    assert _rate_from_mime(None) == 24_000
    assert _rate_from_mime("audio/wav") == 24_000


def test_search_grounded_answers_carry_their_sources() -> None:
    web = types.GroundingChunkWeb
    resp = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="96.78 INR")]),
                grounding_metadata=types.GroundingMetadata(
                    grounding_chunks=[
                        types.GroundingChunk(web=web(title="xe.com", uri="https://a")),
                        types.GroundingChunk(web=web(title="xe.com", uri="https://a")),
                        types.GroundingChunk(web=web(domain="wise.com", uri="https://b")),
                    ]
                ),
            )
        ]
    )
    parsed = llm_module._parse(resp, "m")  # pyright: ignore[reportPrivateUsage]
    assert parsed.text == "96.78 INR"
    assert [(s.title, s.url) for s in parsed.sources] == [
        ("xe.com", "https://a"),
        ("wise.com", "https://b"),
    ]


async def test_web_search_turns_on_google_search_and_is_never_mixed_with_tools() -> None:
    llm = GeminiLLM("key", "model", hedge_after_s=None)
    seen: list[types.GenerateContentConfig] = []

    class _Fake:
        def __init__(self) -> None:
            self.aio = self
            self.models = self

        async def generate_content(self, **kw: Any) -> types.GenerateContentResponse:
            seen.append(kw["config"])
            part = types.Part(text="ok")
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
            )

    llm._client = _Fake()  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    await llm.generate(system="s", contents=[user_text("q")], web_search=True)
    tools: list[Any] = list(seen[0].tools or [])  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    assert len(tools) == 1 and isinstance(tools[0], types.Tool) and tools[0].google_search
    spec = ToolSpec("t", "d", {"type": "object", "properties": {}})
    with pytest.raises(ValueError):
        await llm.generate(system="s", contents=[user_text("q")], tools=[spec], web_search=True)
