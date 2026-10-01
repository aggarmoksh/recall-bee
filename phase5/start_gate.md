# Phase 5B Step 2 -- Spoken-start gate

Operator-present live test, 10 trials, run via a chat-coordinated live stream
tap (`recall.bee.stream.stream_events()`) rather than the standalone
`phase5/start_probe.py` script's own `input()`-based flow -- this
environment doesn't give the agent a shared interactive terminal with the
operator, so the operator spoke each trial and confirmed completion in
chat while a background stream tap watched for the matching utterance in
real time. `start_probe.py` remains in the repo as the intended
standalone implementation (documented, not executed as designed this run).

Detection: fuzzy match on "recall" AND "session" (separately, any word)
across the utterance(s) captured in the ~90s window after the operator
finished speaking. Phone locked, Bee app closed for trials 2-10 (trial 1
had the Bee app open in the background).

Note on latency: because trials were coordinated via chat messages rather
than a precise programmatic Enter-keypress, per-trial detection latency in
seconds was not captured with stopwatch precision this run. Qualitatively,
9 of 10 trials captured near-instantly (utterance events arrived within
the same reply cycle); trial 6/7's capture was noticeably delayed and
initially misattributed in real time (corrected below).

| Trial | Variant | Detected | Captured text | Assistant intercepted | Notes |
|---|---|---|---|---|---|
| 1 | A | y | "Hey B, let's do a recall session." / "I am ready for my questions." | n | Phone locked, Bee app open in background |
| 2 | A | y | "Hey B, let's do a recall session." / "I am ready for my questions." | n | App "capturing" persisted after mic switched off -- live confirmation of the known `state` pitfall |
| 3 | A | y | "Hey B, let's do a recall session." / "I am ready for my questions." | n | Operator manually tapped "process" on the capturing session; same `state` persistence observed |
| 4 | A | y | "Hey B, let's do a recall session." / "I am ready for my questions." | n | |
| 5 | A | y | "Hey B, let's do a recall session." / "I am ready for my questions." | n | |
| 6 | B | **n** | (none -- mic cut out mid-sentence, no capturing session registered in app at all) | n | Genuine capture failure, not a wording/STT issue |
| 7 | B | y | "Okay, recall." / "Let's do a session." | n | Delayed arrival (initially misattributed to trial 6 in real time, corrected after operator clarification); missing "I'm ready for my questions" -- clipped |
| 8 | B | y | "Okay, recall, let's do a session." / "I am ready for my questions." / "Thanks." | n | Full, clean capture |
| 9 | B | **n** | "Okay, let's do a session." / "I'm ready for my questions." / "Thank you." | n | Real speech captured, but STT dropped the word "Recall" entirely -- fails the word-match despite genuine capture |
| 10 | B | y | "Okay, recall, let's do a session." / "I'm ready for my questions." | n | Full, clean capture |

Variant A: 5/5 detected
Variant B: 3/5 detected

Overall: 8/10 detected

Assistant interception: 0/10 -- Bee's own on-phone assistant never responded to either variant across all 10 trials.

**Criterion: >=8/10 overall, OR >=4/5 for one variant (that variant gets adopted).**
Both are met here: 8/10 overall exactly hits the bar, and variant A alone is 5/5 with zero anomalies (vs. variant B's 3/5 with one outright miss and one STT word-drop). Variant A ("Hey Bee, let's do a Recall session...") is the stronger candidate on this data. Reporting for the advisor's call, not deciding.

## Secondary observations (not part of the pass/fail criterion)

- Zero assistant interception across all 10 trials, either variant -- Bee's own assistant never mistook the start phrase for a command directed at it. This removes one plausible UX conflict for the "Hey Bee..." variant.
- The Bee app's "capturing" status visibly persisted after the mic was manually switched off (trials 2-3) -- a live, user-visible confirmation of the already-documented `state` pitfall (previously only confirmed via the backend API).
- Trial 6 was a genuine capture failure (mic self-muted mid-utterance, no session registered at all) -- a different failure mode than the already-documented "short isolated utterance" gap, and worth a dedicated FRICTION_LOG entry.
- Trial 9's STT dropped the trigger word "Recall" entirely while transcribing the rest of the sentence correctly -- a wording-robustness gap specific to variant B's phrasing ("Okay Recall, let's do a session" vs. variant A's "Hey Bee, let's do a Recall session"), not a capture failure.
