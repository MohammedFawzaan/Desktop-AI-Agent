"""Ask the model about a screenshot; it can return clickable positions for what it finds."""

from __future__ import annotations

from google.genai import types
from pydantic import BaseModel, Field

from friday.agent.llm import LLM, CallInfo, Thinking
from friday.desktop.screen import Shot, box_to_point

VISION_SYSTEM = """You look at a screenshot of a Windows desktop or app window and answer precisely
from what is visible. Read text exactly as shown. When the question asks where something is, or
what to click, add it to targets with a tight box_2d. Never guess at things you cannot see."""


class ScreenTarget(BaseModel):
    label: str = Field(description="What this is, e.g. 'Save button'.")
    box_2d: list[int] = Field(
        min_length=4, max_length=4, description="[ymin, xmin, ymax, xmax], normalized to 0-1000."
    )


class ScreenReading(BaseModel):
    answer: str = Field(description="Direct answer to the question, from what is visible.")
    targets: list[ScreenTarget] = Field(
        default_factory=list[ScreenTarget], description="Things to click, if asked for."
    )


async def inspect(llm: LLM, shot: Shot, question: str) -> tuple[str, CallInfo]:
    content = types.Content(
        role="user",
        parts=[
            types.Part.from_bytes(data=shot.png, mime_type="image/png"),
            types.Part(text=question),
        ],
    )
    reading, info = await llm.generate_structured(
        system=VISION_SYSTEM, contents=[content], schema=ScreenReading, thinking=Thinking.LOW
    )
    lines = [reading.answer.strip() or "(no answer)"]
    for target in reading.targets:
        x, y = box_to_point(target.box_2d, shot)
        lines.append(f"- {target.label}: click(x={x}, y={y})")
    return "\n".join(lines), info
