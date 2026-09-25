# Recall — Agent Brief (persistent, read every session)

You are the coding agent for **Recall**, a spaced-retrieval tutor built on Bee for the "Build, Ship, Shape: Amazon Developer Hackathon 2026" (Bee track, deadline Oct 23 2026 12:00pm PDT).

Your operator is a solo builder on Windows 10/11, git bash, VS Code. He runs a two-surface workflow: an advisor chat handles design and decisions, you handle code and commands. **You do not make design decisions.** When you hit one, stop and ask.

## Pitch — Recall v3 (locked 2026-09-24, do not drift)
Bee captures sustained learning speech (lectures, study sessions, family conversations in any language). An agent turns those transcripts into spaced-retrieval questions and delivers them as Bee todo alarms (Watch or iPhone, whichever Bee routes to). The wearer answers by **explaining the answer out loud** (target length under review: 15–20s if the Phase 3 short-answer test passes, else 45–60s). Saying "Recall answer" first is optional. Bee's ambient capture transcribes it. Recall matches that speech to the dispatched question, an LLM grades the explanation against the source transcript, and SM-2 reschedules. A target-language flag lets the same loop serve students and bilingual families.

Fallback grading path (only if the Phase 2 explain-back gate fails): self-grade by ticking the todo on iPhone, detected by polling REST for `completed: true`.

## Stack (LOCKED. Do not substitute without asking.)
- **Runtime:** Windows 10/11, git bash, Python 3.12, Node 20+
- **Backend:** FastAPI + Uvicorn
- **Bee integration:** `@beeai/cli` (binary `bee`). `bee proxy --port 8787` for REST, `bee stream` (SSE) for realtime events. All Bee access goes through ONE module (`recall/bee/`) so pitfalls are handled in one place.
- **Storage:** SQLite via `sqlmodel`. File `recall.db` at repo root, gitignored.
- **Spaced repetition:** SM-2, hand-implemented.
- **LLM:** behind a small provider interface (`recall/llm/`) with one method per job (generate_questions, grade_answer). Default provider: **Google Gemini** (`google-genai` SDK, current Flash model, key `GEMINI_API_KEY` in `.env`). Anthropic adapter optional later. Swapping must be a config change plus one adapter file, never a rewrite. Structured JSON output only.
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind v4. No component library.
- **Auth:** none. Single-user local app.
- **Deploy:** local only.
- **License:** MIT (Open Source mini).

## Quality bar
- Every phase ends with something demoable end-to-end, even if partial.
- Every Bee call is retried with backoff and logged raw to `logs/bee-events.ndjson`.
- A feature is done only after one full loop on real Bee data: real session → real question → real alarm → real spoken explain-back → real grade → next schedule.
- Prefer manual override files (blocklists, seed banks) over prompt hacks.
- Tests: pytest. Each phase adds an integration test against recorded fixtures in `tests/fixtures/` (seed from `phase0/*.ndjson`), never live Bee.

## Anti-features (DO NOT BUILD without asking)
- No Xcode, native iOS/Android, React Native.
- No custom ASR/TTS. No audio capture, storage, or playback.
- No accounts, OAuth, multi-user.
- No Slack, M365, GitHub, LMS integrations (see POSTLAUNCH.md).
- No Ring, Alexa+, Fire TV code.
- No observability stacks. File logs only.
- No agent frameworks (LangGraph, CrewAI, AutoGen). Plain function calls.
- No Docker.
- No tap/todo-completion grading UI unless the advisor activates the fallback.
- Never commit `.env`, `recall.db`, `logs/`, `.venv/`, `node_modules/`, tokens, or CA certs.

## Phase gates (STOP at each boundary)
At the end of every phase print exactly `Phase N complete. Consult advisor with: [artifact list]`, list the files/metrics to review, and wait for the next paste.

- **Phase 0 / 0.5 / 0.75 / 0.9 — Validation (DONE).** Evidence in `phase0/`. Keep that folder; it feeds the README and friction log.
- **Phase 1 — Repo bootstrap + Bee client module + backend skeleton.**
- **Phase 2 — Privacy scrub + schema update + explain-back reliability gate (10 trials, pass = 8/10) + ingestion.**
- **Phase 3 — Question generator** (transcript → questions with rubric + source excerpt).
- **Phase 4 — Dispatch loop** (SM-2 picks due question → todo with alarm_at → mark dispatched, idempotent).
- **Phase 5 — Answer matching + grading** (utterances after alarm with trigger phrase → grade vs rubric/source → SM-2 update).
- **Phase 6 — Dashboard** (due today, mastery by topic, question with source excerpt, answer transcript + grade + feedback).
- **Phase 7 — Target-language flag.**
- **Phase 8 — Demo polish, friction log final, video script, Devpost draft.**
- **Phase 9 — Submit (dry-run inspection, then execute).**

## Known pitfalls (verified; do not repeat)
**Bee CLI / proxy**
- Package `@beeai/cli` installs binary `bee`, not `beeai`.
- Always `bee proxy --port 8787` on Windows.
- Proxy (Fastify) rejects `Content-Type: application/json`. Send exactly `application/json; charset=utf-8` on every POST/PUT.
- Windows: `terminate()` on a bee subprocess orphans `bee.exe`. Tree-kill FIRST with `taskkill /PID <pid> /T /F` (or psutil children-then-parent), in try/finally. Check `tasklist | grep -i bee` before starting any long-running process.
- `bee stream` is SSE; consume line-by-line, never blocking reads.

**Stream events**
- `todo-updated` is NOT emitted on `bee stream` when a todo is completed (0 events in ~17 min, stream confirmed alive). Completion IS visible via REST (`completed: true`). If completion is ever needed, poll REST.
- `journal-*` events never fire; `bee journals` is empty for this account. Do not use journals.

**Capture behaviour**
- Isolated short utterances into silence are NOT captured (0/8). Continuous speech of 45–60s right after the prompt IS captured (3/3, 10–28s latency). Explain-back answers must be continuous speech.
- Conversation `state` can stay `CAPTURING` after the transcript is complete. Never gate ingestion on state.
- Bee may append unrelated later speech to the same conversation (a Hindi session 50+ min later joined an earlier one). Never use conversation boundaries as session boundaries. Match answers by utterance `spoken_at` within a window after the alarm plus fuzzy trigger-phrase match.
- STT mangles short odd phrases. Fuzzy-match "Recall answer" (and plausible mis-transcriptions).
- Lecture capture quality is good: ~12 min → 1,048 words, no gap >60s, transcript available ~2 min after the session ends.

**Delivery**
- The Watch has NO todo UI. It can only show the alarm notification.
- Delivery lands 15–60s after `alarm_at`, sometimes iPhone-only, sometimes missed. iOS routes to the iPhone when it is unlocked. Never assume a Watch haptic. Design copy says "Bee prompt", not "wrist buzz".
- Occasional duplicate notification observed. Log if it recurs.

**Schema decisions (advisor, 2026-09-24)**
- Trigger phrase is OPTIONAL (2026-09-25). Operator forgot it 3/10 times in the gate. Matching is by time window after the dispatch's delivery; the trigger only raises confidence. Grader must return an explicit "not_an_answer" verdict for off-topic speech in the window (scored as a miss). Answer window closes 35s after the last utterance in the window (max observed tail latency 25.2s).
- One graded Answer per Dispatch (`Answer.dispatch_id` unique). `raw_text` = concatenated utterances in the answer window; `utterance_ids` = JSON list for evidence. Grade once when the window closes.
- `Session` table derived from `spoken_at` gaps > 10 min. Questions reference `session_id` + `source_start`/`source_end`. `conversation_id` is a coarse pointer only.

**Privacy (hard rule, revised 2026-09-25)**
- Standard is anonymity/unidentifiability, not "no raw text at all": a transcript, utterance, or Bee payload may be committed as-is if it cannot be tied to a specific identifiable person (no real names, no other uniquely identifying detail). Already-public third-party content (e.g. a YouTube video's transcribed speech) is fine to keep verbatim -- reproduction/copyright is a different concern from privacy and isn't gated by this rule.
- Anything that DOES name or identify a specific person (e.g. a personal todo naming someone by name) must be redacted before committing, regardless of how minor it seems.
- When in doubt whether content is identifiable, ask rather than guess.
- Gemini free tier may use prompts for product improvement. Document this in README privacy section.

**Other**
- Write surface is only facts and todos.
- `alarm_at` is RFC3339 with timezone offset.
- MCP server reportedly speaks protocol `2024-11-05`.
- GitHub reviewer invites expire in 7 days; prefer a public MIT repo.

## Friction log discipline (mandatory)
Append to `FRICTION_LOG.md` AS THINGS HAPPEN, format `YYYY-MM-DD HH:MM  [component]  observation`. Worth up to 10% of judging.

## Model-agnostic note
`CLAUDE.md` points here. Other agents read this file directly.

## When unsure
Stop. Print numbered questions. Wait.
