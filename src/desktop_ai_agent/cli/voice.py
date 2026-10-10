"""Voice mode: say "Hey Friday" (or press the hotkey), speak, and Friday answers out loud."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from rich.panel import Panel

from desktop_ai_agent.agent import Agent
from desktop_ai_agent.agent.state import RunResult, SessionTurn
from desktop_ai_agent.cli.common import Built, console, finish
from desktop_ai_agent.cli.render import render_voice_metrics
from desktop_ai_agent.voice import HotkeyError, HotkeyListener, VoiceCall, VoiceSession
from desktop_ai_agent.voice.audio import rms
from desktop_ai_agent.voice.mic import Microphone
from desktop_ai_agent.voice.player import Player
from desktop_ai_agent.voice.wake import WakeWordDetector, ensure_model


class ConsoleVoiceUI:
    def __init__(self, agent: Agent, verbose: bool) -> None:
        self._agent = agent
        self._verbose = verbose

    def status(self, message: str) -> None:
        console.print(f"[dim]{message}[/dim]")

    def heard(self, text: str) -> None:
        console.print(f"\n[bold cyan]You said>[/bold cyan] {text}")

    def error(self, message: str) -> None:
        console.print(f"[red]{message}[/red]")

    def result(self, result: RunResult, calls: list[VoiceCall]) -> None:
        finish(self._agent, result, self._verbose)

    def voice_metrics(self, calls: list[VoiceCall]) -> None:
        render_voice_metrics(console, calls)
        self._agent.emit_raw(
            "voice",
            {
                "calls": [
                    {
                        "kind": c.kind,
                        "model": c.info.model,
                        "detail": c.detail,
                        "prompt_tokens": c.info.usage.prompt_tokens,
                        "output_tokens": c.info.usage.output_tokens,
                        "latency_s": round(c.info.latency_s, 3),
                    }
                    for c in calls
                ]
            },
        )


async def _warn_if_mic_is_silent(mic: Microphone) -> None:
    loudest = 0.0
    for _ in range(40):
        frame = await mic.read(0.1)
        if frame is not None:
            loudest = max(loudest, rms(frame))
    if loudest < 3:
        console.print(
            "[yellow]The microphone is completely silent right now. If I do not react when "
            "you speak, run `friday mics` to test it, or set MIC_DEVICE in .env.[/yellow]"
        )


async def run_voice(
    built: Built, *, wake: bool, hotkey_spec: str, verbose: bool, device: int | str | None = None
) -> None:
    agent, llm, settings = built.agent, built.llm, built.settings
    loop = asyncio.get_running_loop()
    hotkey_event = asyncio.Event()

    detector: WakeWordDetector | None = None
    if wake:
        model_path = await asyncio.to_thread(ensure_model, settings.friday_data_dir, console.print)
        detector = WakeWordDetector(model_path)

    def on_hotkey() -> None:
        loop.call_soon_threadsafe(hotkey_event.set)

    listener = HotkeyListener(hotkey_spec, on_hotkey)
    hotkey_ok = True
    try:
        listener.start()
    except HotkeyError as exc:
        hotkey_ok = False
        console.print(f"[yellow]Hotkey disabled: {exc}[/yellow]")
    if detector is None and not hotkey_ok:
        console.print("[red]Nothing can start a conversation (no wake word, no hotkey).[/red]")
        return

    async def run_agent(text: str, session: Sequence[SessionTurn]) -> RunResult:
        return await agent.run(text, session=session, voice=True)

    triggers = " or ".join(
        (['say "Hey Friday"'] if detector else []) + ([f"press {hotkey_spec}"] if hotkey_ok else [])
    )
    spec = settings.mic_device
    mic = Microphone(
        device if device is not None else (int(spec) if spec and spec.isdigit() else spec)
    )
    mic.start()
    console.print(f"[dim]Microphone: {mic.name}[/dim]")
    await _warn_if_mic_is_silent(mic)
    console.print(
        Panel(
            f"To talk to me: {triggers}. After the beep, speak your request.\n"
            f"[dim]Press the hotkey again to finish early, or while I am speaking to interrupt "
            f"me. Ctrl+C to exit.[/dim]",
            title="Friday (voice)",
            border_style="cyan",
        )
    )
    session = VoiceSession(
        mic=mic,
        wake=detector,
        hotkey=hotkey_event,
        player=Player(),
        transcribe=llm.transcribe,
        synthesize=llm.synthesize,
        synthesize_stream=llm.synthesize_stream,
        run_agent=run_agent,
        ui=ConsoleVoiceUI(agent, verbose),
    )
    try:
        await session.run()
    finally:
        mic.close()
        listener.stop()
        await built.aclose()
