"""Review CLI: python -m recall.review

Lists graded answers from the last 24h (question, first 12 words of the
answer, verdict, feedback) and asks the operator to agree y/n per answer,
saving the result to the DB. Appends a daily summary to
phase5/dogfood_log.md -- counts and verdicts only, no transcript text.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import select

from recall.db import Answer, Dispatch, Question, create_db_and_tables, get_session

DOGFOOD_LOG_PATH = Path(__file__).resolve().parents[1] / "phase5" / "dogfood_log.md"
FIRST_N_WORDS = 12


def answers_last_24h(db, now: datetime | None = None) -> list[Answer]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    return db.exec(select(Answer).where(Answer.matched_at >= cutoff).order_by(Answer.matched_at)).all()


def _first_n_words(text: str, n: int = FIRST_N_WORDS) -> str:
    words = text.split()
    if len(words) <= n:
        return text
    return " ".join(words[:n]) + "..."


def daily_summary(now: datetime | None = None) -> dict:
    """Counts and verdicts only -- no transcript/question text, per the
    privacy-conscious dogfood log requirement."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    db = get_session()
    try:
        answers = answers_last_24h(db, now)
        dispatches = db.exec(select(Dispatch).where(Dispatch.dispatched_at >= cutoff)).all()
    finally:
        db.close()

    # code_truncated (Phase 4C): a manual correction tag for an answer whose
    # real content was cut by a matcher/tooling bug, not a real grading
    # outcome -- excluded from grader-agreement stats so it doesn't skew them.
    reviewed = [a for a in answers if a.operator_agrees is not None and a.verdict != "code_truncated"]
    qualities = [a.quality for a in answers if a.quality is not None]

    return {
        "date": now.date().isoformat(),
        "prompts_sent": len(dispatches),
        "answered": len(answers),
        "no_answer": sum(1 for a in answers if a.verdict == "no_answer"),
        "incomplete_capture": sum(1 for a in answers if a.verdict == "incomplete_capture"),
        "agreement_pct": (sum(1 for a in reviewed if a.operator_agrees) / len(reviewed) * 100) if reviewed else None,
        "mean_quality": (sum(qualities) / len(qualities)) if qualities else None,
    }


def append_daily_summary(now: datetime | None = None) -> dict:
    summary = daily_summary(now)
    agreement_text = f"{summary['agreement_pct']:.0f}%" if summary["agreement_pct"] is not None else "N/A (nothing reviewed yet)"
    quality_text = f"{summary['mean_quality']:.2f}" if summary["mean_quality"] is not None else "N/A"

    lines = [
        f"## {summary['date']}",
        f"- Prompts sent: {summary['prompts_sent']}",
        f"- Answered: {summary['answered']}",
        f"- no_answer: {summary['no_answer']}",
        f"- incomplete_capture: {summary['incomplete_capture']}",
        f"- Grader agreement: {agreement_text}",
        f"- Mean quality: {quality_text}",
        "",
    ]
    DOGFOOD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DOGFOOD_LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return summary


def review_interactive(now: datetime | None = None) -> None:
    db = get_session()
    try:
        answers = answers_last_24h(db, now)
        to_review = [a for a in answers if a.operator_agrees is None]
        if not to_review:
            print("nothing new to review in the last 24h")
        for a in to_review:
            dispatch = db.get(Dispatch, a.dispatch_id)
            question = db.get(Question, dispatch.question_id) if dispatch else None
            q_text = question.text if question else "(unknown question)"
            print(f"\nQuestion: {q_text}")
            print(f"Answer: {_first_n_words(a.raw_text)}")
            print(f"Verdict: {a.verdict}  Feedback: {a.feedback}")
            reply = input("Do you agree with this grade? (y/n): ").strip().lower()
            a.operator_agrees = reply.startswith("y")
            db.add(a)
            db.commit()
    finally:
        db.close()

    summary = append_daily_summary(now)
    print(f"\nappended daily summary to {DOGFOOD_LOG_PATH}")
    print(summary)


def main() -> None:
    create_db_and_tables()
    review_interactive()


if __name__ == "__main__":
    main()
