"""Question generator: a Session's utterances -> N stored Question rows.

CLI:
    python -m recall.questions --session <id>
    python -m recall.questions --latest
"""

import argparse
import difflib
from datetime import datetime

from sqlmodel import select

from recall.db import Question, Session, Utterance, create_db_and_tables, get_session
from recall.llm import get_provider

DEFAULT_N = 8
FUZZY_MATCH_THRESHOLD = 0.5


def _session_utterances(db, session: Session) -> list[Utterance]:
    return db.exec(
        select(Utterance)
        .where(Utterance.spoken_at >= session.start_at)
        .where(Utterance.spoken_at <= session.end_at)
        .order_by(Utterance.spoken_at)
    ).all()


def locate_source_window(
    utterances: list[Utterance], source_excerpt: str
) -> tuple[datetime | None, datetime | None]:
    """Best-effort: find which utterance(s) the quoted source_excerpt came
    from, to derive source_start/source_end. Exact substring match first
    (checked against every utterance individually), falls back to the
    single best fuzzy match if no utterance contains it verbatim."""
    excerpt = source_excerpt.strip()
    if not excerpt:
        return None, None

    matches = [u for u in utterances if excerpt in u.text]
    if matches:
        return matches[0].spoken_at, matches[-1].spoken_at

    best, best_ratio = None, 0.0
    for u in utterances:
        ratio = difflib.SequenceMatcher(None, u.text.lower(), excerpt.lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, u
    if best is not None and best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best.spoken_at, best.spoken_at
    return None, None


def generate_for_session(session_id: int, n: int = DEFAULT_N, target_language: str | None = None) -> list[Question]:
    db = get_session()
    try:
        session = db.get(Session, session_id)
        if session is None:
            raise ValueError(f"no Session with id={session_id}")

        utterances = _session_utterances(db, session)
        transcript_text = " ".join(u.text for u in utterances)
        if not transcript_text.strip():
            return []

        conversation_id = session.conversation_ids[0] if session.conversation_ids else None
        if conversation_id is None:
            raise ValueError(f"Session {session_id} has no conversation_ids -- cannot set Question.conversation_id")

        provider = get_provider()
        generated = provider.generate_questions(transcript_text, n=n, target_language=target_language)

        questions: list[Question] = []
        for gq in generated:
            source_start, source_end = locate_source_window(utterances, gq.source_excerpt)
            q = Question(
                conversation_id=conversation_id,
                session_id=session_id,
                source_start=source_start,
                source_end=source_end,
                text=gq.text,
                expected_answer=gq.expected_answer,
                rubric=gq.rubric,
                source_excerpt=gq.source_excerpt,
                topic=gq.topic,
                language=gq.language,
            )
            db.add(q)
            questions.append(q)
        db.commit()
        for q in questions:
            db.refresh(q)
        return questions
    finally:
        db.close()


def latest_session_id() -> int | None:
    db = get_session()
    try:
        session = db.exec(select(Session).order_by(Session.start_at.desc())).first()
        return session.id if session else None
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", type=int, help="Session id to generate questions for")
    group.add_argument("--latest", action="store_true", help="use the most recent Session")
    parser.add_argument("-n", type=int, default=DEFAULT_N, help=f"number of questions (default {DEFAULT_N})")
    parser.add_argument("--target-language", default=None)
    args = parser.parse_args()

    create_db_and_tables()

    session_id = args.session
    if args.latest:
        session_id = latest_session_id()
        if session_id is None:
            print("no sessions in the database")
            return

    questions = generate_for_session(session_id, n=args.n, target_language=args.target_language)
    print(f"generated {len(questions)} questions for session {session_id}")
    for q in questions:
        print(f"  [{q.id}] ({q.topic}) {q.text}")


if __name__ == "__main__":
    main()
