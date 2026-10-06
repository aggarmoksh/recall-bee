# Recall — Agent Brief (persistent, read every session)

You are the coding agent for **Recall**, a spaced-retrieval tutor built on Bee for the "Build, Ship, Shape: Amazon Developer Hackathon 2026" (Bee track, deadline Oct 23 2026 12:00pm PDT).

Your operator is a solo builder on Windows 10/11, git bash, VS Code. He runs a two-surface workflow: an advisor chat handles design and decisions, you handle code and commands. **You do not make design decisions.** When you hit one, stop and ask.

## Pitch — Recall v3 (locked 2026-09-24, do not drift)
Bee captures sustained learning speech (lectures, study sessions, family conversations in any language). An agent turns those transcripts into spaced-retrieval questions and delivers them as Bee todo alarms (Watch or iPhone, whichever Bee routes to). The wearer answers by **explaining the answer out loud in 15–20 seconds** (locked after 5/5 capture in Phase 3). Saying "Recall answer" first is optional. After grading, a feedback todo lands on the wrist ("Recall ✓ 3/4 — missed: X"). Bee's ambient capture transcribes it. Recall matches that speech to the dispatched question, an LLM grades the explanation against the source transcript, and SM-2 reschedules. A target-language flag lets the same loop serve students and bilingual families.

Fallback grading path (only if the Phase 2 explain-back gate fails): self-grade by ticking the todo on iPhone, detected by polling REST for `completed: true`.

## v4 delivery model (locked 2026-09-30, supersedes timed push dispatch)
Trial data: 6 timed pushes → 2 answered, 4 missed (67% ignored). Push-at-random-times fails real life (driving, naps, library). Spaced repetition only needs day-level timing, so Recall is now **pull with nudges**:
1. **Bee stings (nudges):** max 3/day inside the user's active window (`ACTIVE_WINDOW`, default 18:00–00:00; operator 13:00–03:00), spread start / middle / end−90min, plus one right after a lecture_study session closes. Held while Bee heard speech in the last 3 min (busy). Silence never means "free", so a sting is only a nudge, never a question. Stings stop once the daily goal is met. Playful copy ("🐝 6 questions are buzzing for you").
2. **Start a session:** (a) tick the persistent "Recall: tick when you can talk" todo on iPhone (detected by REST polling `completed: true`), or (b) a spoken start phrase, ONLY if it passes the Phase 5B gate (≥ 8/10).
3. **Mini-session:** back-to-back questions until the daily quota (6) is done or the user goes quiet: question todo → spoken answer → grade → feedback todo → next question. One no_answer ends the session, no penalty; the remaining questions stay due.
4. **Session report** todo at the end ("Recall: 4/5 ✓ · 🔥 3-day streak"). **Streak** = daily goal met (all due questions, up to 6). The day resets at 04:00 local.
5. Misses never touch SM-2. Timed random dispatch is removed.
Kill criteria: over 2 days, ≥ 1 session/day and ≥ 80% of in-session questions answered.
Not building: XP, badges, leaderboards, levels, unlimited nudges. Learned active window from Bee data → POSTLAUNCH.

## Stack (LOCKED. Do not substitute without asking.)
- **Runtime:** Windows 10/11, git bash, Python 3.12, Node 20+
- **Backend:** FastAPI + Uvicorn
- **Bee integration:** `@beeai/cli` (binary `bee`). `bee proxy --port 8787` for REST, `bee stream` (SSE) for realtime events. All Bee access goes through ONE module (`recall/bee/`) so pitfalls are handled in one place.
- **Storage:** SQLite via `sqlmodel`. File `recall.db` at repo root, gitignored.
- **Spaced repetition:** SM-2, hand-implemented.
- **LLM:** behind a small provider interface (`recall/llm/`) with one method per job (generate_questions, grade_answer). Default provider: **Google Gemini** (`google-genai` SDK, current Flash model, key `GEMINI_API_KEY` in `.env`). Anthropic adapter optional later. Swapping must be a config change plus one adapter file, never a rewrite. Structured JSON output only.
- **AWS:** boto3 for Bedrock. Credentials via `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` in `.env` (IAM user `recall-dev`, never root). Never log or commit keys.
- **Frontend:** Next.js 16 (App Router, revised 2026-09-28 -- `create-next-app@latest` now installs 16.3.6; App Router APIs used here are unchanged from 15) + TypeScript + Tailwind v4. No component library.
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
- **Phase 4 (merged 4+5) — Full loop daemon:** session classification + auto/pending approval, dispatch policy, grading, feedback todo, todo cleanup, `python -m recall.run`. Mid-phase checkpoint after grading unit tests.
- **Phase 5B — v4 gates:** spoken-start test (10 trials), tick-latency test, emoji-on-Watch check, session 15/16 loop diagnosis. Test + diagnose only.
- **Phase 5C — v4 build:** stings, ready todo, mini-sessions, report + streak, no-penalty misses (+ recompute past no_answers), the 5 dashboard fixes, and a fix for the session loop.
- **Phase 5 (old) — 2-day trial (superseded by the v4 kill criteria above).** Day 1 = `RECALL_PROFILE=dense` (15-min gap, max 25/day, first review 30 min after session): target >= 20 graded answers; kill if capture < 80% or grader agreement < 80%. Day 2 = `normal` profile in the background while Phase 6 is built: measures usability (kill if > 50% prompts ignored) and day-1→day-2 retention. After that the daemon keeps running on normal until the video, to build a multi-week retention curve.
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
- Trigger phrase is OPTIONAL (2026-09-25). Operator forgot it 3/10 times in the gate. Matching is by time window after the dispatch's delivery; the trigger only raises confidence. Grader must return an explicit "not_an_answer" verdict for off-topic speech in the window (scored as a miss). Answer window closes 60s after the last utterance in the window (revised 2026-09-28: a real live-test answer had a 37s mid-answer thinking-pause that tripped the old 35s threshold and truncated the answer before its substantive content); hard cap unchanged at alarm_at + 5 min.
- One graded Answer per Dispatch (`Answer.dispatch_id` unique). `raw_text` = concatenated utterances in the answer window; `utterance_ids` = JSON list for evidence. Grade once when the window closes.
- `Session` table derived from `spoken_at` gaps > 10 min. Questions reference `session_id` + `source_start`/`source_end`. `conversation_id` is a coarse pointer only.

**Loop policy (advisor, 2026-09-25)**
- Session types from generator: `lecture_study` (auto-approve questions), `substantive_discussion` (questions pending; operator approves via CLI), `casual`/`personal` (no questions).
- Dispatch profiles via `RECALL_PROFILE` in `.env`: `normal` = max 6/day, 45-min gap, first review ~2h after session end; `dense` = max 25/day, 15-min gap, first review 30 min after session end. Both: quiet hours 22:00–08:00 local, at most ONE open dispatch at a time.
- Scheduling: first review per profile, second next day, then SM-2.
- Product stance (from Becca Farsace's Bee review, Feb 2025): Bee struggles with speaker separation and noisy rooms, and bystander consent is its #1 criticism. Recall never penalizes capture errors, only quizzes on learning sessions, and never generates questions about what other people said about themselves.
- Grade verdicts: correct→5 (all rubric points) / 4 (minor gap), partial→3, incorrect→1, not_an_answer→0. **no_answer does NOT update SM-2** (2026-09-30): a missed notification is not forgetting. Re-queue at the next eligible slot, max 2 retries/day; counted only as "ignored" in usability stats.
- Late answers: SUPERSEDED by the v4 pull model (never built). Answers match only inside the in-session window: question delivery → 60s tail-close after the last utterance, hard cap 5 min.
- Spoken start (Phase 5B, 2026-10-01): variant A only, "Hey Bee, let's do a Recall session, I'm ready for my questions." 5/5 detected, 0 assistant interceptions. Fuzzy match on "recall" + "session". Start-phrase utterances are excluded from question generation like answers.
- Bee REWRITES notification text into its own conversational voice ("<Name>, sorry to interrupt..."). Numbers and facts survive; emoji and symbols do not. All Recall copy is plain words, no emoji, no ✓/✗. The meaning of questions has held so far, but Bee sometimes appends a hint; track this per answer (kill: > 30% hinted → revisit).
- Transcript source (Phase 5D, 2026-10-05): while a conversation is CAPTURING, read ONLY the `realtime: true` transcription (`conversation.transcriptions[] → realtime == true → utterances[]`, sorted by spoken_at), polled every 5s; it appears ~25–39s after speech. Once it's processed, read the non-realtime transcription. Never concatenate transcriptions. Dedupe by normalized text + spoken_at ±2s. Fetch immediately on an `update-conversation` event with state processed. Bee auto-processes ~1.5–2 min after the last utterance.
- Answer-done rule (revised 2026-10-05 after a cross-question bleed in the live test): an answer is done only when BOTH no new utterance has ARRIVED for 15s AND now − last SPOKEN_AT ≥ 40s (covers the 25–39s realtime-transcript lag). Never dispatch the next question until the previous one is graded or pending. The grader receives the current AND previous question; if the answer fits the previous one better, reassign it to that dispatch and grade it there.
- Report wording: "correct"/"solid" means quality ≥ 4 only. Report = "Recall: N answered, X solid, Y need another look, Z-day streak". The streak counts answered questions (habit); quality is reported separately.
- Hint prefix: always use the short prefix "Recall quiz, no hints: "; if prefix + question > 120 chars, send without a prefix. New questions are capped at 95 chars.
- Session pacing (original rule, superseded by the answer-done rule above): an answer is done after 15s with no new utterances following speech after alarm_at → grade → feedback → next question 10s later. No speech by alarm_at + 2 min → move on and mark the question pending; pending questions are graded later from the final transcript, matched by spoken_at in [this alarm_at, next alarm_at). Two consecutive pending → pause the session (no penalty).
- Question todo text uses the prefix "Recall quiz question, do not include the answer or any hints: ". Effect unproven; the hint rate is still tracked.
- PUT /v1/todos/:id with a new alarm_at stores the alarm but does NOT fire a notification. Re-notify only by delete + recreate.
- Laptop sleep kills the daemon and the stream. On wake, the daemon must reconnect the stream and recover any in-flight session/dispatch state without manual cleanup.
- After grading: delete the question todo, post a feedback todo with alarm_at=now. Delete Recall feedback todos older than 12h. Only ever delete todos Recall created (tracked by bee_todo_id); never touch the user's own todos.
- LLM (revised 2026-09-28): **Amazon Bedrock, Claude Haiku 4.5** (Converse API; use the correct cross-region inference profile ID from Bedrock docs, don't guess). Fallback: **Amazon Nova 2 Lite** via one config change if AWS credits don't cover Anthropic usage. Gemini adapter kept only as a dev fallback. Rationale: cost is negligible either way (~$15–20 total on Haiku); grading noisy, code-switched speech against a rubric with strict JSON is where model quality matters. This also enters the AWS Builder mini. Hard in-code cap: 80 LLM calls/day; temperature 0 for classification and grading.
- Trial material: AI/ML interview-prep content (talk-heavy lectures/podcasts). The Roman-history lecture sessions are retired via excluded_sessions.txt so trial data starts fresh.
- NO SELF-FEEDING (2026-09-26): utterances inside any answer window (matched to an Answer or within a Dispatch window) are NEVER used for question generation. Test-era sessions of spoken answers go in `recall/overrides/excluded_sessions.txt`.
- Generator stays transcript-only. Skip questions that depend on visual references (on-screen maps, charts, "as you can see", deictic "this/here" without spoken content). Operator can `--reject-question <id>`. Grader does NOT penalize correct outside knowledge.
- Session classification runs once, is persisted, and is never re-run automatically. Operator override via CLI.
- incomplete_capture: if the answer window has < 12 words and the verdict is not correct, verdict = incomplete_capture, no SM-2 update, re-dispatch in ~2h, feedback "Only caught a few words, will ask again".

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
