from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_gemini_api_key: SecretStr
    agent_model: str = "gemini-3.8-flash"
    agent_fallback_models: list[str] = [
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]

    # Voice (Gemini audio). The hotkey and data folder can be overridden in .env.
    friday_hotkey: str = "ctrl+alt+f"
    friday_data_dir: Path = Path.home() / ".friday"
    stt_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    tts_models: list[str] = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    tts_voice: str = "Kore"
    mic_device: str | None = None  # index or part of a name; default = Windows default mic
