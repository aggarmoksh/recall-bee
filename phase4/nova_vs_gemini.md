# Nova (Bedrock) vs Gemini -- head-to-head check

Per AGENT.md: "Nova must match Gemini on the grader check and question review before it becomes default." Both generated from the same lecture session (5) transcript and graded against the same 5 real Phase 3 Step 5 answers. Default provider stays **gemini** until you confirm from this comparison.

## Question generation (v2 settings: garbled-content rule + "up to N")

Gemini produced **7** questions; Nova produced **8** (did not prune to fewer than N on this transcript). Both classified session 5 as `lecture_study`.

| # | Gemini (phase3/questions_review_v2.md) | Nova | Better / same / worse |
|---|---|---|---|
| 1 | Explain the circumstances under which Amulius became king of Alba Longa, replacing his brother Numitor. | Where did Roman history begin, and why is Alba Longa significant in this context? | |
| 2 | How did Romulus and Remus learn about their true identity, and what action did they take immediately after this discovery? | What was the conflict between King Numitor and his brother Amulius, and what were the consequences of Amulius's actions? | |
| 3 | Describe the event that led to the founding of Rome and the death of Remus. | Who were Rome's original founders, and what were the unusual circumstances surrounding their birth and early life? | |
| 4 | Explain the problem Romulus faced regarding the population of early Rome and how he attempted to solve it. | How did Romulus and Remus discover their true identity, and why was it significant that they were the lost princes of Alba Longa? | |
| 5 | How was the war between the Romans and the Sabines resolved after the abduction of the Sabine women? | What action did Romulus and Remus take to reclaim their heritage, and what was the outcome of their efforts? | |
| 6 | Explain why the Romans decided to abolish the rule of kings and what new system of governance they established instead. | What major problem did Rome face regarding its population, and why was this problem significant? | |
| 7 | According to the transcript, what led Rome to become "ultra, ultra militaristic"? (flagged -- same visual-reference gap as the original Q6) | How did Rome address its gender imbalance, and what was the immediate consequence of this action? | |
| 8 | *(none -- Gemini pruned to 7)* | What was the role of consuls in the Roman Republic, and what potential issue is hinted at with this system? Note: hedges on the ambiguous "it's all burning down" line rather than asserting an unstated cause -- avoids the exact visual-reference overreach pattern Gemini's Q6/Q7 fell into. | |

Note: the two sets don't line up 1:1 by topic (different models chose different groupings/angles over the same material) -- rows are aligned loosely by rough topic order, not a strict pairing. Nova's rubrics are consistently 2 points per question versus Gemini's usual 3-4 (both within the "2-4" spec, but Nova runs to the lean end).

## Grader check (same 5 real answers, same reconstructed original questions)

| Trial | Question | Gemini verdict/quality | Nova verdict/quality | Points hit differ? | Better / same / worse |
|---|---|---|---|---|---|
| 1 | Romulus/Remus origins, why not raised in home city | partial / 3 | partial / 3 | No -- same single point hit (raised by a shepherd) | |
| 2 | Why they returned to Alba Longa, what they did on arrival | partial / 3 | partial / 3 | No -- same single point hit (learned they were princes) | |
| 3 | Demographic problem + solution | **correct / 5** (all 4 points) | **partial / 3** (3 of 4 -- missed "festival," though the answer does say "kidnapped after a festival") | **Yes** | |
| 4 | Sabine women's role in resolving the conflict | **incorrect / 1** (0 points -- "Missed: Sabine men attacked, women intervened, their reason") | **partial / 3** (2 points -- women intervened + stopped the war) | **Yes** | |
| 5 | Why stop having kings, what replaced them | partial / 3 | partial / 3 | No -- same single point hit (elected two consuls) | |

Trials 3 and 4 are the interesting disagreements: Gemini gave full credit on trial 3 where Nova docked a point for "festival" despite the word appearing in the answer (Nova's own feedback text may be slightly inconsistent with its points_hit list here); Gemini gave zero credit on trial 4 where Nova gave partial credit for a paraphrased-but-substantively-correct answer. Neither is obviously "more correct" from the outside -- this is exactly what your judgment column is for.

## Cost/ops notes

- Nova: all 6 calls (1 generation + 5 grading) succeeded on the first attempt, no retries, no quota errors. AWS billing is pay-per-token against your $150 credit, not a hard daily request cap.
- Gemini: required switching between two model names and multiple retry rounds across two days to complete the same 5 grading calls, due to the free-tier 20/day cap per model.
- Bedrock's Nova 2 Lite has no native structured-output mode (confirmed on its model card) -- the adapter uses forced tool-use instead, which worked cleanly.

Full raw data: `private/phase4_nova_questions_raw.json`, `private/phase4_nova_grade_raw.json` (gitignored).
