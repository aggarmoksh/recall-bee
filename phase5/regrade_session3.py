"""Phase 5F Step 6: regrades session 3's 6 dispatches (the Phase 5E live
test) with the new previous-question reassignment logic, retroactively
correcting the confirmed cross-question bleed on dispatches 17 and 18.

Dry run by default (operates on a COPY of recall.db, writes nothing real,
just prints before/after). --apply runs the exact same logic against the
real recall.db.

Retroactive-regrade specifics (this script only, not the live advance()
path): each dispatch's ORIGINAL captured utterances (from its existing
Answer.utterance_ids) are re-fed through grade_match with the previous
dispatch's question, in session order (15->20), so a reassignment chain
replays correctly. If a dispatch's entire captured content gets
reassigned away to the previous dispatch, that dispatch's own Answer is
set to no_answer (SessionRun.questions_answered doesn't count it) --
retrospectively, nothing was actually said in answer to it, unlike the
live advance() path where the dispatch would just stay open waiting for
more speech (not applicable here since the session has already ended).

Usage: python -m phase5.regrade_session3 [--apply]
"""

import argparse
import shutil
import tempfile
from pathlib import Path

from sqlmodel import Session as DBSession
from sqlmodel import create_engine, select

SESSION_RUN_ID = 3
REAL_DB_PATH = Path(__file__).resolve().parents[1] / "recall.db"


def _run(db_path: Path, apply: bool) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _run_with_engine(engine, apply)
    finally:
        engine.dispose()


def _run_with_engine(engine, apply: bool) -> None:
    from recall.db import Answer, Dispatch, Question, SessionRun, Utterance
    from recall.grading import NO_ANSWER_FEEDBACK, grade_match
    from recall.matcher import MatchResult
    from recall.matcher import Utterance as MatcherUtterance
    from recall.matcher import fuzzy_contains_trigger
    from recall.scoring import replay_review_state, session_run_breakdown

    def _question_to_generated(q):
        from recall.llm.base import GeneratedQuestion

        return GeneratedQuestion(
            text=q.text, expected_answer=q.expected_answer, rubric=q.rubric,
            source_excerpt=q.source_excerpt, topic=q.topic or "", language=q.language or "",
        )

    with DBSession(engine) as db:
        dispatches = db.exec(
            select(Dispatch).where(Dispatch.session_run_id == SESSION_RUN_ID).order_by(Dispatch.id)
        ).all()

        report = []
        affected_question_ids = set()

        for i, dispatch in enumerate(dispatches):
            question = db.get(Question, dispatch.question_id)
            old_answer = db.exec(select(Answer).where(Answer.dispatch_id == dispatch.id)).first()
            before = (old_answer.verdict, old_answer.quality, old_answer.raw_text[:60]) if old_answer else (None, None, None)

            if old_answer is None or not old_answer.raw_text.strip():
                report.append({"dispatch_id": dispatch.id, "question": question.text, "before": before, "after": before, "note": "no original answer -- skipped"})
                continue

            current_match = MatchResult(
                utterances=[MatcherUtterance(id=uid, text=db.get(Utterance, uid).text, spoken_at=db.get(Utterance, uid).spoken_at) for uid in old_answer.utterance_ids if db.get(Utterance, uid) is not None],
                raw_text=old_answer.raw_text,
                trigger_found=old_answer.verdict != "no_answer" and any(fuzzy_contains_trigger(t) for t in [old_answer.raw_text]),
            )

            previous = dispatches[i - 1] if i > 0 else None
            previous_question = db.get(Question, previous.question_id) if previous is not None else None

            result = grade_match(question, current_match, previous_question=previous_question)

            if result.answers == "previous" and previous is not None:
                prev_answer = db.exec(select(Answer).where(Answer.dispatch_id == previous.id)).first()
                combined = []
                if prev_answer is not None:
                    for uid in prev_answer.utterance_ids:
                        u = db.get(Utterance, uid)
                        if u is not None:
                            combined.append(MatcherUtterance(id=u.id, text=u.text, spoken_at=u.spoken_at))
                combined.extend(current_match.utterances)
                combined.sort(key=lambda u: u.spoken_at)
                merged = MatchResult(utterances=combined, raw_text=" ".join(u.text for u in combined), trigger_found=False)

                regrade = grade_match(previous_question, merged)
                prev_before = (prev_answer.verdict, prev_answer.quality) if prev_answer is not None else (None, None)

                if apply:
                    if prev_answer is None:
                        prev_answer = Answer(dispatch_id=previous.id, utterance_ids=[], raw_text="", verdict="no_answer", quality=0, feedback="")
                    prev_answer.utterance_ids = [u.id for u in merged.utterances]
                    prev_answer.raw_text = merged.raw_text
                    prev_answer.verdict = regrade.verdict
                    prev_answer.rubric_points_hit = regrade.rubric_points_hit
                    prev_answer.quality = regrade.quality
                    prev_answer.feedback = regrade.feedback
                    db.add(prev_answer)

                    old_answer.verdict = "no_answer"
                    old_answer.quality = 0
                    old_answer.feedback = NO_ANSWER_FEEDBACK
                    old_answer.rubric_points_hit = []
                    db.add(old_answer)
                    db.commit()

                affected_question_ids.add(previous.question_id)
                affected_question_ids.add(dispatch.question_id)
                report.append(
                    {
                        "dispatch_id": dispatch.id,
                        "question": question.text,
                        "before": before,
                        "after": ("no_answer", 0, "(reassigned to dispatch %d)" % previous.id),
                        "note": f"REASSIGNED to dispatch {previous.id} ({previous_question.text[:50]}...): {prev_before} -> ({regrade.verdict}, {regrade.quality})",
                    }
                )
            else:
                after = (result.verdict, result.quality, current_match.raw_text[:60])
                if apply and after != before:
                    old_answer.verdict = result.verdict
                    old_answer.quality = result.quality
                    old_answer.feedback = result.feedback
                    old_answer.rubric_points_hit = result.rubric_points_hit
                    db.add(old_answer)
                    db.commit()
                    affected_question_ids.add(dispatch.question_id)
                report.append({"dispatch_id": dispatch.id, "question": question.text, "before": before, "after": after, "note": "unchanged" if after == before else "regraded, no reassignment"})

        if apply:
            for qid in affected_question_ids:
                replay_review_state(db, qid)

            run = db.get(SessionRun, SESSION_RUN_ID)
            breakdown = session_run_breakdown(db, SESSION_RUN_ID)
            run.questions_answered = breakdown["answered"]
            db.add(run)
            db.commit()
            print(f"\nSession {SESSION_RUN_ID} corrected breakdown: {breakdown}")

        print(f"\n{'APPLIED' if apply else 'DRY RUN'} -- session {SESSION_RUN_ID} regrade:\n")
        for r in report:
            print(f"dispatch {r['dispatch_id']} ({r['question'][:60]}):")
            print(f"  before: {r['before']}")
            print(f"  after:  {r['after']}")
            print(f"  {r['note']}")
            print()


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


if __name__ == "__main__":
    main()
