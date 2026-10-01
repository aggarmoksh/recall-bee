# Phase 5B Step 3 -- Ready-tick latency

Operator-present live test, run chat-coordinated (same reasoning as Step 2
-- no shared interactive terminal with the operator in this environment).
Each todo was polled continuously in the background from creation; the
operator reported the tick method used after the fact rather than via a
precise programmatic keypress, so exact tick-to-detection latency has the
same precision caveat as Step 2 -- bounded by REST poll + chat round-trip,
not stopwatch-accurate.

| Trial | Tick method | Notification received? | Completion detected (UTC) |
|---|---|---|---|
| 1 | app | n (no alarm_at set -- standing todo, none expected) | within <1s of polling starting |
| 2 | app | n (no alarm_at set) | 2026-10-01T20:06:25Z |
| 3 | app | n (no alarm_at set) | 2026-10-01T20:07:15Z |
| 4 (bonus, alarm_at set ~15s out) | notification -> app | y | 2026-10-01T20:09:10Z |

All 4 trials detected well within the 15s criterion (poll interval was
5s; nothing took more than one or two poll cycles). **3/3 on the original
criterion.**

## Finding: trials 1-3 never actually tested notification-tap

The original 3 trials used `create_todo(text)` with no `alarm_at`, matching
the v4 spec's "persistent tick todo" -- but that means no notification ever
fires for it, so the operator could only tick from the app across all 3
trials; the brief's "notification vs app" comparison had no notification
data point until a bonus 4th trial (alarm_at ~15s out) was added mid-test.

## Finding: tapping the notification does not complete the todo directly

In the bonus trial, tapping the notification deep-linked into the Bee app
(expanding it), but the operator still had to navigate to the todo inside
the app and tap it there to actually mark it complete -- there is no
direct complete/checkmark action reachable from the notification itself.
This matches the already-documented Phase 0.9 finding for question todos,
now reconfirmed for this ready-tick todo specifically. Practically: a
notification-based ready-tick is really "notification -> app -> tap",
not a one-tap action, so it is not meaningfully faster than just opening
the app directly when ready.

## Recommendation (reported, not decided)

If the v4 design intends the operator to ever tick from a notification,
either the standing tick-todo needs a periodic/repeating `alarm_at` (so a
notification exists to tap at all), or the design should treat "tick from
app" as the only real path and drop the notification-tap case from the
UX copy, since it does not save a step over just opening the app.
