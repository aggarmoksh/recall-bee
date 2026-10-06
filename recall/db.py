"""SQLModel engine + schema.

Schema notes:
- Conversation.id uses Bee's own conversation id as primary key -- no local
  indirection needed, and it makes upserts from ingestion trivial.
- Bee may append unrelated later speech to the same conversation (Phase 0.9
  pitfall), so a Conversation row is NOT a reliable session boundary.
  `Session` is the real boundary, derived from `spoken_at` gaps > 10 min
  (advisor decision, 2026-09-24 -- see AGENT.md "Schema decisions").
- `Answer.dispatch_id` is unique -- one graded Answer per Dispatch.
  `raw_text` is the concatenated text of every utterance in the answer
  window; `utterance_ids` (JSON list) is the evidence trail. Grade once
  when the window closes (advisor decision).
- `Question.conversation_id` stays as a coarse pointer; `session_id` +
  `source_start`/`source_end` are the actual source-material reference.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

DB_PATH = "recall.db"
engine = create_engine(f"sqlite:///{DB_PATH}")


class Conversation(SQLModel, table=True):
    id: int = Field(primary_key=True)  # Bee's conversation id
    conversation_uuid: str | None = Field(default=None, index=True)
    start_time: datetime | None = None
    end_time: datetime | None = None
    short_summary: str | None = None
    state: str | None = None  # CAPTURING / COMPLETED / etc. -- never gate logic on this
    last_ingested_at: datetime | None = None


class Utterance(SQLModel, table=True):
    id: int = Field(primary_key=True)  # Bee's utterance id
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    conversation_uuid: str = Field(index=True)
    text: str
    spoken_at: datetime = Field(index=True)
    speaker: str | None = None
    created_at: datetime | None = None


class Session(SQLModel, table=True):
    """A real learning session, derived from spoken_at gaps > 10 min --
    NOT the same thing as a Bee Conversation (see module docstring)."""

    id: int | None = Field(default=None, primary_key=True)
    start_at: datetime = Field(index=True)
    end_at: datetime | None = None
    conversation_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # Phase 4A classification (set by recall.questions.generate_for_session).
    # lecture_study | substantive_discussion | casual | personal
    session_type: str | None = None
    session_type_reason: str | None = None
    # none (unclassified or casual/personal -- no questions) | pending | approved | rejected
    approval_status: str = "none"


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)  # coarse pointer only
    session_id: int | None = Field(default=None, foreign_key="session.id", index=True)
    source_start: datetime | None = None
    source_end: datetime | None = None
    text: str
    expected_answer: str
    rubric: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # 2-4 key points, Phase 3
    source_excerpt: str
    topic: str | None = None
    language: str | None = None  # Phase 7 target-language flag
    active: bool = True  # False = retired (e.g. excluded_sessions.txt, --reject-question) -- never deleted
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Dispatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    bee_todo_id: int = Field(index=True, unique=True)
    alarm_at: datetime
    status: str = "pending"  # pending / processing (deferred) / answered
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # v4 (Phase 5C): which mini-session this question belongs to. NULL for
    # pre-v4 historical rows (the old timed-dispatch model had no sessions).
    session_run_id: int | None = Field(default=None, foreign_key="sessionrun.id", index=True)
    # Phase 5F answer-done rule: tracks when the current last-seen utterance
    # for this dispatch's window first ARRIVED (was first observed by our
    # own polling), distinct from when it was actually spoken -- "done"
    # requires both no new arrival for 15s AND the spoken_at itself being
    # >=40s old, to tolerate realtime-transcript lag (Phase 5D: 25-39s+).
    last_seen_utterance_id: int | None = None
    last_seen_utterance_at: datetime | None = None


class Answer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dispatch_id: int = Field(foreign_key="dispatch.id", index=True, unique=True)
    utterance_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    raw_text: str
    matched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verdict: str | None = None  # correct | partial | incorrect | not_an_answer | no_answer | incomplete_capture
    rubric_points_hit: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    quality: int | None = None  # SM-2 quality scale, 0-5
    feedback: str | None = None  # <=12 words, fits a notification
    operator_agrees: bool | None = None  # set by `python -m recall.review`
    hint_in_notification: bool | None = None  # Phase 5C Step 7: set by `python -m recall.review`


class ReviewState(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, unique=True)
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_reviewed_at: datetime | None = None


class FeedbackTodo(SQLModel, table=True):
    """Tracks every feedback todo Recall posts, so cleanup can delete only
    ids Recall itself created -- never touching the user's own todos."""

    id: int | None = Field(default=None, primary_key=True)
    bee_todo_id: int = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionRun(SQLModel, table=True):
    """One v4 mini-session (Phase 5C): a back-to-back run of due questions
    started by the user (tick todo or spoken start phrase), ended by the
    daily quota, running out of due questions, or one no_answer (no
    penalty -- the question stays due). Only one `status == "active"` row
    at a time; all state lives here so a daemon restart resumes correctly."""

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    trigger: str  # "tick" | "spoken"
    status: str = "active"  # active | completed | ended_no_answer
    questions_asked: int = 0
    questions_answered: int = 0  # non-no_answer verdicts


class StingTodo(SQLModel, table=True):
    """Tracks every sting (nudge) todo Recall posts, so cleanup can delete
    only ids Recall created, and so at most one sting per slot per local
    day is sent."""

    id: int | None = Field(default=None, primary_key=True)
    bee_todo_id: int = Field(index=True, unique=True)
    local_date: str = Field(index=True)  # YYYY-MM-DD, local day this sting belongs to
    slot: str  # "start" | "middle" | "end" | "post_lecture"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReadyTodo(SQLModel, table=True):
    """Tracks the single persistent 'Recall: tick when you can talk' todo.
    Recreated whenever it's ticked or goes missing (deleted)."""

    id: int | None = Field(default=None, primary_key=True)
    bee_todo_id: int = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StartPhraseUtterance(SQLModel, table=True):
    """Tracks utterances that matched the spoken session-start phrase, so
    they're excluded from question generation the same way answer-window
    utterances already are (no-self-feeding)."""

    id: int | None = Field(default=None, primary_key=True)
    utterance_id: int = Field(index=True, unique=True)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LLMCallLog(SQLModel, table=True):
    """One row per local calendar date, counting LLM calls made that day --
    enforces the 80/day hard cap (AGENT.md Loop policy)."""

    id: int | None = Field(default=None, primary_key=True)
    date: str = Field(index=True, unique=True)  # YYYY-MM-DD, local date
    count: int = 0


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> DBSession:
    return DBSession(engine)
