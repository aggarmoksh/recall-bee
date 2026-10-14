"""Builds src/edit.json. Measured values live in M (filled from transcript + frame checks)."""
import json, sys, pathlib

M = json.loads(pathlib.Path(__file__).with_name('measured.json').read_text())
FPS = 30

def clip(source, tin, tout, layout='full', **kw):
    d = {'type': 'clip', 'source': source, 'in': round(tin, 2), 'out': round(tout, 2), 'layout': layout}
    d.update(kw)
    return d

VO = {
    3: "Bee hears everything I learn: lectures, study sessions, podcasts. It can summarize them and remember them for me. But in an interview, I'm the one who has to know it.",
    4: "Practicing recall is one of the best-tested ways to learn. Recall turns what Bee heard into quizzes on my wrist that I answer out loud. Bee's transcript is the answer sheet.",
    5: "When a learning session ends, Recall pulls the transcript through the Bee CLI and skips anything casual or personal. Claude Haiku on Amazon Bedrock writes the questions, each tied to the moment I heard it. When I'm free, I say so, or tick a todo. Each question goes out as a Bee todo with an alarm. Recall reads Bee's live transcript while I'm talking, grades me against a rubric, and sends feedback the same way. Spaced repetition decides when I see it again.",
    6: "My first version quizzed me at random times. Six prompts, four missed: driving, asleep, or the alert hit my phone. So Recall waits for you. It nudges after a lecture and in your active hours, holds off while Bee hears you talking, and a missed question never counts against you.",
    7: "In my first two real sessions, I started each one with a single sentence and answered all twelve questions.",
    8: "Recall only quizzes on learning sessions. Casual and personal conversations are skipped, and it never touches audio. This page shows what it ignored, and why.",
    10: "Built on the Bee CLI and Amazon Bedrock, open source under MIT. Bee remembers what you heard. Recall makes sure you do.",
}

NUDGE = "Recall: 6 questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."

c = M['cold']
beats = []

# 1. Cold open (A only, live audio kept)
beats.append({
    'id': 1, 'name': 'Cold open', 'start': 0, 'duration': 20,
    'scenes': [
        clip('A', c['phrase_in'], c['phrase_out'], audio=True,
             label={'text': 'Real Bee data. Waits cut and timed.', 'dur': 3.0}),
        clip('A', c['q1_in'], c['q1_out'], audio=True,
             chips=[{'text': f"+{M['t1']:.0f} s", 'at': 0.15, 'dur': 1.8}]),
        clip('A', c['ans_in'], c['ans_out'], audio=True),
        clip('A', c['fb_in'], c['fb_out'], audio=True,
             chips=[{'text': f"+{M['t2']:.0f} s", 'at': 0.15, 'dur': 1.8}]),
    ],
    'needs_operator': M.get('needs_cold', []),
})

beats.append({'id': 2, 'name': 'Title', 'start': 20, 'duration': 4, 'scenes': [
    {'type': 'title', 'duration': 4, 'title': 'Recall', 'sub': 'Bee remembers what you heard. Recall makes sure you do.'}]})

beats.append({'id': 3, 'name': 'Problem', 'start': 24, 'duration': 14, 'vo': VO[3], 'scenes': [
    {'type': 'slate', 'id': 'AI1', 'duration': 3},
    clip('C', 0.5, 11.5, layout='phone'),
], 'needs_operator': ['AI1 missing (slate for 3 s)']})

beats.append({'id': 4, 'name': 'Loop', 'start': 38, 'duration': 14, 'vo': VO[4], 'scenes': [
    {'type': 'loop', 'duration': 14, 'steps': ['Bee hears', 'Questions', 'Your watch', 'Your voice', 'Graded', 'Next review']}]})

sp = M['split']
beats.append({'id': 5, 'name': 'How it works', 'start': 52, 'duration': 38, 'vo': VO[5], 'scenes': [
    {'type': 'split', 'duration': 22, 'a': {'in': sp['a_in'], 'objectPosition': sp['a_pos']}, 'b': {'in': sp['b_in']}},
    clip('D', M['D']['q_in'], M['D']['q_in'] + 2, layout='inset'),
    clip('D', M['D']['cap_in'], M['D']['cap_in'] + 2, layout='inset'),
    {'type': 'slate', 'id': 'E answer page', 'duration': 12},
], 'needs_operator': ['E answer page missing (slate for 12 s)',
                      f"A/B sync offset estimated from DB alarm times, +-{M['B_offset_err']} s",
                      'B footage is dimmed; brightened in post']})

beats.append({'id': 6, 'name': 'Why waits', 'start': 90, 'duration': 22, 'vo': VO[6], 'scenes': [
    {'type': 'miss', 'duration': 5, 'text': 'Version 1: 6 pop quizzes, 4 missed'},
    {'type': 'nudge', 'duration': 8, 'text': NUDGE},
    clip('B', M['B_goal_in'], M['B_goal_in'] + 9, layout='full'),
], 'needs_operator': ['Nudge N shown as 6; stings.py fills in the real due count']})

beats.append({'id': 7, 'name': 'Results', 'start': 112, 'duration': 11, 'vo': VO[7], 'scenes': [
    clip('A', M['report_in'], M['report_in'] + 6, layout='full'),
    {'type': 'slate', 'id': 'E progress', 'duration': 5},
], 'needs_operator': ['E progress missing (slate for 5 s)',
                      'VO claims "first two real sessions ... twelve questions": DB sessions 7 and 8 are 6/6 each']})

beats.append({'id': 8, 'name': 'Privacy', 'start': 123, 'duration': 11, 'vo': VO[8], 'scenes': [
    {'type': 'slate', 'id': 'E privacy', 'duration': 5.5},
    {'type': 'slate', 'id': 'E sessions', 'duration': 5.5},
], 'needs_operator': ['E privacy and E sessions missing']})

beats.append({'id': 10, 'name': 'Architecture + end', 'start': 134, 'duration': 12, 'vo': VO[10], 'scenes': [
    {'type': 'arch', 'duration': 8},
    {'type': 'end', 'duration': 4, 'url': '[repo URL placeholder]', 'line': 'Bee remembers what you heard. Recall makes sure you do.'},
], 'needs_operator': ['Repo URL placeholder']})

# sanity: scene durations must fill each beat exactly
for b in beats:
    tot = sum((s['out'] - s['in']) if s['type'] == 'clip' else s['duration'] for s in b['scenes'])
    assert abs(tot - b['duration']) < 0.02, (b['id'], tot, b['duration'])

out = {'fps': FPS, 'width': 1920, 'height': 1080, 'measured': {k: v for k, v in M.items() if not isinstance(v, (dict, list))}, 'beats': beats}
pathlib.Path(__file__).resolve().parents[1].joinpath('src', 'edit.json').write_text(json.dumps(out, indent=1))
print('edit.json written;', sum(b['duration'] for b in beats), 's total')
