"""Manual override files (AGENT.md: "manual override lists beat algorithmic
perfection") -- loaded fresh on every call so edits take effect without a
restart."""

from pathlib import Path

OVERRIDES_DIR = Path(__file__).parent
EXCLUDED_SESSIONS_PATH = OVERRIDES_DIR / "excluded_sessions.txt"


def load_excluded_sessions() -> set[int]:
    if not EXCLUDED_SESSIONS_PATH.exists():
        return set()
    excluded = set()
    for line in EXCLUDED_SESSIONS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        excluded.add(int(line))
    return excluded
