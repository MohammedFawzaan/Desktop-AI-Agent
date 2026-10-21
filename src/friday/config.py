"""Settings, read from environment variables or the .env file. Only the API key is required."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_gemini_api_key: SecretStr

    # Models: the agent tries them in order when one is unavailable.
    agent_models: list[str] = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    stt_model: str = "gemini-3.5-flash-lite"
    tts_model: str = "gemini-3.8-flash-tts"
    tts_voice: str = "Kore"
    request_timeout_s: float = 30.0

    # Limits for one task.
    max_llm_calls: int = 40
    max_turns_per_step: int = 15
    max_step_retries: int = 2
    max_replans: int = 2
    task_timeout_s: float = 900.0

    # Voice.
    friday_hotkey: str = "ctrl+alt+f"
    mic_device: str | None = None  # index or part of a name; default: the Windows default mic

    # App and browser control.
    gui_enabled: bool = True
    browser_enabled: bool = True
    browser_channels: list[str] = ["chrome", "msedge"]  # installed browsers to try, in order
    browser_headless: bool = False

    # Where Friday keeps its files (browser profile, speech model) and its logs.
    friday_data_dir: Path = Path.home() / ".friday"
    runs_dir: Path = Path("runs")
