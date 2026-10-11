"""Builds Friday: the model, the tools, the safety policy and the agent, wired together."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from friday.agent.llm import GeminiLLM
from friday.agent.loop import Agent, Budget
from friday.browser import Browser
from friday.config import Settings
from friday.desktop import Desktop
from friday.monitoring import CompositeSink, EventSink, JsonlSink
from friday.safety import AuditLog, Confirm, SafetyPolicy
from friday.tools.browser import register_browser_tools
from friday.tools.desktop import register_desktop_tools
from friday.tools.files import register_file_tools
from friday.tools.registry import ToolRegistry
from friday.tools.shell import register_shell_tools
from friday.tools.system import register_system_tools
from friday.tools.web import register_web_tools

RUNS_DIR = Path("runs")


@dataclass(frozen=True, slots=True)
class FridayApp:
    agent: Agent
    llm: GeminiLLM
    settings: Settings
    desktop: Desktop | None = None
    browser: Browser | None = None

    async def aclose(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.desktop is not None:
            self.desktop.close()


def build_app(
    *, confirm: Confirm | None, progress: EventSink | None = None, trace: bool = True
) -> FridayApp:
    """`confirm` asks the user before risky actions; `progress` shows live events."""
    settings = Settings()  # type: ignore[call-arg]
    llm = GeminiLLM(
        settings.google_gemini_api_key.get_secret_value(),
        settings.agent_model,
        fallback_models=settings.agent_fallback_models,
        stt_models=settings.stt_models,
        tts_models=settings.tts_models,
        tts_voice=settings.tts_voice,
    )
    sinks: list[EventSink] = [progress] if progress is not None else []
    if trace:
        sinks.append(JsonlSink(RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"))

    desktop = Desktop() if settings.gui_enabled else None
    browser = (
        Browser(
            settings.friday_data_dir / "browser",
            channels=settings.browser_channels,
            headless=settings.browser_headless,
        )
        if settings.browser_enabled
        else None
    )
    policy = SafetyPolicy(
        confirm,
        audit=AuditLog(RUNS_DIR / "audit.jsonl"),
        describe_element=desktop.describe_element if desktop else None,
        describe_web_element=browser.describe_element if browser else None,
    )

    registry = ToolRegistry(policy)
    register_system_tools(registry)
    register_file_tools(registry)
    register_shell_tools(registry)
    register_web_tools(registry, llm)
    if desktop is not None:
        register_desktop_tools(registry, desktop, llm)
    if browser is not None:
        register_browser_tools(registry, browser)

    agent = Agent(llm, registry, budget=Budget(), sink=CompositeSink(sinks))
    return FridayApp(agent, llm, settings, desktop, browser)
