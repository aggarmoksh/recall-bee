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
    aws_region: str
    timezone: str
    recall_profile: str


def load_settings() -> Settings:
    return Settings(
        bee_proxy_port=int(os.environ.get("BEE_PROXY_PORT", "8787")),
        llm_provider=os.environ.get("LLM_PROVIDER", "gemini"),
        llm_model=os.environ.get("LLM_MODEL", "gemini-2.5-flash"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY deliberately not read here --
        # boto3 picks them up directly from the environment itself, so they never
        # pass through this dataclass or any of our own logging paths.
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        timezone=os.environ.get("TIMEZONE", "America/Los_Angeles"),
        # Dispatch pacing profile (AGENT.md "Loop policy"): "normal" or "dense".
        # Validated against recall.dispatch.PROFILES at the point of use, not
        # here, so an invalid value fails loudly where it actually matters.
        recall_profile=os.environ.get("RECALL_PROFILE", "normal"),
    )


settings = load_settings()
