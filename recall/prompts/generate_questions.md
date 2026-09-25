# Generate spaced-retrieval questions

You are generating spaced-retrieval quiz questions from a transcript of sustained learning speech (a lecture, study session, or family conversation). The wearer will hear each question on their wrist and answer by **speaking an explanation out loud**.

## Rules

1. Every question must be answerable **from the transcript alone**. Do not use outside facts, even if you know them -- if the transcript doesn't say it, don't ask it.
2. Prefer **why / how / explain** questions over one-word or single-fact questions. The answer is a spoken explanation, not a quiz-bowl buzzer -- "why did X happen" beats "what year was X."
3. Skip small talk, logistics (scheduling, "let's meet at 3pm"), and anything personal about people other than the wearer (names, relationships, personal circumstances of others). This transcript may include casual conversation as well as learning content -- only generate questions from the substantive, learnable material.
4. Skip garbled, incoherent, or low-confidence transcript fragments. Speech-to-text makes mistakes -- if a passage doesn't read as a coherent, plausible statement (garbled words, nonsensical claims, sentence fragments that don't parse), do not build a question on it, even if it is technically "in the transcript." A quiz question's factual content must be something a careful listener would actually recognize as sensible, not an STT artifact.
5. Each question must be answerable in **15-20 seconds of speech** -- not a multi-part essay question.
6. Output language: use the transcript's dominant language, unless a target language is specified below.
7. Never generate a question touching any topic or phrase on the blocklist below.

## Blocklist (manual override -- never quiz on these topics/phrases)

{{BLOCKLIST}}

## Target language

{{TARGET_LANGUAGE}}

## Output

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
