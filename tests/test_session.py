from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, Question
from recall.db import Session as SessionModel
from recall.db import SessionRun, Utterance
from recall.llm.base import GradeResult, LLMProvider

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeProvider(LLMProvider):
    def __init__(self, result: GradeResult):
        self.result = result

    def generate_questions(self, transcript, n, target_language=None):
        raise NotImplementedError

    def grade_answer(self, question, spoken_answer, previous_question=None):
        return self.result

    def shorten_question(self, text, max_chars):
        raise NotImplementedError


class _FakeBeeClient:
    def __init__(self, store: dict, next_id_box: list):
        self.store = store
        self.next_id_box = next_id_box

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def create_todo(self, text, alarm_at=None):
        new_id = self.next_id_box[0]
        self.next_id_box[0] += 1
        self.store[new_id] = {"text": text, "alarm_at": alarm_at, "deleted": False}
        return {"id": new_id}

    def delete_todo(self, todo_id):
        if todo_id not in self.store:
            raise RuntimeError(f"attempted to delete unknown todo id {todo_id}")
        self.store[todo_id]["deleted"] = True
        return {"success": True}


class _FakeBeeProxy:
    def __init__(self, port=None):
        self.base_url = "http://fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def bee_store():
    return {}, [1000]


@pytest.fixture
def db_engine(tmp_path, monkeypatch, bee_store):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("recall.session.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.grading.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("recall.scoring.get_session", lambda: DBSession(engine))
    monkeypatch.setattr("time.sleep", lambda s: None)

    store, next_id_box = bee_store
    monkeypatch.setattr("recall.session.BeeProxy", lambda port=None: _FakeBeeProxy())
    monkeypatch.setattr("recall.session.BeeClient", lambda base_url: _FakeBeeClient(store, next_id_box))
    return engine


@pytest.fixture(autouse=True)
def patch_provider(monkeypatch):
    def _patch(result: GradeResult):
        monkeypatch.setattr("recall.grading.get_provider", lambda: _FakeProvider(result))

    return _patch


def _approved_session(db, session_id=1):
    db.add(SessionModel(id=session_id, start_at=BASE, end_at=BASE, conversation_ids=[1], approval_status="approved"))


def _question(db, qid, session_id=1, text="Q", rubric=None):
    q = Question(id=qid, conversation_id=1, session_id=session_id, text=text, expected_answer="A", rubric=rubric or ["a", "b"], source_excerpt="...")
    db.add(q)
    return q


def _seed_questions(engine, n: int, session_id=1):
    with DBSession(engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db, session_id)
        for i in range(1, n + 1):
            _question(db, i, session_id=session_id, text=f"Question {i}?")
        db.commit()


def test_question_todo_text_uses_short_prefix_when_it_fits():
    from recall.session import QUESTION_PREFIX_SHORT, TODO_TEXT_MAX_CHARS, question_todo_text

    text = question_todo_text("Short question?")
    assert text == f"{QUESTION_PREFIX_SHORT}Short question?"
    assert len(text) <= TODO_TEXT_MAX_CHARS


def test_question_todo_text_falls_back_to_bare_question_when_still_too_long():
    """Phase 5F: always the short prefix; if even that doesn't fit, send
    the bare question with no prefix at all rather than truncating it."""
    from recall.session import TODO_TEXT_MAX_CHARS, question_todo_text

    near_max_question = "A" * 100  # short prefix (23 chars) + this exceeds 120
    text = question_todo_text(near_max_question)
    assert text == near_max_question  # no prefix at all
    assert len(text) <= TODO_TEXT_MAX_CHARS


def test_start_session_returns_none_when_nothing_due(db_engine):
    from recall.session import start_session

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        db.commit()

    assert start_session("tick", now=BASE) is None


def test_start_session_dispatches_first_due_question(db_engine, bee_store):
    from recall.session import start_session

    _seed_questions(db_engine, 3)
    store, _ = bee_store

    run = start_session("spoken", now=BASE)

    assert run is not None
    assert run.status == "active"
    assert run.trigger == "spoken"
    assert run.questions_asked == 1
    with DBSession(db_engine) as db:
        dispatches = db.exec(select(Dispatch)).all()
        assert len(dispatches) == 1
        assert dispatches[0].session_run_id == run.id
    assert len(store) == 1  # the question todo


def test_start_session_refuses_when_already_active(db_engine):
    from recall.session import start_session

    _seed_questions(db_engine, 3)
    start_session("tick", now=BASE)
    second = start_session("tick", now=BASE + timedelta(seconds=1))

    assert second is None
    with DBSession(db_engine) as db:
        assert len(db.exec(select(SessionRun)).all()) == 1


def _answer_open_dispatch(db_engine, bee_store, verdict, quality, rubric_hit=None, raw_text="a reasonably long spoken answer with plenty of extra words included here to be safe", spoken_offset_s=5):
    """Simulates a spoken answer arriving for whatever dispatch is
    currently open, by seeding a matching utterance."""
    with DBSession(db_engine) as db:
        open_d = db.exec(select(Dispatch).where(Dispatch.status == "pending")).first()
        db.add(Utterance(id=open_d.id * 1000, conversation_id=1, conversation_uuid="x", text=raw_text, spoken_at=open_d.alarm_at + timedelta(seconds=spoken_offset_s)))
        db.commit()


NO_ANSWER_AFTER_S = 245  # just past the 4-min (240s) no-speech hard cap (Phase 5G)

# Phase 5F answer-done rule needs BOTH no new arrival for 15s AND the
# utterance's own spoken_at to be >=40s old -- and arrival is tracked from
# whenever advance() FIRST sees it, so a single call can never satisfy the
# arrival-quiet condition on the same tick new speech showed up. Tests that
# need a dispatch to actually grade call advance() twice: once right after
# the utterance is seeded (registers arrival), once comfortably past both
# thresholds (grades).
REGISTER_AFTER_S = 6  # just after the utterance (seeded at alarm+5s) exists
READY_AFTER_ANSWER_S = 50  # >=15s after REGISTER_AFTER_S and >=40s after spoken_at (alarm+5s)


def _advance_to_ready(alarm_at: datetime):
    from recall.session import advance

    advance(now=alarm_at + timedelta(seconds=REGISTER_AFTER_S))  # registers arrival, returns None (wait)
    return advance(now=alarm_at + timedelta(seconds=READY_AFTER_ANSWER_S))


def _open_dispatch_alarm_at(db_engine) -> datetime:
    with DBSession(db_engine) as db:
        d = db.exec(select(Dispatch).where(Dispatch.status == "pending")).first()
        return d.alarm_at


def test_quota_hit_ends_session_completed_and_posts_report(db_engine, bee_store, patch_provider, monkeypatch):
    from dataclasses import replace

    import recall.session as session_module
    from recall.session import advance, start_session

    # settings is a frozen dataclass -- swap the whole module-level instance
    # recall.session reads, rather than trying to mutate a frozen field.
    monkeypatch.setattr(session_module, "settings", replace(session_module.settings, daily_quota=2))

    _seed_questions(db_engine, 3)
    patch_provider(GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nice."))

    now = BASE
    run = start_session("tick", now=now)

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "next_question_dispatched"

    _answer_open_dispatch(db_engine, bee_store, "correct", 5)
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "session_completed_quota"

    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "completed"
        assert run_row.questions_asked == 2
        assert run_row.questions_answered == 2

    store, _ = bee_store
    report_texts = [v["text"] for v in store.values() if "need another look" in v["text"]]
    assert len(report_texts) == 1
    assert "2 answered, 2 solid, 0 need another look" in report_texts[0]


def test_utterance_arriving_at_3min_is_graded_not_missed(db_engine, bee_store, patch_provider):
    """Phase 5G Step 1 required test: speech arriving well after the old
    2-min defer threshold, but before the new 4-min no_answer timeout,
    must still be graded normally -- not missed."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 1)
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b."))

    now = BASE
    start_session("tick", now=now)
    alarm_at = _open_dispatch_alarm_at(db_engine)

    _answer_open_dispatch(db_engine, bee_store, "partial", 3, spoken_offset_s=180)  # 3 min in

    result = advance(now=alarm_at + timedelta(seconds=184))  # registers arrival, still waiting
    assert result is None

    result = advance(now=alarm_at + timedelta(seconds=230))  # 46s arrival-quiet, 50s spoken-quiet, still <240s
    assert result is not None
    assert result["event"] == "session_completed_exhausted"  # only 1 question -- grading it ends the session

    with DBSession(db_engine) as db:
        run_row = db.exec(select(SessionRun)).first()
        assert run_row.status == "completed"  # NOT ended_no_answer -- this was a real, graded answer
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        answer = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert answer.verdict == "partial"  # graded on its real content, not missed


def test_no_answer_after_4min_silence_ends_session_cleanly_no_penalty(db_engine, bee_store, patch_provider):
    """Phase 5G Step 1 required test: no speech at all by alarm_at + 4 min
    -> no_answer, no SM-2 change, session ends cleanly, remaining due
    questions stay due (no Dispatch created for them)."""
    from recall.db import ReviewState
    from recall.session import advance, start_session

    _seed_questions(db_engine, 2)
    now = BASE
    run = start_session("tick", now=now)
    alarm_at = _open_dispatch_alarm_at(db_engine)

    result = advance(now=alarm_at + timedelta(seconds=NO_ANSWER_AFTER_S))

    assert result["event"] == "session_ended_no_answer"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "ended_no_answer"
        assert run_row.questions_asked == 1
        assert run_row.questions_answered == 0

        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "answered"
        a1 = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1.verdict == "no_answer"
        assert db.exec(select(ReviewState).where(ReviewState.question_id == 1)).first() is None  # SM-2 untouched

        # Question 2 was never dispatched -- it stays due for later.
        assert db.exec(select(Dispatch).where(Dispatch.question_id == 2)).first() is None
        q2 = db.get(Question, 2)
        assert q2.active is True

    store, _ = bee_store
    report_texts = [v["text"] for v in store.values() if "need another look" in v["text"]]
    assert len(report_texts) == 1
    assert "0 answered, 0 solid, 0 need another look" in report_texts[0]


def test_restart_mid_session_resumes_same_open_dispatch(db_engine, bee_store, patch_provider):
    """Simulates a daemon restart: start_session() must not be callable
    again (one session active), and advance() must find the SAME open
    dispatch rather than losing or duplicating it."""
    from recall.session import has_active_session, open_dispatch_for, start_session

    _seed_questions(db_engine, 3)
    run = start_session("tick", now=BASE)

    with DBSession(db_engine) as db:
        original_dispatch = open_dispatch_for(db, run.id)
        assert original_dispatch is not None
        original_dispatch_id = original_dispatch.id

    # "Restart": nothing in memory survives, but the DB state does.
    with DBSession(db_engine) as db:
        resumed_run = has_active_session(db)
        assert resumed_run.id == run.id
        resumed_dispatch = open_dispatch_for(db, resumed_run.id)
        assert resumed_dispatch.id == original_dispatch_id


def test_recover_resolves_no_answer_after_long_sleep_gap(db_engine, bee_store, patch_provider):
    """Phase 5G: recover() is now a thin wrapper around advance() -- a long
    sleep/wake gap just means the open dispatch's window is classified the
    same way a normal late poll would: no_answer once past the 4-min hard
    cap, ending the session cleanly."""
    from recall.session import recover, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    run = start_session("tick", now=now)

    much_later = now + timedelta(hours=6)
    result = recover(now=much_later)

    assert result["event"] == "session_ended_no_answer"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "ended_no_answer"
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "answered"
        a1 = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1.verdict == "no_answer"


def test_recover_is_a_noop_when_window_has_not_passed_yet(db_engine, bee_store, patch_provider):
    from recall.session import recover, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    start_session("tick", now=now)

    result = recover(now=now + timedelta(seconds=30))

    assert result is None
    with DBSession(db_engine) as db:
        run_row = db.exec(select(SessionRun)).first()
        assert run_row.status == "active"


def test_recover_is_a_noop_when_no_active_session(db_engine):
    from recall.session import recover

    assert recover(now=BASE) is None


def test_late_arriving_speech_never_closes_the_window_early(db_engine, bee_store, patch_provider):
    """Phase 5F Step 1: simulates the realtime-transcript lag (Phase 5D:
    25-39s+) by having new utterances keep ARRIVING (becoming visible to
    advance()) well after they were actually spoken, staggered close
    enough together that the 15s-no-new-arrival condition never gets a
    chance to be satisfied -- the window must stay open throughout."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 1)
    now = BASE
    start_session("tick", now=now)
    alarm_at = _open_dispatch_alarm_at(db_engine)

    # 3 utterances, each actually spoken a while ago (lag), but only
    # becoming visible (inserted into the DB) at staggered 10s intervals --
    # advance() must see each one as a fresh arrival and keep waiting.
    for i, lag_s in enumerate([30, 40, 50]):
        poll_time = alarm_at + timedelta(seconds=10 * (i + 1))
        with DBSession(db_engine) as db:
            db.add(
                Utterance(
                    id=5000 + i,
                    conversation_id=1,
                    conversation_uuid="x",
                    text=f"chunk {i} of a slow-to-transcribe answer",
                    spoken_at=poll_time - timedelta(seconds=lag_s),
                )
            )
            db.commit()
        result = advance(now=poll_time)
        assert result is None  # still waiting -- a new utterance just arrived

    with DBSession(db_engine) as db:
        assert db.exec(select(Answer)).first() is None  # never graded while speech kept arriving


def test_feedback_text_is_plain_words_no_symbols(db_engine, bee_store, patch_provider):
    """AGENT.md copy rule: no emoji, no checkmarks -- Bee rewrites them
    away before the wearer ever sees them."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 1)
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed the second point."))

    now = BASE
    start_session("tick", now=now)
    _answer_open_dispatch(db_engine, bee_store, "partial", 3)
    _advance_to_ready(_open_dispatch_alarm_at(db_engine))

    store, _ = bee_store
    # Feedback todo: not deleted (unlike the question todo, which was),
    # not the question text itself, not the end-of-session report.
    feedback_texts = [
        v["text"]
        for v in store.values()
        if v["text"].startswith("Recall:") and not v["deleted"] and "need another look" not in v["text"] and "Question 1?" not in v["text"]
    ]
    assert len(feedback_texts) == 1
    text = feedback_texts[0]
    assert "✓" not in text
    assert "✗" not in text
    assert "/" not in text  # no "1/2" shorthand
    for ch in text:
        assert ord(ch) < 0x1F000  # no emoji range characters


class _SequencedProvider(LLMProvider):
    """Returns a different GradeResult per call, in order -- for tests
    where the grader's diagnostic answer differs across calls within one
    advance() cascade (current question, then the next one)."""

    def __init__(self, results: list[GradeResult]):
        self.results = list(results)
        self.calls: list[tuple[str, str | None]] = []

    def generate_questions(self, transcript, n, target_language=None):
        raise NotImplementedError

    def grade_answer(self, question, spoken_answer, previous_question=None):
        self.calls.append((question.text, previous_question.text if previous_question else None))
        return self.results.pop(0)

    def shorten_question(self, text, max_chars):
        raise NotImplementedError


def test_grader_diagnostic_previous_is_logged_but_never_moves_data(db_engine, bee_store, monkeypatch):
    """Phase 5G Step 1: the grader's current|previous|neither field is kept
    as a diagnostic only -- even when it says 'previous', the current
    dispatch is graded on ITS OWN content and the previous dispatch's
    Answer is left exactly as it already was. This replaces Phase 5F's
    live reassignment, which turned out to loop forever in production
    (FRICTION_LOG.md, 2-day check Incident 3)."""
    from recall.session import advance, start_session

    provider = _SequencedProvider(
        [
            GradeResult(verdict="not_an_answer", rubric_points_hit=[], quality=0, feedback="off-topic", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b.", answers="previous"),
        ]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 2)
    now = BASE
    start_session("tick", now=now)

    _answer_open_dispatch(db_engine, bee_store, "not_an_answer", 0, raw_text="restating question one only with some extra words included here to be long enough")
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "next_question_dispatched"

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        a1_before = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1_before.verdict == "not_an_answer"
        a1_before_id = a1_before.id

    _answer_open_dispatch(db_engine, bee_store, "partial", 3, raw_text="Q2's own content, even though the grader flags it as answering Q1 instead")
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "session_completed_exhausted"

    with DBSession(db_engine) as db:
        # Q1's Answer is untouched -- no merge, no regrade, same row.
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        a1_after = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1_after.id == a1_before_id
        assert a1_after.verdict == "not_an_answer"

        # Q2 is graded on its own GradeResult, literally -- "answers:
        # previous" never redirects the data anywhere.
        d2 = db.exec(select(Dispatch).where(Dispatch.question_id == 2)).first()
        a2 = db.exec(select(Answer).where(Answer.dispatch_id == d2.id)).first()
        assert a2.verdict == "partial"
        assert "Q2's own content" in a2.raw_text

        run_row = db.exec(select(SessionRun)).first()
        # not_an_answer still counts as "answered" (only no_answer doesn't,
        # AGENT.md) -- both Q1 and Q2 count, unchanged by Phase 5G.
        assert run_row.questions_answered == 2

    assert provider.calls[1][1] == "Question 1?"  # the diagnostic lookup still happened


def test_classify_window_excludes_already_claimed_utterances(db_engine, bee_store):
    """Phase 5G Step 2: once an utterance is claimed by some Answer, it is
    excluded from every future answer-window query -- the structural fix
    for Incident 3 (a dispatch re-matching the same already-used
    utterances forever once its original hard cap had elapsed)."""
    from recall.session import _classify_window, start_session

    _seed_questions(db_engine, 1)
    start_session("tick", now=BASE)
    with DBSession(db_engine) as db:
        dispatch = db.exec(select(Dispatch)).first()
        u = Utterance(id=12345, conversation_id=1, conversation_uuid="x", text="already used elsewhere", spoken_at=dispatch.alarm_at + timedelta(seconds=5))
        db.add(u)
        db.commit()

        u.claimed_by_dispatch_id = 999  # simulates having already fed some other Answer
        db.add(u)
        db.commit()

        status, match = _classify_window(db, dispatch, dispatch.alarm_at + timedelta(seconds=60))
        assert match.utterances == []
        assert status == "wait"  # no unclaimed speech, and the 4-min hard cap hasn't hit yet


def test_grade_skipped_when_utterance_set_unchanged_since_last_grade(db_engine, bee_store, monkeypatch):
    """Phase 5G Step 2: a dispatch is only (re)graded when its utterance
    set has actually changed since the last grade -- calling the guard
    again with the identical set must not call the LLM a second time."""
    from recall.matcher import MatchResult
    from recall.matcher import Utterance as MatcherUtterance
    from recall.session import _grade_with_guards, _persist_answer, start_session

    provider = _SequencedProvider([GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b.", answers="current")])
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 1)
    start_session("tick", now=BASE)
    with DBSession(db_engine) as db:
        dispatch = db.exec(select(Dispatch)).first()
        question = db.get(Question, dispatch.question_id)
        db.add(Utterance(id=55001, conversation_id=1, conversation_uuid="x", text="a stable answer", spoken_at=dispatch.alarm_at + timedelta(seconds=5)))
        db.commit()
        match = MatchResult(
            utterances=[MatcherUtterance(id=55001, text="a stable answer", spoken_at=dispatch.alarm_at + timedelta(seconds=5))],
            raw_text="a stable answer",
            trigger_found=False,
        )

        result = _grade_with_guards(db, dispatch, question, None, match)
        assert result is not None
        _persist_answer(db, dispatch, result, match, dispatch.alarm_at + timedelta(seconds=20))

        dispatch = db.get(Dispatch, dispatch.id)
        assert dispatch.grade_call_count == 1

        # Same dispatch, same utterance set -- must be skipped, not re-called.
        result_again = _grade_with_guards(db, dispatch, question, None, match)
        assert result_again is None
        dispatch = db.get(Dispatch, dispatch.id)
        assert dispatch.grade_call_count == 1  # unchanged -- no second LLM call
        assert len(provider.calls) == 1


def test_grade_call_guard_caps_at_three_then_needs_review(db_engine, bee_store, monkeypatch):
    """Phase 5G Step 2: max 3 grader calls per dispatch -- on the 4th, stop
    and mark the dispatch needs_review instead of calling the LLM again
    (the circuit breaker for any future bug of Incident 3's shape)."""
    from recall.matcher import MatchResult
    from recall.matcher import Utterance as MatcherUtterance
    from recall.session import _grade_with_guards, start_session

    provider = _SequencedProvider(
        [
            GradeResult(verdict="partial", rubric_points_hit=[], quality=3, feedback="1", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=[], quality=3, feedback="2", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=[], quality=3, feedback="3", answers="current"),
        ]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 1)
    start_session("tick", now=BASE)
    with DBSession(db_engine) as db:
        dispatch = db.exec(select(Dispatch)).first()
        question = db.get(Question, dispatch.question_id)
        dispatch_id, question_id, alarm_at = dispatch.id, question.id, dispatch.alarm_at

    # 3 calls, each with a different (never-persisted) utterance set, so
    # the "unchanged since last grade" skip never fires -- isolates the
    # call-count cap itself.
    for i in range(3):
        uid = 70000 + i
        with DBSession(db_engine) as db:
            db.add(Utterance(id=uid, conversation_id=1, conversation_uuid="x", text=f"changing answer {i}", spoken_at=alarm_at + timedelta(seconds=5)))
            db.commit()
            dispatch = db.get(Dispatch, dispatch_id)
            question = db.get(Question, question_id)
            match = MatchResult(
                utterances=[MatcherUtterance(id=uid, text=f"changing answer {i}", spoken_at=alarm_at + timedelta(seconds=5))],
                raw_text=f"changing answer {i}",
                trigger_found=False,
            )
            result = _grade_with_guards(db, dispatch, question, None, match)
            assert result is not None

    with DBSession(db_engine) as db:
        dispatch = db.get(Dispatch, dispatch_id)
        assert dispatch.grade_call_count == 3

        uid = 70099
        db.add(Utterance(id=uid, conversation_id=1, conversation_uuid="x", text="one too many", spoken_at=alarm_at + timedelta(seconds=5)))
        db.commit()
        question = db.get(Question, question_id)
        match = MatchResult(
            utterances=[MatcherUtterance(id=uid, text="one too many", spoken_at=alarm_at + timedelta(seconds=5))],
            raw_text="one too many",
            trigger_found=False,
        )
        result = _grade_with_guards(db, dispatch, question, None, match)
        assert result is None

        dispatch = db.get(Dispatch, dispatch_id)
        assert dispatch.status == "needs_review"
    assert len(provider.results) == 0  # exactly 3 LLM calls made, not 4
