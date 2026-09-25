# Phase 2 Step 2 -- explain-back gate results (redacted)

Full text lives in private/phase2_trials_full.json (gitignored). This file has word counts and first 8 words only, per the redaction rule.

| Trial | Captured y/n | # utterances | Trigger found y/n | First arrival latency (s) | Last arrival latency (s) | Words captured | First 8 words | Usable to grade (operator fills in) |
|---|---|---|---|---|---|---|---|---|
| 1 | y | 4 | n | -15.6 | -15.6 | 52 | Many people think that Rome was founded in... | y |
| 2 | y | 6 | y | -12.1 | 23.4 | 50 | So the question is who was the king... | y |
| 3 | y | 8 | y | 4.0 | 4.0 | 76 | I did not receive buzz for this answer... | y |
| 4 | y | 6 | n | 9.5 | 9.5 | 64 | So the question is who found and raised... | y |
| 5 | y | 4 | y | -0.5 | -0.5 | 50 | So the question is who did Romulus and... | y |
| 6 | y | 6 | y | 9.0 | 9.0 | 88 | So the question is what major problem did... | y |
| 7 | y | 8 | y | 7.3 | 7.3 | 84 | So the question is which neighboring people did... | y |
| 8 | y | 4 | n | 1.7 | 2.2 | 70 | So the question is who was the second... | y |
| 9 | y | 8 | y | 4.0 | 4.0 | 94 | So the question is what major piece of... | y |
| 10 | y | 8 | y | 2.7 | 25.2 | 108 | So the question is what system of government... | y |

Note: "Usable to grade" is the operator's general "i think so" affirmative across all 10, not an individually-reviewed per-trial judgment.

## Diagnosis: trials 1, 4, 8 (trigger not found)

Not a capture or matcher failure -- confirmed with the operator that they mostly forgot to say "Recall answer" at the start of those three answers (launched straight into the answer, or restated the question instead). The matcher correctly reported `trigger_found: False` because the phrase genuinely wasn't spoken.

## Trial 3 note

Operator was using the phone during trial 3, so no Watch notification arrived (known AGENT.md pitfall: iOS routes to the phone when unlocked/in use) -- they answered anyway based on the known schedule. Not a new finding.

## Pass criterion evaluation

**Criterion (advisor-set, not reinterpreted): at least 8 of 10 trials captured AND trigger found AND marked usable.**

Trials meeting all three: 2, 3, 5, 6, 7, 9, 10 = **7 of 10**. Trials 1, 4, 8 fail only on trigger-found (forgotten trigger phrase, not a technical issue).

**Result: 7/10 -- fails the strict numeric bar (need >=8).** Capture itself was 10/10 with real, correctly-responsive answer text and fast arrival latencies -- the shortfall is entirely attributable to the operator forgetting the trigger phrase on 3 of 10 trials, not any platform or code limitation. Flagging both numbers (10/10 capture, 7/10 full criterion) for the advisor rather than rounding one direction or the other.

## Answer-window recommendation

Last-arrival latencies across all 10 trials (relative to speech_end): -15.6, 23.4, 4.0, 9.5, -0.5, 9.0, 7.3, 2.2, 4.0, 25.2 seconds. Max positive latency observed: **25.2s**. Recommend an answer window of **at least 30s after the detected/estimated speech end** (or, in production terms with no Enter-press ground truth, roughly 30-45s past the last utterance matching the question's answer window) to reliably catch the final utterance of a multi-utterance explain-back answer.

