"""`friday voice`: talk to Friday in the terminal. Say "Hey Friday" (or press the hotkey), speak,
and Friday answers out loud. This file connects the mic, wake word, hotkey and speaker to a
VoiceSession and prints what is happening."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from rich.panel import Panel

from friday.agent.models import RunResult, SessionTurn
from friday.app import FridayApp, build_app
from friday.cli.display import ConsoleSink, confirm, console, show_result
from friday.voice.hotkey import HotkeyError, HotkeyListener
from friday.voice.microphone import Microphone
from friday.voice.session import VoiceSession
from friday.voice.text_to_speech import Speaker
from friday.voice.wake_word import WakeWordDetector, ensure_model


class ConsoleVoiceUI:
    def status(self, message: str) -> None:
        console.print(f"[dim]{message}[/dim]")

    def heard(self, text: str) -> None:
        console.print(f"\n[bold cyan]You said>[/bold cyan] {text}")

    def answered(self, result: RunResult) -> None:
        show_result(result)

    def error(self, message: str) -> None:
        console.print(f"[red]{message}[/red]")


def start_voice_chat(*, wake: bool) -> None:
    friday = build_app(confirm=confirm, progress=ConsoleSink())
    try:
        asyncio.run(_run(friday, wake=wake))
    except KeyboardInterrupt:
        console.print("\nBye.")


async def _run(friday: FridayApp, *, wake: bool) -> None:
    settings = friday.settings
    loop = asyncio.get_running_loop()
    hotkey_pressed = asyncio.Event()

    detector = None
    if wake:
        model = await asyncio.to_thread(ensure_model, settings.friday_data_dir, console.print)
        detector = WakeWordDetector(model)

    def on_hotkey() -> None:  # runs on the hotkey's own thread
        loop.call_soon_threadsafe(hotkey_pressed.set)

    hotkey = HotkeyListener(settings.friday_hotkey, on_hotkey)
    try:
        hotkey.start()
        ways_to_talk = [f"press {settings.friday_hotkey}"]
    except HotkeyError as exc:
        console.print(f"[yellow]Hotkey disabled: {exc}[/yellow]")
        ways_to_talk = []
    if detector is not None:
        ways_to_talk.insert(0, 'say "Hey Friday"')
    if not ways_to_talk:
        console.print("[red]Nothing can start a conversation (no wake word and no hotkey).[/red]")
        return

    async def run_agent(text: str, history: Sequence[SessionTurn]) -> RunResult:
        return await friday.agent.run(text, session=history, voice=True)

    mic = Microphone(settings.mic_device)
    mic.start()
    console.print(
        Panel(
            f"To talk to me: {' or '.join(ways_to_talk)}, then speak after the beep.\n"
            f"[dim]Microphone: {mic.name}. Press the hotkey again to stop recording early or to "
            "interrupt me. Ctrl+C to exit.[/dim]",
            title="Friday (voice)",
            border_style="cyan",
        )
    )
    session = VoiceSession(
        mic=mic,
        wake=detector,
        hotkey=hotkey_pressed,
        speaker=Speaker(),
        transcribe=friday.llm.transcribe,
        speak=friday.llm.speak,
        run_agent=run_agent,
        ui=ConsoleVoiceUI(),
    )
    try:
        await session.run()
    finally:
        mic.close()
        hotkey.stop()
        await friday.aclose()
