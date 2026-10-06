from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from recall.db import Answer, Conversation, Dispatch, FeedbackTodo, Question
from recall.db import Session as SessionModel
from recall.db import SessionRun
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


def _answer_open_dispatch(db_engine, bee_store, verdict, quality, rubric_hit=None, raw_text="a reasonably long spoken answer with plenty of extra words included here to be safe"):
    """Simulates a spoken answer arriving for whatever dispatch is
    currently open, by seeding a matching utterance."""
    from recall.db import Utterance

    with DBSession(db_engine) as db:
        open_d = db.exec(select(Dispatch).where(Dispatch.status == "pending")).first()
        db.add(Utterance(id=open_d.id * 1000, conversation_id=1, conversation_uuid="x", text=raw_text, spoken_at=open_d.alarm_at + timedelta(seconds=5)))
        db.commit()


DEFER_AFTER_S = 125  # just past the 2-min no-speech hard cap

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


def test_single_deferred_question_does_not_end_session(db_engine, bee_store, patch_provider):
    """Phase 5E Step 3: one deferred (no-speech-by-2min) dispatch just
    moves on to the next question -- it takes TWO consecutive deferred
    dispatches to pause the session, not one."""
    from recall.session import advance, start_session

    _seed_questions(db_engine, 3)

    now = BASE
    run = start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)

    # No utterance seeded -- nothing arrives within the 2-min hard cap.
    result = advance(now=defer_at)

    assert result["event"] == "deferred_next_question_dispatched"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "active"
        assert run_row.questions_asked == 2
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"
        # Not graded yet -- no Answer row at all, no SM-2 touched.
        assert db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first() is None
        q1 = db.get(Question, 1)
        assert q1.active is True


def test_two_consecutive_deferred_pauses_session_no_penalty(db_engine, bee_store, patch_provider):
    from recall.session import advance, start_session

    _seed_questions(db_engine, 3)

    now = BASE
    run = start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)

    result = advance(now=defer_at)
    assert result["event"] == "deferred_next_question_dispatched"

    result = advance(now=defer_at + timedelta(seconds=DEFER_AFTER_S))
    assert result["event"] == "session_paused_two_deferred"

    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "paused"
        assert run_row.questions_asked == 2
        assert run_row.questions_answered == 0
        for qid in (1, 2):
            d = db.exec(select(Dispatch).where(Dispatch.question_id == qid)).first()
            assert d.status == "processing"

    store, _ = bee_store
    report_texts = [v["text"] for v in store.values() if "need another look" in v["text"]]
    assert len(report_texts) == 1
    assert "0 answered, 0 solid, 0 need another look" in report_texts[0]


def test_deferred_dispatch_graded_later_from_final_transcript(db_engine, bee_store, patch_provider):
    """Phase 5E Step 3: a deferred dispatch is graded in the background
    once the final transcript shows an utterance landing inside
    [this alarm_at, next alarm_at) -- matched by spoken_at, no feedback
    todo posted (dashboard-only)."""
    from recall.db import Utterance
    from recall.session import advance, grade_pending_dispatches, start_session

    _seed_questions(db_engine, 3)
    patch_provider(GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b."))

    now = BASE
    start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)
    result = advance(now=defer_at)  # question 1 deferred, question 2 dispatched
    assert result["event"] == "deferred_next_question_dispatched"

    store_before, _ = bee_store
    todos_before = len(store_before)

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        # A late-arriving final-transcript utterance, inside [d1.alarm_at, d2.alarm_at).
        db.add(Utterance(id=99001, conversation_id=1, conversation_uuid="x", text="a late answer that finally came through with plenty of extra words to be safe", spoken_at=d1.alarm_at + timedelta(seconds=30)))
        db.commit()

    results = grade_pending_dispatches(now=defer_at + timedelta(seconds=60))
    assert len(results) == 1
    assert results[0]["question_id"] == 1
    assert results[0]["verdict"] == "partial"

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "answered"
        answer = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert answer.verdict == "partial"
        run_row = db.exec(select(SessionRun)).first()
        assert run_row.questions_answered == 1  # updated even though the session already moved on

    # No new feedback todo posted for the deferred grade -- dashboard-only.
    store_after, _ = bee_store
    assert len(store_after) == todos_before


def test_grade_pending_dispatches_is_a_noop_when_nothing_new_arrived(db_engine, bee_store, patch_provider):
    from recall.session import advance, grade_pending_dispatches, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    start_session("tick", now=now)
    defer_at = now + timedelta(seconds=DEFER_AFTER_S)
    advance(now=defer_at)

    results = grade_pending_dispatches(now=defer_at + timedelta(seconds=30))
    assert results == []
    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"  # still pending resolution


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


def test_recover_defers_stale_dispatch_and_pauses_session(db_engine, bee_store, patch_provider):
    """Phase 5E: recover() no longer forces an immediate no_answer -- a
    sleep/wake gap can be long, and a real answer might just be slow to
    transcribe (Phase 5D). It defers instead (graded later from the final
    transcript, same as any other deferred dispatch) and pauses the
    session, no penalty."""
    from recall.session import recover, start_session

    _seed_questions(db_engine, 3)
    now = BASE
    run = start_session("tick", now=now)

    # Simulate a laptop-sleep gap: recover() is called much later, well
    # past the answer window, even though nothing ever polled in between.
    much_later = now + timedelta(hours=6)
    result = recover(now=much_later)

    assert result["event"] == "recovered_stale_session"
    with DBSession(db_engine) as db:
        run_row = db.get(SessionRun, run.id)
        assert run_row.status == "paused"
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "processing"
        assert db.exec(select(Answer)).first() is None  # not graded yet -- deferred


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
    from recall.db import Utterance
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
    """Returns a different GradeResult per call, in order -- for
    reassignment tests, where the grader's decision differs across calls
    within one advance() cascade (current question, then previous)."""

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


def test_answer_reassigned_to_previous_question_when_grader_says_previous(db_engine, bee_store, monkeypatch):
    """Phase 5F Step 2: a late-arriving real answer to Q1, captured in
    Q2's window (the exact Phase 5E live-test bleed), gets reassigned to
    Q1's dispatch -- merged with Q1's existing (wrong) answer, regraded
    once, while Q2 is left exactly as if nothing had arrived for it."""
    from recall.session import advance, start_session

    provider = _SequencedProvider(
        [
            GradeResult(verdict="not_an_answer", rubric_points_hit=[], quality=0, feedback="off-topic", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b.", answers="previous"),
            GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nailed it.", answers="current"),
        ]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 2)
    now = BASE
    start_session("tick", now=now)

    # Q1: a restatement-only "answer" -- graded not_an_answer (no previous question exists yet, it's the first dispatch).
    _answer_open_dispatch(
        db_engine, bee_store, "not_an_answer", 0,
        raw_text="restating question one only with some extra words included here to be long enough",
    )
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "next_question_dispatched"

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        a1_before = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1_before.verdict == "not_an_answer"
        a1_before_id = a1_before.id

    # Q2's window actually captures Q1's real, late-arriving answer.
    _answer_open_dispatch(
        db_engine, bee_store, "partial", 3,
        raw_text="the real answer to question one arrived late with plenty of extra words here",
    )
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "reassigned_to_previous"
    assert result["previous_question_id"] == 1

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        a1_after = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1_after.id == a1_before_id  # same row, updated in place -- not duplicated
        assert a1_after.verdict == "correct"  # corrected by the regrade
        assert "restating question one" in a1_after.raw_text
        assert "real answer to question one" in a1_after.raw_text  # merged, chronological

        d2 = db.exec(select(Dispatch).where(Dispatch.question_id == 2)).first()
        assert d2.status == "pending"  # current dispatch untouched -- still open
        assert db.exec(select(Answer).where(Answer.dispatch_id == d2.id)).first() is None

        run_row = db.exec(select(SessionRun)).first()
        assert run_row.questions_answered == 1  # Q1 counts once (not_an_answer didn't, correct does) -- not double-counted

    assert [c[0] for c in provider.calls] == ["Question 1?", "Question 2?", "Question 1?"]
    assert provider.calls[1][1] == "Question 1?"  # Q2's grade call was given Q1 as previous_question
    assert provider.calls[2][1] is None  # the regrade call is a plain grade, no further lookback


def test_reassignment_regrade_replays_sm2_from_scratch(db_engine, bee_store, monkeypatch):
    """The first (wrong) grade of Q1 may already have fed SM-2 once;
    reassignment must leave ReviewState reflecting only the corrected
    grade, not both stacked on top of each other."""
    from recall.db import ReviewState
    from recall.session import advance, start_session

    provider = _SequencedProvider(
        [
            GradeResult(verdict="incorrect", rubric_points_hit=[], quality=1, feedback="Wrong.", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=["a"], quality=3, feedback="Missed b.", answers="previous"),
            GradeResult(verdict="correct", rubric_points_hit=["a", "b"], quality=5, feedback="Nailed it.", answers="current"),
        ]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 2)
    now = BASE
    start_session("tick", now=now)
    _answer_open_dispatch(db_engine, bee_store, "incorrect", 1, raw_text="a wrong answer to question one with plenty of extra words here")
    _advance_to_ready(_open_dispatch_alarm_at(db_engine))

    with DBSession(db_engine) as db:
        rs = db.exec(select(ReviewState).where(ReviewState.question_id == 1)).first()
        assert rs is not None  # SM-2 already touched once, under the wrong verdict

    _answer_open_dispatch(db_engine, bee_store, "partial", 3, raw_text="the real correct answer to question one arrived late with extra words")
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "reassigned_to_previous"

    with DBSession(db_engine) as db:
        rs = db.exec(select(ReviewState).where(ReviewState.question_id == 1)).first()
        # Replayed from scratch with ONLY the corrected quality=5 grade:
        # ease_factor=2.6 (2.5 + 0.1). If the wrong quality=1 grade had
        # also fed SM-2 first (double-counted), ease_factor would be 2.06
        # instead (1.96 after quality=1, +0.1 for quality=5) -- a real,
        # observable difference, not just an internal detail.
        assert rs.repetitions == 1
        assert rs.interval_days == 1
        assert rs.ease_factor == pytest.approx(2.6)


# Phase 5F Step 2: real fixture from the Phase 5E live test's actual bleed
# (session 3, dispatches 16-18 in the live recall.db). No redaction needed
# -- the content is generic chemistry/history trivia from AI/ML-interview
# practice material, not personal or identifying. Reproduced here to prove
# the reassignment logic actually fixes the exact case that motivated it.
_Q2_CHOCOLATE_TEXT = "Using chocolate as an example, explain what polymorphs are and how they affect properties."
_Q2_RESTATEMENT = "So, the question is explaining the chocolate as an example with a few more words here"
_Q3_RITONAVIR_TEXT = "Why was discovering Ritonavir Form II problematic for Abbott despite being chemically identical?"
_Q3_RAW_TEXT_CAPTURED = (
    "So this is exactly how polymorphs works when polymorphs can also have can also be "
    "same substance but have different properties and make different substances."
)


def test_live_test_q3_bleed_fixture_gets_reassigned_to_q2(db_engine, bee_store, monkeypatch):
    """Reproduces dispatch 17 from the Phase 5E live test almost exactly:
    Q2 (chocolate polymorphs) only captured a restatement and graded
    incomplete_capture/not_an_answer; the operator's real polymorphs
    explanation arrived late and was captured in Q3's (Ritonavir) window
    instead, where it was wrongly graded 'off-topic' for Ritonavir. With
    Phase 5F's reassignment, that same captured text should now correctly
    land back on Q2."""
    from recall.session import advance, start_session

    provider = _SequencedProvider(
        [
            GradeResult(verdict="incomplete_capture", rubric_points_hit=[], quality=0, feedback="Only caught a few words, will ask again", answers="current"),
            GradeResult(verdict="partial", rubric_points_hit=["polymorphs"], quality=3, feedback="Missed: chocolate tempering detail.", answers="previous"),
            GradeResult(verdict="partial", rubric_points_hit=["polymorphs"], quality=3, feedback="Missed: chocolate tempering detail.", answers="current"),
        ]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    with DBSession(db_engine) as db:
        db.add(Conversation(id=1))
        _approved_session(db)
        _question(db, 1, text=_Q2_CHOCOLATE_TEXT, rubric=["polymorphs", "tempering"])
        _question(db, 2, text=_Q3_RITONAVIR_TEXT, rubric=["solubility", "bioavailability"])
        db.commit()

    now = BASE
    start_session("tick", now=now)
    _answer_open_dispatch(db_engine, bee_store, "incomplete_capture", 0, raw_text=_Q2_RESTATEMENT)
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))
    assert result["event"] == "next_question_dispatched"

    _answer_open_dispatch(db_engine, bee_store, "partial", 3, raw_text=_Q3_RAW_TEXT_CAPTURED)
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))

    assert result["event"] == "reassigned_to_previous"
    assert result["previous_question_id"] == 1

    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        a1 = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1.verdict == "partial"  # corrected -- no longer incomplete_capture
        assert "polymorphs works" in a1.raw_text  # the real content, now on the right question

        d2 = db.exec(select(Dispatch).where(Dispatch.question_id == 2)).first()
        assert d2.status == "pending"
        assert db.exec(select(Answer).where(Answer.dispatch_id == d2.id)).first() is None  # Ritonavir question still genuinely unanswered


def test_answer_not_reassigned_when_grader_says_current(db_engine, bee_store, monkeypatch):
    """Sanity check: when the grader says the speech answers the current
    question (the normal case), no reassignment machinery fires at all."""
    from recall.session import advance, start_session

    provider = _SequencedProvider(
        [GradeResult(verdict="correct", rubric_points_hit=["a"], quality=5, feedback="Nice.", answers="current")]
    )
    monkeypatch.setattr("recall.grading.get_provider", lambda: provider)

    _seed_questions(db_engine, 2)
    now = BASE
    start_session("tick", now=now)
    _answer_open_dispatch(db_engine, bee_store, "correct", 5, raw_text="a perfectly normal on-topic answer with plenty of extra words here")
    result = _advance_to_ready(_open_dispatch_alarm_at(db_engine))

    assert result["event"] == "next_question_dispatched"
    with DBSession(db_engine) as db:
        d1 = db.exec(select(Dispatch).where(Dispatch.question_id == 1)).first()
        assert d1.status == "answered"
        a1 = db.exec(select(Answer).where(Answer.dispatch_id == d1.id)).first()
        assert a1.verdict == "correct"
