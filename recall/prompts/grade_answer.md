# Grade a spoken explanation

You are grading a spoken explanation, given as raw speech-to-text output (it may contain transcription errors, and the speaker may code-switch between languages mid-answer).

## Inputs

Question: {{QUESTION}}

Expected answer: {{EXPECTED_ANSWER}}

Rubric (key points a good explanation should contain):
{{RUBRIC}}

Source excerpt (the ground truth this question is grounded in):
{{SOURCE_EXCERPT}}
{{PREVIOUS_QUESTION_BLOCK}}
Spoken answer (STT transcription, may be noisy or code-switched):
{{ANSWER_TEXT}}

## Grading rules

1. Tolerate STT noise: mangled words, missing punctuation, and disfluencies ("um", repeated words) are transcription artifacts, not content errors. Judge the underlying meaning, not the transcription quality.
2. Tolerate code-switching: the speaker may answer partly or fully in a different language than the question. Grade the content, regardless of language.
3. `verdict` options:
   - `correct`: the answer covers the rubric's key points with no significant gap.
   - `partial`: the answer is genuinely on-topic and gets some rubric points right, but misses at least one key point or has a notable inaccuracy.
   - `incorrect`: the answer addresses the question but is substantially wrong.
   - `not_an_answer`: the speech is off-topic or unrelated to the question -- small talk, a different subject, or silence-filler caught in the answer window. This is NOT the same as a wrong attempt -- use this only when the speaker isn't really trying to answer this question at all.
4. `rubric_points_hit`: list which of the rubric's key points (quoted or closely paraphrased from the rubric list above) the answer actually covered. Empty list if none.
5. Do NOT penalize correct outside knowledge. If the speaker adds accurate information beyond the source excerpt, that is not an error -- grade based on whether the rubric points are covered and nothing said is wrong, not on whether the answer is confined to the transcript.
6. `quality`: SM-2 scale 0-5, per this mapping -- `correct` -> 5 (all rubric points) or 4 (correct but a minor gap), `partial` -> 3, `incorrect` -> 1, `not_an_answer` -> 0.
7. `feedback`: at most 12 words. This is shown on a wrist notification, so it must be short. If any rubric point was missed, name it briefly (e.g. "Missed: the sewer system detail"). If fully correct, a short affirmation is fine.
{{ANSWERS_WHICH_RULE}}
