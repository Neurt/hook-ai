"""Configuration — loads OpenRouter settings from the environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # optional: load a .env file if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# NOTE: OpenRouter model availability changes over time. This is a *placeholder*
# default that was commonly available; it is not guaranteed live. Set
# OPENROUTER_MODEL to a current slug from https://openrouter.ai/models
DEFAULT_MODEL = "openai/gpt-4o-mini"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass
class Settings:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    referer: str | None = None
    title: str | None = None
    temperature: float = 0.4
    max_tokens: int = 1500
    request_timeout: float = 60.0


def load_settings(require_key: bool = True) -> Settings:
    """Read settings from env. Raises ConfigError if the key is required but absent."""
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if require_key and not api_key:
        raise ConfigError(
            "OPENROUTER_API_KEY is not set. Copy app/.env.example to app/.env and add "
            "your key (or export the variable). Get a key at https://openrouter.ai/keys"
        )
    return Settings(
        api_key=api_key,
        model=(os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        base_url=(os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        referer=(os.getenv("OPENROUTER_REFERER") or None),
        title=(os.getenv("OPENROUTER_TITLE") or None),
        temperature=float(os.getenv("HOOKAI_TEMPERATURE", "0.4")),
        max_tokens=int(os.getenv("HOOKAI_MAX_TOKENS", "1500")),
        request_timeout=float(os.getenv("HOOKAI_TIMEOUT", "60")),
    )
