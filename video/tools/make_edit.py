"""Builds src/edit.json (V2). Run from video/: python tools/make_edit.py

Measured values live in tools/measured.json. Captions are authored in whole phrases (never broken mid-phrase)
and timed in proportion to character count over the beat (or over the VO audio when videos/vo/beatNN.* exists).
"""
import json, pathlib, subprocess, glob, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
M = json.loads((ROOT / 'tools' / 'measured.json').read_text())
FPS = 30
import os
VO_DIR = pathlib.Path(os.environ['RECALL_VO_DIR']) if os.environ.get('RECALL_VO_DIR') else ROOT.parent / 'videos' / 'vo'
MAX_TOTAL = 170.0


def clip(source, tin, tout, layout='full', **kw):
    d = {'type': 'clip', 'source': source, 'in': round(tin, 2), 'out': round(tout, 2), 'layout': layout}
    d.update(kw)
    return d


def sdur(s):
    return ((s['out'] - s['in']) / s.get('rate', 1)) if s['type'] == 'clip' else s['duration']


# ---- voiceover scripts, authored as phrases (a caption is one or more whole phrases) ----
PHRASES = {
    3: ["Bee hears everything I learn:", "lectures, study sessions, podcasts.", "It can summarize them and remember them for me.",
        "But in an interview,", "I'm the one who has to know it."],
    4: ["Practicing recall is one of the best-tested ways to learn.", "Recall turns what Bee heard into quizzes on my wrist",
        "that I answer out loud.", "Bee's transcript is the answer sheet."],
    5: ["When a learning session ends,", "Recall pulls the transcript through the Bee CLI", "and skips anything casual or personal.",
        "Claude Haiku on Amazon Bedrock writes the questions,", "each tied to the moment I heard it.",
        "When I'm free, I say so, or tick a todo.", "Each question goes out as a Bee todo with an alarm.",
        "Recall reads Bee's live transcript while I'm talking,", "grades me against a rubric,", "and sends feedback the same way.",
        "Spaced repetition decides when I see it again."],
    6: ["My first version quizzed me at random times.", "Six prompts, four missed:", "driving, asleep, or the alert hit my phone.",
        "So Recall waits for you.", "It nudges after a lecture and in your active hours,", "holds off while Bee hears you talking,",
        "and a missed question never counts against you."],
    7: ["In my first two real sessions,", "I started each one with a single sentence", "and answered all twelve questions."],
    8: ["Recall only quizzes on learning sessions.", "Casual and personal conversations are skipped,", "and it never touches audio.",
        "This page shows what it ignored, and why."],
    10: ["Built on the Bee CLI and Amazon Bedrock,", "open source under MIT.", "||", "Bee remembers what you heard.", "Recall makes sure you do."],
}
MAX_CAPTION_CHARS = 96  # two lines at 54 px in a 1560 px box


def pack(phrases):
    """Whole phrases only. Sentences stay together when they fit; a long sentence splits at its phrase boundaries."""
    sentences, cur = [], []
    for p in phrases:
        if p == '||':
            if cur:
                sentences.append(cur)
                cur = []
            sentences.append(['||'])
            continue
        cur.append(p)
        if p[-1] in '.!?':
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    caps, buf = [], ''
    for sent in sentences:
        if sent == ['||']:
            if buf:
                caps.append(buf)
                buf = ''
            continue
        text = ' '.join(sent)
        if len(text) <= MAX_CAPTION_CHARS:
            units = [text]
        else:
            units, acc = [], ''
            for p in sent:
                if acc and len(acc + ' ' + p) > MAX_CAPTION_CHARS:
                    units.append(acc)
                    acc = p
                else:
                    acc = (acc + ' ' + p).strip()
            units.append(acc)
        for u in units:
            if buf and len(buf + ' ' + u) <= MAX_CAPTION_CHARS:
                buf = buf + ' ' + u
            else:
                if buf:
                    caps.append(buf)
                buf = u
    if buf:
        caps.append(buf)
    return caps


def caption_track(beat_no, start, end):
    caps = pack(PHRASES[beat_no])
    total = sum(len(c) for c in caps)
    span = max(end - start, 1.0)
    out, t = [], start
    for c in caps:
        d = span * len(c) / total
        out.append({'text': c, 'from': round(t, 2), 'to': round(t + d, 2)})
        t += d
    return out


def vo_info(n):
    """videos/vo/beatNN.* -> normalized proxies/vo_NN.wav (-16 LUFS) and its duration; None if the file doesn't exist."""
    files = sorted(glob.glob(str(VO_DIR / f'beat{n:02d}.*')))
    if not files:
        return None
    out = ROOT / 'proxies' / f'vo_{n:02d}.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', files[0], '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '48000', '-ac', '2', str(out)], check=True)
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(out)], capture_output=True, text=True)
    return {'file': f'vo_{n:02d}.wav', 'dur': float(r.stdout.strip())}


def finish_beat(b, n):
    """Apply VO if present: beat length = max(visual, VO + 0.6), extra time goes to the last scene."""
    vis = sum(sdur(s) for s in b['scenes'])
    vo = vo_info(n) if n in PHRASES else None
    vis = round(vis, 3)
    b['duration'] = vis
    b['vo_placed'] = bool(vo)
    if vo:
        need = vo['dur'] + 0.6
        if need > vis:
            extra = need - vis
            last = b['scenes'][-1]
            if last['type'] == 'clip':
                last['out'] = round(last['out'] + extra * last.get('rate', 1), 3)
            else:
                last['duration'] = round(last['duration'] + extra, 2)
            b['duration'] = need
        b['audio'] = b.get('audio', []) + [{'src': vo['file'], 'at': 0.0}]
        b['captions'] = caption_track(n, 0.15, vo['dur'])
    elif n in PHRASES:
        b['captions'] = caption_track(n, 0.15, b['duration'] - 0.3)
    return b


c = M['cold']
E = M['E']
sp = M['split']
NUDGE = "Recall: 6 questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."
beats = []

# 1. Cold open: A only; live audio replaced by the denoised, -16 LUFS track (tools/cold_audio.py)
t_phrase = c['phrase_out'] - c['phrase_in']
t_q1 = c['q1_out'] - c['q1_in']
cold = {
    'id': 1, 'name': 'Cold open', 'start': 0,
    'scenes': [
        clip('A', c['phrase_in'], c['phrase_out'], label={'text': 'Real Bee data. Waits cut and timed.', 'dur': 3.0}),
        clip('A', c['q1_in'], c['q1_out'], chips=[{'text': f"+{M['t1']:.0f} s", 'at': 0.1, 'dur': 1.5}]),
        clip('A', c['ans_in'], c['ans_out']),
        clip('A', c['fb_in'], c['fb_out'], chips=[{'text': f"+{M['t2']:.0f} s", 'at': 0.1, 'dur': 1.5}]),
    ],
    'audio': [{'src': 'cold_audio.wav', 'at': 0.0}],
    'caption_bottom': 170,
}
a0 = t_phrase + t_q1  # beat-relative start of the answer clip
speech_on = 68.35 - c['ans_in']  # answer speech starts this far into the clip
mid = a0 + speech_on + (c['ans_out'] - 68.35) / 2
cold['captions'] = [
    {'text': "Hey Bee, let's do a Recall session.", 'from': 0.75, 'to': 3.3},
    {'text': 'Because oscillating signals generate eddy currents,', 'from': round(a0 + speech_on, 2), 'to': round(mid, 2), 'label': 'What Bee heard'},
    {'text': 'they interfere with the signal at higher frequencies,', 'from': round(mid, 2), 'to': round(a0 + (c['ans_out'] - c['ans_in']), 2), 'label': 'What Bee heard'},
]
cold['needs_operator'] = [
    "t1/t2 use the readable text on the watch as 'visible' (icon-first values: 42.5 s and 41.4 s)",
    "Start-phrase caption shows what the audio says (\"Hey Bee, let's do a Recall session.\"); the brief's longer wording (\"...I'm ready for my questions\") is not in the recording",
    "'What Bee heard' shows the first two phrases of the Q1 raw_text; the clip cuts before the rest ('the signal is pushed to the wire surface ...')",
    "The watch notification reads 'Lord Moksh' (your name in Bee's rewrite): keep or blur",
]
beats.append(finish_beat(cold, 1))

title = {'id': 2, 'name': 'Title', 'scenes': [{'type': 'title', 'duration': 4, 'title': 'Recall', 'sub': 'Bee remembers what you heard. Recall makes sure you do.'}]}
beats.append(finish_beat(title, 2))

# 3. C large, cropped to the top of the Bee summary with a slow scroll. No AI1 file exists, so its 3 s go to C.
beats.append(finish_beat({'id': 3, 'name': 'Problem', 'scenes': [
    clip('C', M['C']['in'], M['C']['in'] + round(14 * M['C']['rate'], 3), layout='scroll', rate=M['C']['rate'], pan={'from': M['C']['pan'][0], 'to': M['C']['pan'][1]})],
    'needs_operator': ['AI1 not found (videos/ai/AI1.*): slot dropped, its 3 s went to C']}, 3))

beats.append(finish_beat({'id': 4, 'name': 'Loop', 'scenes': [
    {'type': 'loop', 'duration': 14, 'steps': ['Bee hears', 'Questions', 'Your watch', 'Your voice', 'Graded', 'Next review']}]}, 4))

beats.append(finish_beat({'id': 5, 'name': 'How it works', 'scenes': [
    {'type': 'split', 'duration': 22, 'a': {'in': sp['a_in'], 'objectPosition': sp['a_pos']}, 'b': {'in': sp['b_in']}},
    clip('D', M['D']['q_in'], M['D']['q_in'] + 2, layout='inset'),
    clip('D', M['D']['cap_in'], M['D']['cap_in'] + 2, layout='inset'),
    {'type': 'slate', 'id': 'E answer page', 'duration': 12},
], 'needs_operator': [
    "MISSING: the answer page for 'What two types of glass are needed in a fiber optic cable and why?' is not in E (the question appears only as a row in Today's answers). 12 s slot is a slate; the final render is blocked until it exists",
    f"A/B sync offset estimated from DB alarm times, +-{M['B_offset_err']} s",
    'B footage is dimmed; brightened x1.65 in post',
    'A in the split uses a static crop (30% horizontal) and may clip the watch']}, 5))

beats.append(finish_beat({'id': 6, 'name': 'Why waits', 'scenes': [
    {'type': 'miss', 'duration': 5, 'text': 'Version 1: 6 pop quizzes, 4 missed'},
    {'type': 'nudge', 'duration': 8, 'text': NUDGE},
    clip('B', M['B_goal_in'], M['B_goal_in'] + 9, layout='full'),
], 'needs_operator': ['Nudge N shown as 6; stings.py fills in the real due count']}, 6))

beats.append(finish_beat({'id': 7, 'name': 'Results', 'scenes': [
    clip('A', M['report_in'], M['report_in'] + 6, layout='full'),
    clip('E', E['progress'][0], E['progress'][1], layout='full', zoom={'from': 1.0, 'to': 1.3, 'ox': '22%', 'oy': '26%'}),
], 'needs_operator': ['VO says "first two real sessions ... twelve questions": DB sessions 7 and 8 are 6/6 each']}, 7))

beats.append(finish_beat({'id': 8, 'name': 'Privacy', 'scenes': [
    clip('E', E['privacy'][0], E['privacy'][1], layout='full', zoom={'from': 1.0, 'to': 1.3, 'ox': '30%', 'oy': '30%'}),
    clip('E', E['sessions'][0], E['sessions'][1], layout='full', zoom={'from': 1.0, 'to': 1.3, 'ox': '20%', 'oy': '28%'}),
], 'needs_operator': ['Dashboard body text stays below 40 px even zoomed; only headings and numbers (13, 4) reach 40 px']}, 8))

beats.append(finish_beat({'id': 10, 'name': 'Architecture + end', 'scenes': [
    {'type': 'arch', 'duration': 8},
    {'type': 'end', 'duration': 4, 'url': '[repo URL placeholder]', 'line': 'Bee remembers what you heard. Recall makes sure you do.'},
], 'needs_operator': ['Repo URL placeholder']}, 10))

t = 0.0
for b in beats:
    b['start'] = round(t, 2)
    t += b['duration']
    assert abs(sum(sdur(s) for s in b['scenes']) - b['duration']) < 0.02, b['id']
total = round(t, 2)
assert total < MAX_TOTAL, f'total {total}s exceeds {MAX_TOTAL}s'

out = {'fps': FPS, 'width': 1920, 'height': 1080,
       'measured': {k: v for k, v in M.items() if not isinstance(v, (dict, list))}, 'beats': beats}
(ROOT / 'src' / 'edit.json').write_text(json.dumps(out, indent=1))
print(f'edit.json written; total {total}s;', 'VO placed: ' + (', '.join(str(b['id']) for b in beats if b.get('vo_placed')) or 'none (caption-only)'))
