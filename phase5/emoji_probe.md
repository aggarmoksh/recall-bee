# Phase 5B Step 4 -- Emoji + length check

**MAJOR finding, not the one this step set out to check**: the real
notification text is NOT what Recall posts to `/v1/todos`. Bee rewrites
the todo text into a personalized, conversational notification before
displaying it -- both tests below show the same template ("<name>, sorry
to interrupt—...") regardless of the original copy's structure.

| Text posted | Watch | iPhone | Actual notification shown |
|---|---|---|---|
| "🐝 6 questions are buzzing for you" | no emoji; full rewrite | no emoji; full rewrite | "Lord Moksh, sorry to interrupt—your questions are ready and buzzing for you whenever you'd like to dive in." |
| "Recall: 4/5 ✓ · 🔥 3-day streak" | no emoji; full rewrite | no emoji; full rewrite | "Lord Moksh, sorry to interrupt—your Recall session is ready! You're on a 3-day streak with 4 out of 5 done. Let's keep that fire going!" |

Notification arrived on both Watch and iPhone for both trials this time
(contrast with earlier in this project's trial where Watch delivery was
inconsistent) -- small positive data point, though sample size is 2.

## What's confirmed

- Emoji never render on either device -- not stripped silently, the
  whole message is rewritten into prose instead (🔥 -> "fire", ✓ ->
  "done", 🐝 -> dropped entirely with no equivalent).
- The rewriting is template-consistent across both trials, not random: the
  same "<Bee display name>, sorry to interrupt—" opener, conversational
  tone, exclamation-point energy.
- Factual content (numbers: streak count, fraction) survives the rewrite
  accurately -- this is not a data-corruption risk, just a formatting one.
- Both trials personalized with the operator's own Bee display name
  ("Lord Moksh"), inserted automatically -- Recall never sent a name in
  either todo text.

## Not tested this round (flagging for later)

- Whether a plain-sentence todo with no emoji/symbols at all still gets
  rewritten, or passes through verbatim -- would isolate whether this
  triggers on every todo or specifically on non-standard characters.
- Whether the rewriting differs between Watch display and iPhone display
  (both showed the same wording this round, but only eyeballed, not
  diffed character-for-character).

## Implication for v4 copy design

Any notification copy (stings, feedback, session report) should be
written assuming Bee will rephrase it into a sentence before the wearer
ever sees it -- compact symbolic formats (fractions, checkmarks, emoji
shorthand) will not reach the wrist/phone as written, even though the
*meaning* (numbers, intent) does survive. Reported for the advisor's call
on whether/how to adapt the v4 copy style around this.
