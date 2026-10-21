"""Builds Friday: the model, the tools, the safety policy and the agent, wired together."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.agent.gemini import GeminiLLM
from friday.agent.loop import Agent
from friday.agent.models import Budget
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


@dataclass(frozen=True, slots=True)
class FridayApp:
    agent: Agent
    llm: GeminiLLM
    settings: Settings
    desktop: Desktop | None
    browser: Browser | None

    async def aclose(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.desktop is not None:
            self.desktop.close()


def build_app(*, confirm: Confirm, progress: EventSink) -> FridayApp:
    """`confirm` asks the user before risky actions; `progress` shows what the agent is doing."""
    settings = Settings()  # pyright: ignore[reportCallIssue]  (the API key comes from .env)
    llm = GeminiLLM(
        settings.google_gemini_api_key.get_secret_value(),
        settings.agent_models,
        stt_model=settings.stt_model,
        tts_model=settings.tts_model,
        tts_voice=settings.tts_voice,
        timeout_s=settings.request_timeout_s,
    )
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
        audit=AuditLog(settings.runs_dir / "audit.jsonl"),
        describe_element=desktop.describe_element if desktop else None,
        describe_web_element=browser.describe_element if browser else None,
    )

    tools = ToolRegistry(policy)
    register_system_tools(tools)
    register_file_tools(tools)
    register_shell_tools(tools)
    register_web_tools(tools, llm)
    if desktop is not None:
        register_desktop_tools(tools, desktop, llm)
    if browser is not None:
        register_browser_tools(tools, browser)

    trace = JsonlSink(settings.runs_dir / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    budget = Budget(
        max_llm_calls=settings.max_llm_calls,
        max_turns_per_step=settings.max_turns_per_step,
        max_step_retries=settings.max_step_retries,
        max_replans=settings.max_replans,
        timeout_s=settings.task_timeout_s,
    )
    agent = Agent(llm, tools, budget=budget, sink=CompositeSink([progress, trace]))
    return FridayApp(agent, llm, settings, desktop, browser)
