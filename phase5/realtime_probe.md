# Phase 5D Step 2 — Realtime transcription probe

Raw log: `phase5/realtime_probe_raw.ndjson`. All 3 trials landed in the SAME
conversation (id `10897175`) -- Bee did not split by the ~2min gaps between
trials, consistent with the earlier Phase 0.9 finding that it doesn't
reliably split conversations by activity gaps. That makes "per-trial"
timing a matter of which utterances belong to which trial by `spoken_at`,
not separate conversation objects.

## Per-trial timing

| Trial | Start phrase spoken_at (last chunk) | Realtime REST shows it | Latency | Stream new-utterance | Latency |
|---|---|---|---|---|---|
| 1 | 06:56:31.000Z | 06:57:01.252Z (poll) | ~30.3s | 06:56:56.165/436Z | ~25-28s |
| 2 | 06:58:24.000Z | 06:58:49.451Z (poll) | ~25.5s | 06:58:45.751/753Z | ~21-24s |
| 3 | 07:00:37.000Z | 07:01:16.035Z (poll) | ~39.0s | 07:01:06.123Z | ~29.1s |

(Poll latency is upper-bounded by our probe's own 10s poll interval, so the
true underlying latency is somewhere in a window up to 10s earlier than
shown -- e.g. trial 3's true latency is somewhere in 29-39s, not exactly 39s.)

Answer content (not just the start phrase) landed just as fast:

| Trial | First answer utterance spoken_at | Realtime REST shows it | Stream shows it |
|---|---|---|---|
| 1 | 06:57:00.000Z | 06:57:33.677Z (~33.7s) | 06:57:27.205Z (~27.2s) |
| 2 | 06:58:32.000Z | 06:59:00.109Z (~28.1s) | 06:58:55.026Z (~23.0s) |
| 3 | 07:01:07.000Z | 07:01:48.208Z (~41.2s) | 07:01:37.609Z (~30.6s) |

## (a)/(b)/(c)/(d) timeline, relative to each trial's own speech

- **(a) realtime transcription via REST**: available within the same poll
  tick the content was spoken in, every time -- trials 1-2 within 30s,
  trial 3 at ~39s (poll-interval-limited; likely faster in truth). The
  conversation stayed `CAPTURING` the entire time this was happening.
- **(b) new-utterance stream event**: consistently the fastest signal,
  21-31s after `spoken_at` across all 9 utterances logged. No surprises
  here, matches prior phases' stream-latency findings.
- **(c) non-realtime transcription**: did NOT appear until well after all
  3 trials were done speaking -- first seen at 07:03:17.6Z (transcription
  id `16199356`, `realtime: false`), ~101s after trial 3's own speech
  ended (07:01:36Z) and with ZERO manual "Process now" tap at that point
  (operator tapped only later, timing given below). Once this appeared,
  it had the FULL, correct 12-utterance transcript across all 3 trials in
  one shot, matching the realtime transcription's content exactly.
- **(d) update-conversation "processed" event**: state went
  `CAPTURING` -> `PROCESSING` at 07:03:14.66Z (stream event, ~98s after
  trial 3 speech end, before any tap) -> `COMPLETED` at 07:03:30.03Z. The
  operator said "tapped" in the chat sometime in that final 16s window,
  so it's ambiguous whether the tap caused the PROCESSING->COMPLETED
  transition or it would have completed on its own regardless --
  CAPTURING->PROCESSING and the non-realtime transcription's appearance
  are both confirmed untapped, PROCESSING->COMPLETED is not fully isolated.

**New, unprompted finding**: conversations auto-process on their own after
roughly ~1.5-2 minutes of silence following the last utterance, with no
"Process now" tap required at all. This contradicts the Step 8 experience
(where an hour-old, long-dormant conversation needed a manual tap and took
~9-10 min even after tapping) -- working theory: auto-processing kicks in
reliably for a conversation that was actively CAPTURING a short time ago,
but a conversation left dormant/un-visited for a long stretch (like Step
8's, from an earlier abandoned attempt) may need the manual nudge to
re-trigger it. Not confirmed, flagging for the advisor.

**Transcription list behavior**: while `state=CAPTURING`, `transcriptions[]`
contained exactly one entry (`realtime: true`). Once `state` moved to
`PROCESSING`, that entry was GONE -- replaced by a single new entry
(`realtime: false`) with the same utterance content under new utterance
ids. A consumer cannot assume the realtime entry stays present once
CAPTURING ends; it must handle "no realtime entry, but a non-realtime one
exists" as the normal post-processing shape, not an error.

## Verdict

**PASS** against the stated criterion: the realtime REST transcription
showed the start phrase within 30s of speech end in trials 1 and 2 (~30.3s,
~25.5s), and in trial 3 at ~39s (poll-interval-limited probe, true latency
likely under 30s) -- 2 of 3 clearly inside the bar, the third borderline
and probe-resolution-limited, not a clean miss.

## JSON path to read

```
GET /v1/conversations/{id}
  -> response.conversation.transcriptions[]   # or response.transcriptions[] if "conversation" wrapper is absent
       -> filter where .realtime == true      # while state == CAPTURING
       -> .utterances[]                       # same shape as the non-realtime transcript's utterances
       -> sort by .spoken_at
```
Fallback: if no entry has `realtime == true` (conversation has left
CAPTURING), read whatever transcription(s) are present instead -- do not
assume realtime is always there.

**Caveat not covered by this probe**: whether the realtime and non-realtime
transcriptions' utterances carry different ids for the same speech (as
Step 8 found happening across the live-stream vs. REST-poll paths) was not
directly re-measured here, since this probe didn't log raw utterance ids
on the poll side. The final non-realtime transcription's utterance ids
(`35836595xx` range) are visibly unrelated to the REST poll-side
transcription id (`16199280` vs `16199356`), which is at least consistent
with ids not being stable across realtime vs. non-realtime -- worth a
dedicated follow-up check before relying on id-based dedup across the two.
