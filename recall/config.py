"""Loads configuration from .env into a single Settings object."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bee_proxy_port: int
    llm_provider: str
    llm_model: str
    gemini_api_key: str
    anthropic_api_key: str
    timezone: str


def load_settings() -> Settings:
    return Settings(
        bee_proxy_port=int(os.environ.get("BEE_PROXY_PORT", "8787")),
        llm_provider=os.environ.get("LLM_PROVIDER", "gemini"),
        llm_model=os.environ.get("LLM_MODEL", "gemini-2.5-flash"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        timezone=os.environ.get("TIMEZONE", "America/Los_Angeles"),
    )


settings = load_settings()
