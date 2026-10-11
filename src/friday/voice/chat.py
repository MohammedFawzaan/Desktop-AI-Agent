"""Voice chat in the terminal: say "Hey Friday" (or press the hotkey), speak, and Friday answers
out loud. Wires the mic, wake word, hotkey and speaker into a VoiceSession and shows its status."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from friday.agent.loop import Agent, RunResult, SessionTurn
from friday.app import FridayApp, build_app
from friday.cli.display import ConsoleSink, confirm, console, finish
from friday.voice import HotkeyError, HotkeyListener, VoiceCall, VoiceSession
from friday.voice.microphone import Microphone, list_input_devices, measure_level, rms
from friday.voice.text_to_speech import Speaker
from friday.voice.wake_word import WakeWordDetector, ensure_model


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
    friday_app: FridayApp,
    *,
    wake: bool,
    hotkey_spec: str,
    verbose: bool,
) -> None:
    agent, llm, settings = friday_app.agent, friday_app.llm, friday_app.settings
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
    mic = Microphone(settings.mic_device)
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
        speaker=Speaker(),
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
        await friday_app.aclose()


def start_voice_chat(*, verbose: bool, trace: bool, wake: bool, hotkey: str | None) -> None:
    """`friday voice`: talk to Friday until Ctrl+C."""
    friday_app = build_app(confirm=confirm, progress=ConsoleSink(), trace=trace)
    spec = hotkey or friday_app.settings.friday_hotkey
    try:
        asyncio.run(run_voice(friday_app, wake=wake, hotkey_spec=spec, verbose=verbose))
    except KeyboardInterrupt:
        console.print("\nBye.")


def check_microphones(seconds: float, device: str | None) -> None:
    """`friday mics`: list microphones and test the one Friday will use."""
    for d in list_input_devices():
        star = "*" if d.is_default else " "
        console.print(f"{star} [{d.index:>2}] {d.host:<12} {d.name} ({d.channels} ch)")
    console.print(f"\n[bold]Speak normally for {seconds:g} seconds...[/bold]")
    level, peak = measure_level(device, seconds)
    console.print(
        f"Average level {level:.0f}, loudest {peak} (silence is 0-2; speech is usually 1000+)."
    )
    if peak < 50:
        console.print(
            "[red]No sound picked up.[/red] Check Windows Settings > Privacy > Microphone, the "
            "mic mute switch, and the input device in Sound settings; or pass --device."
        )
    elif peak < 1000:
        console.print("[yellow]Quiet. It works, but raise the input volume in Windows.[/yellow]")
    else:
        console.print("[green]The microphone is working.[/green]")


def render_voice_metrics(console: Console, calls: Sequence[VoiceCall]) -> None:
    if not calls:
        return
    table = Table(title="Voice calls", box=box.SIMPLE_HEAD)
    for name in ("Step", "Model", "Size", "Input", "Output", "Time"):
        table.add_column(name, justify="right" if name in ("Input", "Output", "Time") else "left")
    for c in calls:
        u = c.info.usage
        table.add_row(
            "speech to text" if c.kind == "stt" else "text to speech",
            c.info.model,
            c.detail,
            f"{u.prompt_tokens:,}",
            f"{u.output_tokens:,}",
            f"{c.info.latency_s:.1f}s",
        )
    console.print(table)
