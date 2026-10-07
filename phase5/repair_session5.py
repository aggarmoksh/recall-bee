"""Phase 5G Step 5: repairs SessionRun 5 (2026-10-07), the run live when
the infinite-reassignment-loop bug (FRICTION_LOG.md, 2-day check Incident
3) was found and the daemon was stopped mid-session.

Dispatch 27 (question 36, "how does light reveal structure"): its Answer's
utterance_ids contains 53 duplicate ids (the same handful of utterances
re-matched and re-persisted on every bug-loop iteration) -- deduped to the
unique set. verdict/quality/feedback are left exactly as they already are
(partial/3) -- the duplicate ids never changed what was graded, only
padded the evidence trail.

Dispatch 28 (question 37, "chocolate polymorphs"): the operator's real
answer (utterances 3645702150-3645704496, spoken ~10:50:33-10:50:57 UTC)
never attached to this dispatch at all -- the bug-loop kept re-matching
stale question-36 content against it instead. Attaches the real content
and grades it once (the dispatch's only real grade -- grade_call_count
starts at 1, well under the Phase 5G Step 2 cap of 3).

No feedback todo is posted -- the daemon is down for this repair, same
"dashboard-only" convention as the Phase 5F deferred-grade corrections.

Dry run by default (operates on a COPY of recall.db, writes nothing real,
just prints before/after). --apply runs the exact same logic against the
real recall.db.

Usage: python -m phase5.repair_session5 [--apply]
"""

import argparse
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

SESSION_RUN_ID = 5
DISPATCH_Q1_ID = 27
DISPATCH_Q2_ID = 28
Q2_ORPHANED_UTTERANCE_IDS = [3645702150, 3645702151, 3645702152, 3645702153, 3645704496]
REAL_DB_PATH = Path(__file__).resolve().parents[1] / "recall.db"


def _run(db_path: Path, apply: bool) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _run_with_engine(engine, apply)
    finally:
        engine.dispose()


def _backfill_claims(db) -> int:
    """Every Answer predates Utterance.claimed_by_dispatch_id (added this
    phase) -- backfill it from existing Answer.utterance_ids so the new
    claim-exclusivity guard (Phase 5G Step 2) actually recognizes history,
    not just answers graded from now on. Returns the number of utterance
    rows newly claimed."""
    from recall.db import Answer, Utterance

    claimed = 0
    for answer in db.exec(select(Answer)).all():
        for uid in set(answer.utterance_ids):
            u = db.get(Utterance, uid)
            if u is not None and u.claimed_by_dispatch_id is None:
                u.claimed_by_dispatch_id = answer.dispatch_id
                db.add(u)
                claimed += 1
    db.commit()
    return claimed


def _run_with_engine(engine, apply: bool) -> None:
    from recall.db import Answer, Dispatch, Question, SessionRun, Utterance
    from recall.grading import grade_match
    from recall.matcher import MatchResult
    from recall.matcher import Utterance as MatcherUtterance
    from recall.scoring import session_run_breakdown
    from recall.session import _apply_scheduling, _claim_utterances

    with DBSession(engine) as db:
        backfilled = _backfill_claims(db)
        print(f"backfilled claimed_by_dispatch_id on {backfilled} historical utterance row(s)\n")

        # --- Q1 (dispatch 27): dedupe only, no regrade ---
        d1 = db.get(Dispatch, DISPATCH_Q1_ID)
        a1 = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        q1_before_count = len(a1.utterance_ids)
        q1_deduped = sorted(set(a1.utterance_ids))
        print(f"dispatch {d1.id} (question {d1.question_id}): utterance_ids {q1_before_count} -> {len(q1_deduped)} (deduped)")
        print(f"  verdict/quality unchanged: {a1.verdict}/{a1.quality}")
        if apply:
            a1.utterance_ids = q1_deduped
            db.add(a1)
            db.commit()

        # --- Q2 (dispatch 28): attach the real orphaned content, grade once ---
        d2 = db.get(Dispatch, DISPATCH_Q2_ID)
        q2 = db.get(Question, d2.question_id)
        q1 = db.get(Question, d1.question_id)
        rows = [db.get(Utterance, uid) for uid in Q2_ORPHANED_UTTERANCE_IDS]
        rows = [r for r in rows if r is not None]
        match = MatchResult(
            utterances=[MatcherUtterance(id=r.id, text=r.text, spoken_at=r.spoken_at) for r in rows],
            raw_text=" ".join(r.text for r in rows),
            trigger_found=False,
        )
        print(f"\ndispatch {d2.id} (question {d2.question_id}): attaching {len(rows)} orphaned utterance(s):")
        print(f"  raw_text: {match.raw_text[:200]}")

        result = grade_match(q2, match, previous_question=q1)
        print(f"  grade result: verdict={result.verdict} quality={result.quality} feedback={result.feedback!r}")
        if result.answers != "current":
            print(f"  (diagnostic only, not acted on: grader said answers={result.answers!r})")

        last_spoken_at = max(r.spoken_at for r in rows)
        repair_now = last_spoken_at + timedelta(seconds=20)

        if apply:
            a2 = Answer(
                dispatch_id=d2.id,
                utterance_ids=[r.id for r in rows],
                raw_text=match.raw_text,
                verdict=result.verdict,
                rubric_points_hit=result.rubric_points_hit,
                quality=result.quality,
                feedback=result.feedback,
                matched_at=repair_now,
            )
            db.add(a2)
            db.commit()
            db.refresh(a2)
            db.refresh(d2)
            _claim_utterances(db, d2, match)

            d2.status = "answered"
            d2.grade_call_count = 1
            db.add(d2)
            _apply_scheduling(db, q2, a2, repair_now)
            db.commit()

            run = db.get(SessionRun, SESSION_RUN_ID)
            if result.verdict != "no_answer":
                run.questions_answered += 1
                db.add(run)
            run.status = "completed"
            run.ended_at = repair_now
            db.add(run)
            db.commit()

            breakdown = session_run_breakdown(db, SESSION_RUN_ID)
            print(f"\nSessionRun {SESSION_RUN_ID} closed: status={run.status}, breakdown={breakdown}")
        else:
            print(f"\n(dry run -- SessionRun {SESSION_RUN_ID} would close with status=completed, ended_at={repair_now})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write real changes to recall.db (default: dry run on a copy)")
    args = parser.parse_args()

    if args.apply:
        _run(REAL_DB_PATH, apply=True)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / "recall_dryrun.db"
            shutil.copy(REAL_DB_PATH, copy_path)
            _run(copy_path, apply=False)
            print(f"\n{'=' * 60}\nDRY RUN ONLY -- no changes written to {REAL_DB_PATH}. Re-run with --apply to commit.")


if __name__ == "__main__":
    main()
