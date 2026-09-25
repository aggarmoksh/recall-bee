**Resolved 2026-09-24 by advisor, implemented in Phase 2 Step 1** -- see AGENT.md "Schema decisions": Answer.dispatch_id is now unique (one graded Answer per Dispatch), Answer.utterance_id became Answer.utterance_ids (JSON list), and a new Session model (derived from spoken_at gaps > 10 min) replaces Conversation as the session boundary, with Question gaining session_id/source_start/source_end. Kept below as a historical record of the reasoning.

# Phase 1 — schema decisions flagged for the advisor

Per the Phase 1 brief: these are schema choices that affect matching or grading. I picked the simplest option for each so Phase 1 has a working skeleton, but none of these are locked -- flagging them here rather than deciding silently.

## 1. Answer <-> Dispatch linkage

**Chosen:** `Answer.dispatch_id` is a plain FK to `Dispatch.id`, not unique. Multiple `Answer` rows can point at the same `Dispatch` (e.g. if a spoken explanation gets matched across more than one utterance-arrival event, or the wearer re-answers).

**Why simplest:** a straight FK is the smallest schema that still lets Phase 5's matcher (utterance `spoken_at` within a window after `alarm_at`, fuzzy trigger-phrase match) write one row per matched utterance without needing to decide up front how many answers a dispatch can have.

**Open question:** Phase 5 will need a way to mark which `Answer` row (if more than one) is authoritative for SM-2 grading -- e.g. first match, longest match, or an explicit `is_final` flag. Not designed yet.

## 2. Answer <-> Utterance: single utterance vs. multiple

**Chosen:** `Answer.utterance_id` is a single nullable FK to one `Utterance` row.

**Why this is a real simplification, not just a placeholder:** Phase 0.9 Step 3 showed a single 45-60s explain-back answer can arrive as **more than one** `new-utterance` event (trial 1 split into 2 separate utterances, ~17s apart). A single FK can't represent "this answer is utterances #4821 and #4822 together" -- only one of them. Two ways to fix this, not yet chosen between:
- (a) A join table `AnswerUtterance(answer_id, utterance_id)` -- clean, handles any number of utterances per answer.
- (b) Store `Answer.raw_text` as the already-concatenated text from a matching window, and drop the utterance FK to a `first_utterance_id` (evidence pointer only, not a structural link).

For Phase 1 I went with a single FK because there's no grading logic yet to exercise the gap -- but Phase 5's matcher will hit this immediately (it needs to concatenate consecutive utterances within the answer window before grading). Recommend deciding (a) vs (b) before Phase 5 starts.

## 3. Question <-> Conversation: one source conversation per question

**Chosen:** `Question.conversation_id` is a single FK, implying one question's source material comes from exactly one Bee conversation.

**Known tension:** Phase 0.9 confirmed Bee can silently append unrelated later speech to the same conversation object (a Hindi recording joined an unrelated earlier conversation 50+ minutes later). So "one Conversation" is not the same as "one learning session" -- a single Conversation row could span multiple unrelated topics, or a real session's tail could spill into a separate Conversation. `Question.source_excerpt` (the literal quoted text) is the real evidence; `conversation_id` is best treated as a coarse pointer for now, not a session boundary. No fix needed yet since Phase 3 (question generator) hasn't been designed, but the ingestion logic in Phase 2 will need its own session-boundary heuristic based on `spoken_at` gaps, not `Conversation` rows.
