# Phase 5D Step 3 — Hint-prefix test

6 todos created (2 per question x 3 questions), alarm_at 1 min apart within
a pair, pairs 2 min apart; all 6 deleted afterward (confirmed via
`DELETE /v1/todos/{id}` -> `{"success": true}` on all 6).

**Incomplete data**: the operator captured only 4 of the 6 notifications
(screenshots for q37 and q38 only cover one variant each, not both A and
B). The two missing notifications (q37's second, q38's second) were not
screenshotted before being dismissed and cannot be reconstructed --
reporting what was actually captured rather than guessing the rest.

For q36, both variants were captured but the screenshots carry no
timestamp finer than "now", so A ("Recall: <question>") vs. B ("Recall
quiz question, do not include the answer or any hints: <question>")
cannot be reliably told apart after the fact purely from the notification
text -- both read as plain question restatements. Reported as "variant 1"
/ "variant 2" below rather than asserting an A/B label neither screenshot
can actually prove.

## Results

| Question | Variant | Exact notification text | Hint/answer present? | Instruction text leaked/looked odd? |
|---|---|---|---|---|
| q36 (light/molecule bonds) | 1 | "Hey Lord Moksh, sorry to interrupt your session! Quick reminder—it's time for your recall question: How does light reveal a molecule's structure and identify its bonds? Ready when you are!" | No | No |
| q36 (light/molecule bonds) | 2 | "Hey Lord Moksh, sorry to jump in! It's time for your recall session. Here's your question: How does light reveal a molecule's structure and identify its bonds?" | No | No |
| q37 (chocolate polymorphs) | captured (A or B, unknown which) | "Lord Moksh, sorry to interrupt—it's time for your recall session. Here's your question: using chocolate as an example, can you explain what polymorphs are and how they affect its properties?" | No | No |
| q38 (Ritonavir Form II) | captured (A or B, unknown which) | "Lord Moksh, sorry to interrupt your session! Quick recall question for you: why was discovering Ritonavir Form II problematic for Abbott even though it was chemically identical to Form I?" | No, but see note | No |

**Note on q38**: the rewrite appended "to Form I" at the end, a detail not
explicit in the original question text ("...despite being chemically
identical?" doesn't name what it's identical *to* in the same clause).
Not an answer or a hint toward answering the question, but a small
borderline case of Bee adding clarifying context beyond the literal input
-- worth being aware of even though it doesn't cross into "giving away the
answer."

## Findings

- **Zero hints/answers appeared in any of the 4 captured notifications**,
  regardless of which prefix wording produced them. This doesn't confirm
  the "do not include hints" prefix is what's suppressing hints (we have
  no hinted baseline to compare against from this run) -- it's consistent
  with AGENT.md's existing note that hinting is occasional ("track this
  per answer"), not constant, so a 4-sample check with no hints either way
  is inconclusive on the prefix's effect specifically.
- **The instruction text itself never leaked into any rewritten
  notification** -- "do not include the answer or any hints" was fully
  absorbed/discarded by Bee's rewriting in every captured case, never
  echoed back oddly or verbatim. This is a mildly positive sign for using
  an instructional prefix if the advisor wants to pursue it further: at
  minimum it doesn't visibly corrupt the output.
- **Recommendation**: this test needs a rerun with complete capture (all
  6 screenshotted, in firing order, ideally with the operator naming which
  one is A vs B as it arrives) before drawing a real conclusion -- this
  round's result is "no hints seen, inconclusive," not "prefix confirmed
  to prevent hints."
