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


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)  # coarse pointer only
    session_id: int | None = Field(default=None, foreign_key="session.id", index=True)
    source_start: datetime | None = None
    source_end: datetime | None = None
    text: str
    expected_answer: str
    source_excerpt: str
    topic: str | None = None
    language: str | None = None  # Phase 7 target-language flag
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Dispatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    bee_todo_id: int = Field(index=True, unique=True)
    alarm_at: datetime
    status: str = "pending"  # pending / delivered / answered / missed
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Answer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dispatch_id: int = Field(foreign_key="dispatch.id", index=True, unique=True)
    utterance_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    raw_text: str
    matched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correct: bool | None = None
    quality: int | None = None  # SM-2 quality scale, 0-5
    feedback: str | None = None


class ReviewState(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, unique=True)
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_reviewed_at: datetime | None = None


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> DBSession:
    return DBSession(engine)
