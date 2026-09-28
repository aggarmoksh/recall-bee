# Generate spaced-retrieval questions

You are generating spaced-retrieval quiz questions from a transcript of sustained learning speech (a lecture, study session, or family conversation). The wearer will hear each question on their wrist and answer by **speaking an explanation out loud**.

## Step 1: classify the session

Before generating questions, classify the whole transcript into exactly one `session_type`, with a one-line `session_type_reason`:

- `lecture_study`: a structured lecture, talk, or deliberate study session -- one speaker (or a clear teacher/student dynamic) presenting organized factual/conceptual material.
- `substantive_discussion`: real, informative conversation between people -- not structured as a lecture, but containing genuine technical, professional, or factual content worth learning from (e.g. planning a project, explaining how something works, career advice).
- `casual`: small talk, chit-chat, logistics, or low-information conversation with little or no substantive content -- even if some sentences are individually coherent.
- `personal`: content that is primarily personal to people other than the wearer (their relationships, circumstances, feelings), regardless of how substantive it sounds.

This classification also gates what happens to the questions you generate: `lecture_study` sessions get their questions auto-approved for dispatch, `substantive_discussion` sessions get questions held for the operator's manual approval, and `casual`/`personal` sessions get no questions at all -- so classify honestly even if it means producing zero questions.

## Step 2: generate questions (skip this step entirely if session_type is casual or personal -- return an empty questions list)

## Rules

1. Every question must be answerable **from the transcript alone**. Do not use outside facts, even if you know them -- if the transcript doesn't say it, don't ask it.
2. Prefer **why / how / explain** questions over one-word or single-fact questions. The answer is a spoken explanation, not a quiz-bowl buzzer -- "why did X happen" beats "what year was X."
3. Skip small talk, logistics (scheduling, "let's meet at 3pm"), and anything personal about people other than the wearer (names, relationships, personal circumstances of others). This transcript may include casual conversation as well as learning content -- only generate questions from the substantive, learnable material.
4. Skip garbled, incoherent, or low-confidence transcript fragments. Speech-to-text makes mistakes -- if a passage doesn't read as a coherent, plausible statement (garbled words, nonsensical claims, sentence fragments that don't parse), do not build a question on it, even if it is technically "in the transcript." A quiz question's factual content must be something a careful listener would actually recognize as sensible, not an STT artifact.
5. Each question must be answerable in **15-20 seconds of speech** -- not a multi-part essay question.
6. Skip questions that depend on a visual reference the transcript doesn't actually describe in words -- on-screen maps, charts, diagrams; phrases like "as you can see," "shown here," "this graph"; or a deictic "this/here/that" pointing at something never spoken aloud. Bee's capture is audio-only, so if the real answer lives in something the speaker gestured at or displayed rather than said, the transcript is missing the fact entirely -- don't build a question that assumes it's there. (This is the same failure pattern behind two retired Roman-history questions that referenced "what's happening right here in front of us" without the transcript ever stating what that was.)
7. Output language: use the transcript's dominant language, unless a target language is specified below.
8. Never generate a question touching any topic or phrase on the blocklist below.
9. `text` must be at most 100 characters, including punctuation -- it is delivered as a Bee todo notification. Write it tight; don't rely on the shortening pass to fix an overlong question.

## Blocklist (manual override -- never quiz on these topics/phrases)

{{BLOCKLIST}}

## Target language

{{TARGET_LANGUAGE}}

## Output

Produce `session_type` and `session_type_reason` (one line) as described above.

For each question, produce:
- `text`: the question itself
- `expected_answer`: a concise correct answer
- `rubric`: 2-4 key points a good spoken explanation must contain to be graded correct
- `source_excerpt`: the exact transcript text (quoted, verbatim) this question is grounded in
- `topic`: a short topic label
- `language`: the language the question and expected answer are written in

Generate up to {{N}} questions -- fewer than {{N}} (including zero) is correct and expected if the transcript doesn't contain that many distinct, substantive, ungarbled things worth quizzing on. Do not pad the count by lowering your standards on rules 1-4.

## Transcript

{{TRANSCRIPT}}
