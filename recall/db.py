"""SQLModel engine + schema.

Schema notes (see phase1/open_questions.md for the flagged decision):
- Conversation.id uses Bee's own conversation id as primary key -- no local
  indirection needed, and it makes upserts from ingestion trivial.
- Bee may append unrelated later speech to the same conversation (Phase 0.9
  pitfall), so a Conversation row is NOT a reliable session boundary by
  itself; session/topic grouping for question generation happens at the
  Utterance level via spoken_at, not here.
- Answer links to Dispatch via a single dispatch_id FK (simplest option,
  flagged in open_questions.md), and to at most one Utterance for now.
"""

from datetime import datetime

from sqlmodel import Field, Session, SQLModel, create_engine

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


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    text: str
    expected_answer: str
    source_excerpt: str
    topic: str | None = None
    language: str | None = None  # Phase 7 target-language flag
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Dispatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    bee_todo_id: int = Field(index=True, unique=True)
    alarm_at: datetime
    status: str = "pending"  # pending / delivered / answered / missed
    dispatched_at: datetime = Field(default_factory=datetime.utcnow)


class Answer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dispatch_id: int = Field(foreign_key="dispatch.id", index=True)
    utterance_id: int | None = Field(default=None, foreign_key="utterance.id")
    raw_text: str
    matched_at: datetime = Field(default_factory=datetime.utcnow)
    correct: bool | None = None
    quality: int | None = None  # SM-2 quality scale, 0-5
    feedback: str | None = None


class ReviewState(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, unique=True)
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_at: datetime = Field(default_factory=datetime.utcnow)
    last_reviewed_at: datetime | None = None


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
