"""Timezone helper shared across modules.

Phase 5C removed the old timed-dispatch path entirely (continuous
profile-paced dispatching, quiet hours, the single-open-dispatch-at-a-
time polling loop) -- AGENT.md's v4 "pull with nudges" model replaces it
with `recall.session` (mini-sessions, user-initiated) and `recall.stings`
(nudges). `to_local()` is the one piece of the old module still used
elsewhere (the LLM daily-call-cap's local-day boundary in `recall.llm`)
and is kept here rather than moved, to avoid an unrelated import churn.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from recall.config import settings


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo(settings.timezone))
