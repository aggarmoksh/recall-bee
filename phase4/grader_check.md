# Phase 4A checkpoint -- grading the 5 real Phase 3 short answers offline

Graded against the ORIGINAL 5 questions (text/rubric/source_excerpt reconstructed from `phase3/questions_review.md`, since `recall.db` was recreated twice since Phase 3 and the old `Question.id` references in `private/phase3_short_answer_trials_full.json` now point at unrelated rows -- see FRICTION_LOG.md 2026-09-25 14:22).

`gemini-2.5-flash`'s daily quota (20/day) was fully exhausted before this checkpoint started on 2026-09-25 (consumed by that day's session classification work). Switched to `gemini-3.8-flash` for the first pass; trials 1, 2, 3, 5 succeeded, but trial 4 hit `gemini-3.8-flash`'s own daily quota too. Retried trial 4 on 2026-09-27 (Phase 4B Step 0, quota reset) on `gemini-2.5-flash` -- succeeded. All 5 trials below are now graded (trials 1, 2, 3, 5 via gemini-3.8-flash on 2026-09-25; trial 4 via gemini-2.5-flash on 2026-09-27).

| Trial | Question | First 12 words of answer | Verdict | Points hit | Feedback | I agree (y/n) |
|---|---|---|---|---|---|---|
| 1 | Explain where Rome's original founders, Romulus and Remus, originally came from and why they were not raised in their home city. | "So the question is where the original founder Romulus and Remus originally..." | partial | They were abandoned/river-deleted and raised by a shepherd. | Missed Alba Longa and uncle Amulius overthrowing Numitor. |y |
| 2 | Describe why Romulus and Remus decided to return to Alba Longa and what they did upon their arrival. | "So Romulus and Remus decided to return to Alba Longa when they found..." | partial | They learned they were the lost princes/rightful heirs. | Missed what they did on arrival: overthrowing Amulius and reinstating Numitor. |n |
| 3 | Explain the major demographic problem Romulus faced when founding Rome and how he attempted to solve it. | "So Romulus faced a problem when he built the city of Rome and..." | correct | Rome had zero women.; Romulus hosted a big festival.; The Sabines were invited.; The Sabine women were kidnapped. | Great job! You covered all the key points accurately. | y|
| 4 | How did the kidnapped Sabine women influence the conflict between the Romans and the Sabine men after the kidnapping event? | "So the captured Sabine women have already become mothers and started families with..." | incorrect | (none) | Missed: Sabine men attacked, women intervened between armies, and their reason. | |
| 5 | Explain why the Romans decided to stop having kings and what system they implemented instead. | "So Romans were having lot of wars, so they decided that they need..." | partial | They elected two consuls who could veto each other and switched yearly. | Missed: kings abused power, were assassinated, and consuls switched yearly. | y|

Full answer text and raw grading output: `private/phase4_grader_check_raw.json` (gitignored).
