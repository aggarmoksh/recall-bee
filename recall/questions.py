"""Question generator: a Session's utterances -> classification + stored
Question rows.

Approval policy (AGENT.md "Loop policy"): lecture_study sessions get their
questions auto-approved; substantive_discussion sessions get questions
stored but pending manual approval; casual/personal sessions get no
questions persisted at all.

Session classification runs once and is persisted -- re-running
generate_for_session on an already-classified session is refused (use
--set-type to override, or force=True to regenerate anyway).

NO SELF-FEEDING (AGENT.md, 2026-09-26): utterances already inside some
Answer's matched window are excluded from the transcript sent to the
generator, and whole sessions can be permanently excluded via
recall/overrides/excluded_sessions.txt.

CLI:
    python -m recall.questions --session <id>
    python -m recall.questions --latest
    python -m recall.questions --pending
    python -m recall.questions --approve <session_id>
    python -m recall.questions --reject <session_id>
    python -m recall.questions --set-type <session_id> <type>
    python -m recall.questions --reject-question <question_id>
"""

import argparse
import difflib
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from recall.db import Answer, Question, Session, SessionRun, StartPhraseUtterance, Utterance, create_db_and_tables, get_session
from recall.llm import get_provider
from recall.overrides import load_excluded_sessions

# Phase 5G Step 3 (AGENT.md, 2026-10-07): a SessionRun's own window is
# excluded wholesale, not just the utterances a dispatch actually matched
# -- a quiz session can contain the operator's own spoken answers that
# never landed inside any Answer (e.g. a no_answer dispatch, or a reply
# that arrived outside every window), and those must never feed back into
# future question generation either. 1 min of slack before started_at
# covers the spoken start-phrase itself; 10 min after ended_at covers
# realtime-transcript lag on the session's last few utterances. An
# active run (ended_at is NULL) extends the window up to `now`.
SESSION_RUN_EXCLUSION_LEAD_MIN = 1
SESSION_RUN_EXCLUSION_TRAIL_MIN = 10

DEFAULT_N = 8
FUZZY_MATCH_THRESHOLD = 0.5
MAX_QUESTION_CHARS = 95  # AGENT.md Phase 5F: tightened from 100 so the short hint-prefix (23 chars) usually still fits under the 120-char todo cap

# session_type -> approval_status for the questions generated from it
APPROVAL_BY_SESSION_TYPE = {
    "lecture_study": "approved",
    "substantive_discussion": "pending",
    "casual": "none",
    "personal": "none",
}
VALID_SESSION_TYPES = set(APPROVAL_BY_SESSION_TYPE)


def _excluded_utterance_ids(db, now: datetime | None = None) -> set[int]:
    """No-self-feeding (AGENT.md): utterances inside any answer window,
    matched as the spoken session-start phrase, or spoken anywhere inside
    a quiz SessionRun's own window (Phase 5G Step 3 -- a broader,
    session-window-based exclusion added after session 28 was found to
    contain the operator's own spoken quiz answers that no per-utterance
    match had caught) are never source material for new questions."""
    ids: set[int] = set()
    for answer in db.exec(select(Answer)).all():
        ids.update(answer.utterance_ids)
    for row in db.exec(select(StartPhraseUtterance)).all():
        ids.add(row.utterance_id)

    now = now or datetime.now(UTC)
    for run in db.exec(select(SessionRun)).all():
        window_start = run.started_at - timedelta(minutes=SESSION_RUN_EXCLUSION_LEAD_MIN)
        window_end = (run.ended_at if run.ended_at is not None else now) + timedelta(minutes=SESSION_RUN_EXCLUSION_TRAIL_MIN)
        rows = db.exec(
            select(Utterance.id).where(Utterance.spoken_at >= window_start).where(Utterance.spoken_at <= window_end)
        ).all()
        ids.update(rows)
    return ids


def _session_utterances(db, session: Session, exclude_answer_window: bool = True, now: datetime | None = None) -> list[Utterance]:
    utterances = db.exec(
        select(Utterance)
        .where(Utterance.spoken_at >= session.start_at)
        .where(Utterance.spoken_at <= session.end_at)
        .order_by(Utterance.spoken_at)
    ).all()
    if exclude_answer_window:
        excluded_ids = _excluded_utterance_ids(db, now=now)
        utterances = [u for u in utterances if u.id not in excluded_ids]
    return utterances


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


def enforce_question_length(text: str, provider) -> str | None:
    """Returns `text` unchanged if it's already within MAX_QUESTION_CHARS.
    Otherwise makes one LLM shortening call and returns the result if THAT
    fits; if it's still too long after one attempt, returns None (the
    caller drops the question rather than retrying indefinitely)."""
    if len(text) <= MAX_QUESTION_CHARS:
        return text
    shortened = provider.shorten_question(text, MAX_QUESTION_CHARS)
    return shortened if len(shortened) <= MAX_QUESTION_CHARS else None


def shorten_active_questions() -> dict:
    """Batch pass (Phase 4C) over every currently-active Question: shortens
    any over-length text (one LLM call each), drops (marks inactive, never
    deletes) any that are still too long afterward. Returns counts."""
    provider = get_provider()
    db = get_session()
    try:
        active_questions = db.exec(select(Question).where(Question.active == True)).all()  # noqa: E712
        checked = len(active_questions)
        already_ok = shortened = dropped = 0
        for q in active_questions:
            if len(q.text) <= MAX_QUESTION_CHARS:
                already_ok += 1
                continue
            new_text = enforce_question_length(q.text, provider)
            if new_text is None:
                q.active = False
                dropped += 1
            else:
                q.text = new_text
                shortened += 1
            db.add(q)
        db.commit()
        return {"checked": checked, "already_ok": already_ok, "shortened": shortened, "dropped": dropped}
    finally:
        db.close()


def generate_for_session(
    session_id: int, n: int = DEFAULT_N, target_language: str | None = None, force: bool = False
) -> list[Question]:
    """Classifies the session (stored on Session.session_type/_reason) and,
    for lecture_study/substantive_discussion sessions, persists up to n
    Question rows with approval_status set per policy. casual/personal
    sessions get classified but no questions are persisted."""
    if session_id in load_excluded_sessions():
        raise ValueError(f"Session {session_id} is in recall/overrides/excluded_sessions.txt -- refusing to generate")

    db = get_session()
    try:
        session = db.get(Session, session_id)
        if session is None:
            raise ValueError(f"no Session with id={session_id}")

        if session.session_type is not None and not force:
            raise ValueError(
                f"Session {session_id} already classified as {session.session_type!r} -- classification runs "
                "once and is not re-run automatically. Use --set-type to override, or force=True to regenerate."
            )

        utterances = _session_utterances(db, session)
        transcript_text = " ".join(u.text for u in utterances)
        if not transcript_text.strip():
            # Phase 5C fix: mark classified even with nothing to classify, or
            # find_closed_unclassified_sessions() re-selects this session on
            # every poll forever (session_type stays NULL). Common case now:
            # a session made entirely of excluded start-phrase/answer
            # utterances (no-self-feeding strips them all, leaving nothing).
            session.session_type = "no_content"
            session.session_type_reason = "No utterances left after start-phrase/answer-window exclusion"
            session.approval_status = "none"
            db.add(session)
            db.commit()
            return []

        conversation_id = session.conversation_ids[0] if session.conversation_ids else None
        if conversation_id is None:
            raise ValueError(f"Session {session_id} has no conversation_ids -- cannot set Question.conversation_id")

        provider = get_provider()
        result = provider.generate_questions(transcript_text, n=n, target_language=target_language)

        session.session_type = result.session_type
        session.session_type_reason = result.session_type_reason
        session.approval_status = APPROVAL_BY_SESSION_TYPE[result.session_type]
        db.add(session)

        questions: list[Question] = []
        if result.session_type in ("lecture_study", "substantive_discussion"):
            for gq in result.questions:
                text = enforce_question_length(gq.text, provider)
                if text is None:
                    continue  # still over MAX_QUESTION_CHARS after one shortening call -- drop it
                source_start, source_end = locate_source_window(utterances, gq.source_excerpt)
                q = Question(
                    conversation_id=conversation_id,
                    session_id=session_id,
                    source_start=source_start,
                    source_end=source_end,
                    text=text,
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


def list_pending() -> list[Session]:
    db = get_session()
    try:
        return db.exec(select(Session).where(Session.approval_status == "pending").order_by(Session.start_at)).all()
    finally:
        db.close()


def set_approval(session_id: int, status: str) -> None:
    db = get_session()
    try:
        session = db.get(Session, session_id)
        if session is None:
            raise ValueError(f"no Session with id={session_id}")
        if session.approval_status != "pending":
            raise ValueError(f"Session {session_id} is not pending (status={session.approval_status!r})")
        session.approval_status = status
        db.add(session)
        db.commit()
    finally:
        db.close()


def set_type(session_id: int, session_type: str) -> int:
    """Manual override of a session's classification. If the new type is
    casual/personal, any existing questions for that session are retired
    (marked inactive, never deleted). Returns the number of questions
    retired by this call."""
    if session_type not in VALID_SESSION_TYPES:
        raise ValueError(f"invalid session_type {session_type!r} (valid: {sorted(VALID_SESSION_TYPES)})")

    db = get_session()
    try:
        session = db.get(Session, session_id)
        if session is None:
            raise ValueError(f"no Session with id={session_id}")

        session.session_type = session_type
        session.session_type_reason = "(manual override via --set-type)"
        session.approval_status = APPROVAL_BY_SESSION_TYPE[session_type]
        db.add(session)

        retired = 0
        if session_type in ("casual", "personal"):
            for q in db.exec(select(Question).where(Question.session_id == session_id).where(Question.active == True)).all():  # noqa: E712
                q.active = False
                db.add(q)
                retired += 1

        db.commit()
        return retired
    finally:
        db.close()


def reject_question(question_id: int) -> None:
    db = get_session()
    try:
        q = db.get(Question, question_id)
        if q is None:
            raise ValueError(f"no Question with id={question_id}")
        q.active = False
        db.add(q)
        db.commit()
    finally:
        db.close()


def retire_sessions(session_ids: list[int]) -> int:
    """Marks all active questions for the given sessions inactive (never
    deleted). Returns the count retired."""
    db = get_session()
    try:
        retired = 0
        for q in db.exec(select(Question).where(Question.session_id.in_(session_ids)).where(Question.active == True)).all():  # noqa: E712
            q.active = False
            db.add(q)
            retired += 1
        db.commit()
        return retired
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", type=int, help="Session id to generate questions for")
    group.add_argument("--latest", action="store_true", help="use the most recent Session")
    group.add_argument("--pending", action="store_true", help="list sessions pending approval")
    group.add_argument("--approve", type=int, metavar="SESSION_ID", help="approve a pending session's questions")
    group.add_argument("--reject", type=int, metavar="SESSION_ID", help="reject a pending session's questions")
    group.add_argument("--set-type", nargs=2, metavar=("SESSION_ID", "TYPE"), help="manually override a session's classification")
    group.add_argument("--reject-question", type=int, metavar="QUESTION_ID", help="retire a single question")
    group.add_argument(
        "--shorten-active", action="store_true", help=f"shorten/drop all active questions over {MAX_QUESTION_CHARS} chars"
    )
    parser.add_argument("-n", type=int, default=DEFAULT_N, help=f"number of questions (default {DEFAULT_N})")
    parser.add_argument("--target-language", default=None)
    parser.add_argument("--force", action="store_true", help="regenerate even if already classified")
    args = parser.parse_args()

    create_db_and_tables()

    if args.pending:
        sessions = list_pending()
        if not sessions:
            print("no sessions pending approval")
            return
        for s in sessions:
            print(f"session {s.id}: {s.session_type} -- {s.session_type_reason}")
        return

    if args.approve is not None:
        set_approval(args.approve, "approved")
        print(f"session {args.approve} approved")
        return

    if args.reject is not None:
        set_approval(args.reject, "rejected")
        print(f"session {args.reject} rejected")
        return

    if args.set_type is not None:
        session_id_str, session_type = args.set_type
        retired = set_type(int(session_id_str), session_type)
        print(f"session {session_id_str} set to {session_type!r}; {retired} existing question(s) retired")
        return

    if args.reject_question is not None:
        reject_question(args.reject_question)
        print(f"question {args.reject_question} retired")
        return

    if args.shorten_active:
        counts = shorten_active_questions()
        print(
            f"checked {counts['checked']} active question(s): {counts['already_ok']} already <= {MAX_QUESTION_CHARS} chars, "
            f"{counts['shortened']} shortened, {counts['dropped']} dropped (still too long after shortening)"
        )
        return

    session_id = args.session
    if args.latest:
        session_id = latest_session_id()
        if session_id is None:
            print("no sessions in the database")
            return

    questions = generate_for_session(session_id, n=args.n, target_language=args.target_language, force=args.force)
    db = get_session()
    session = db.get(Session, session_id)
    print(f"session {session_id}: {session.session_type} ({session.approval_status}) -- {session.session_type_reason}")
    db.close()
    print(f"generated {len(questions)} questions")
    for q in questions:
        print(f"  [{q.id}] ({q.topic}) {q.text}")


if __name__ == "__main__":
    main()
