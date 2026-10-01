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
    # v4 pull-with-nudges model (AGENT.md, Phase 5C)
    active_window: str  # "HH:MM-HH:MM", may wrap past midnight
    daily_quota: int
    day_reset: str  # "HH:MM" local
    max_stings: int


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
        # Kept only for recall.dispatch's pure functions (active_profile(),
        # compute_next_alarm()) which Phase 5C leaves in place but unused by
        # the live daemon -- the v4 mini-session model (recall/session.py)
        # replaces timed dispatch entirely. Flagged to the advisor; not
        # read by recall.session or recall.stings.
        recall_profile=os.environ.get("RECALL_PROFILE", "normal"),
        active_window=os.environ.get("ACTIVE_WINDOW", "18:00-00:00"),
        daily_quota=int(os.environ.get("DAILY_QUOTA", "6")),
        day_reset=os.environ.get("DAY_RESET", "04:00"),
        max_stings=int(os.environ.get("MAX_STINGS", "3")),
    )


settings = load_settings()
