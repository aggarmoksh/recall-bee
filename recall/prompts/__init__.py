"""Loads and renders the reviewable prompt templates in this package."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
GENERATE_QUESTIONS_TEMPLATE = PROMPTS_DIR / "generate_questions.md"
GRADE_ANSWER_TEMPLATE = PROMPTS_DIR / "grade_answer.md"
SHORTEN_QUESTION_TEMPLATE = PROMPTS_DIR / "shorten_question.md"
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


def render_grade_answer_prompt(
    question: str, expected_answer: str, rubric: list[str], source_excerpt: str, answer_text: str
) -> str:
    template = GRADE_ANSWER_TEMPLATE.read_text(encoding="utf-8")
    rubric_text = "\n".join(f"- {r}" for r in rubric) if rubric else "(none given)"
    return (
        template.replace("{{QUESTION}}", question)
        .replace("{{EXPECTED_ANSWER}}", expected_answer)
        .replace("{{RUBRIC}}", rubric_text)
        .replace("{{SOURCE_EXCERPT}}", source_excerpt)
        .replace("{{ANSWER_TEXT}}", answer_text)
    )


def render_shorten_question_prompt(text: str, max_chars: int) -> str:
    template = SHORTEN_QUESTION_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{{TEXT}}", text).replace("{{MAX_CHARS}}", str(max_chars))
