"""Loads and renders the reviewable prompt templates in this package."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
GENERATE_QUESTIONS_TEMPLATE = PROMPTS_DIR / "generate_questions.md"
BLOCKLIST_PATH = PROMPTS_DIR / "blocklist.txt"


def load_blocklist() -> list[str]:
    if not BLOCKLIST_PATH.exists():
        return []
    entries = []
    for line in BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


def render_generate_questions_prompt(transcript: str, n: int, target_language: str | None = None) -> str:
    template = GENERATE_QUESTIONS_TEMPLATE.read_text(encoding="utf-8")
    blocklist = load_blocklist()
    blocklist_text = "\n".join(f"- {b}" for b in blocklist) if blocklist else "(none)"
    target_language_text = target_language if target_language else "(none specified -- use the transcript's dominant language)"

    return (
        template.replace("{{BLOCKLIST}}", blocklist_text)
        .replace("{{TARGET_LANGUAGE}}", target_language_text)
        .replace("{{N}}", str(n))
        .replace("{{TRANSCRIPT}}", transcript)
    )
