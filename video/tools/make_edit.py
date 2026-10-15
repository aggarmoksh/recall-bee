"""Builds src/edit.json (V3). Run from video/: python tools/make_edit.py

Measured values live in tools/measured.json. Captions are authored in whole phrases (never broken mid-phrase)
and timed in proportion to character count over the beat (or over the VO audio when videos/vo/beatNN.* exists).
Music is added only when videos/music/track.* exists; AI audio is never used.
"""
import glob, json, os, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
M = json.loads((ROOT / 'tools' / 'measured.json').read_text())
FPS = 30
VO_DIR = pathlib.Path(os.environ['RECALL_VO_DIR']) if os.environ.get('RECALL_VO_DIR') else ROOT.parent / 'videos' / 'vo'
MUSIC_DIR = pathlib.Path(os.environ['RECALL_MUSIC_DIR']) if os.environ.get('RECALL_MUSIC_DIR') else ROOT.parent / 'videos' / 'music'
MAX_TOTAL = 170.0


def clip(source, tin, tout, layout='full', **kw):
    d = {'type': 'clip', 'source': source, 'in': round(tin, 3), 'out': round(tout, 3), 'layout': layout}
    d.update(kw)
    return d


def sdur(s):
    return ((s['out'] - s['in']) / s.get('rate', 1)) if s['type'] == 'clip' else s['duration']


# ---- voiceover scripts, authored as phrases (a caption is one or more whole phrases; "||" forces a caption break) ----
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
    vis = round(sum(sdur(s) for s in b['scenes']), 3)
    vo = vo_info(n) if n in PHRASES else None
    b['duration'] = vis
    b['vo_placed'] = bool(vo)
    if vo:
        need = round(vo['dur'] + 0.6, 3)
        if need > vis:
            extra = need - vis
            last = b['scenes'][-1]
            if last['type'] == 'clip' and last['source'].startswith('AI'):
                last['in'] = round(max(0.0, last['in'] - extra), 3)  # AI shots end on their last frames: lengthen at the start
            elif last['type'] == 'clip':
                last['out'] = round(last['out'] + extra * last.get('rate', 1), 3)
            else:
                last['duration'] = round(last['duration'] + extra, 3)
                if last['type'] == 'still':
                    last['keys'][-1]['t'] = last['duration']
            b['duration'] = need
        b['audio'] = b.get('audio', []) + [{'src': vo['file'], 'at': 0.0}]
        b['captions'] = caption_track(n, 0.15, vo['dur'])
    elif n in PHRASES and 'captions' not in b:
        b['captions'] = caption_track(n, 0.15, b['duration'] - 0.3)
    return b


c = M['cold']
sp = M['split']
S = M['stills']
have_stills = all((ROOT / 'proxies' / f).exists() for f in ('answer68.png', 'progress.png', 'privacy.png', 'sessions_top.png'))
NUDGE = "Recall: 6 questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."
beats = []

# 1. Cold open: A only; live audio replaced by the denoised, -16 LUFS track (tools/cold_audio.py).
#    Hard cuts, slow push-in on each wrist shot.
t_phrase = c['phrase_out'] - c['phrase_in']
t_q1 = c['q1_out'] - c['q1_in']
cold = {
    'id': 1, 'name': 'Cold open', 'start': 0, 'no_fade': True,
    'scenes': [
        clip('A', c['phrase_in'], c['phrase_out'], push=True, label={'text': 'Real Bee data. Waits cut and timed.', 'dur': 3.0}),
        clip('A', c['q1_in'], c['q1_out'], push=True, chips=[{'text': f"+{M['t1']:.0f} s", 'at': 0.1, 'dur': 1.5}]),
        clip('A', c['ans_in'], c['ans_out'], push=True),
        clip('A', c['fb_in'], c['fb_out'], push=True, chips=[{'text': f"+{M['t2']:.0f} s", 'at': 0.1, 'dur': 1.5}]),
    ],
    'audio': [{'src': 'cold_audio.wav', 'at': 0.0}],
    'caption_bottom': 170,
}
a0 = t_phrase + t_q1
speech_on = 68.35 - c['ans_in']
mid = a0 + speech_on + (c['ans_out'] - 68.35) / 2
cold['captions'] = [
    {'text': "Hey Bee, let's do a Recall session.", 'from': 0.75, 'to': 3.3},
    {'text': 'Because oscillating signals generate eddy currents,', 'from': round(a0 + speech_on, 2), 'to': round(mid, 2), 'label': 'What Bee heard'},
    {'text': 'they interfere with the signal at higher frequencies,', 'from': round(mid, 2), 'to': round(a0 + (c['ans_out'] - c['ans_in']), 2), 'label': 'What Bee heard'},
]
cold['needs_operator'] = [
    "t1/t2 use the readable text on the watch as 'visible' (icon-first values: 42.5 s and 41.4 s)",
    "Start-phrase caption shows what the audio says (\"Hey Bee, let's do a Recall session.\"); the brief's longer wording is not in the recording",
    "'What Bee heard' shows the first two phrases of the Q1 raw_text; the clip cuts before the rest",
    "The watch notification reads 'Lord Moksh' (your name in Bee's rewrite): keep or blur",
]
beats.append(finish_beat(cold, 1))

# 2. Title over AI3 (honeycomb), darkened 40%
beats.append(finish_beat({'id': 2, 'name': 'Title', 'scenes': [
    {'type': 'title', 'duration': 4, 'title': 'Recall', 'sub': 'Bee remembers what you heard. Recall makes sure you do.',
     'backdrop': {'source': 'AI3', 'in': 1.0, 'darken': 0.4}}]}, 2))

# 3. AI1 (3 s, screen cropped and blurred) -> AI4 (2 s) -> C (5 s, large crop) -> AI2 (4 s, ends on the blank page).
ai1 = M['AI1']
beats.append(finish_beat({'id': 3, 'name': 'Problem', 'scenes': [
    clip('AI1', ai1['in'], ai1['in'] + 3, crop=ai1['crop'], blurLeft=ai1['blur']),
    clip('AI4', M['AI4']['in'], M['AI4']['in'] + 2),
    clip('C', M['C']['in'], M['C']['in'] + round(5 * M['C']['rate'], 3), layout='scroll', rate=M['C']['rate'],
         pan={'from': M['C']['pan'][0], 'to': M['C']['pan'][1]}),
    clip('AI2', M['AI2']['in'], M['AI2']['in'] + 4),
],
    'captions': [
        {'text': 'Bee hears everything I learn: lectures, study sessions, podcasts.', 'from': 0.15, 'to': 4.85},
        {'text': 'It can summarize them and remember them for me.', 'from': 5.15, 'to': 9.85},
        {'text': "But in an interview, I'm the one who has to know it.", 'from': 10.15, 'to': 13.85},
    ],
    'needs_operator': [
        'AI1: the shot is a 1.4x crop with the laptop-screen side blurred (no 2 s window is free of the player UI while the student is in frame)',
        'Beat 3 captions are timed to the visuals (AI1 / C / AI2); with a real VO they are retimed to the audio']}, 3))

# 4. AI5 lead-in (2 s), then the loop card
beats.append(finish_beat({'id': 4, 'name': 'Loop', 'scenes': [
    clip('AI5', M['AI5']['in'], M['AI5']['in'] + 2),
    {'type': 'loop', 'duration': 12, 'steps': ['Bee hears', 'Questions', 'Your watch', 'Your voice', 'Graded', 'Next review']}]}, 4))

# 5. split (A+B), D inset, then the answer page still (E clip only as a fallback if the still is missing)
if have_stills:
    answer = {'type': 'still', 'duration': 12, 'src': S['answer68']['file'], 'h': S['answer68']['h'], 'keys': S['answer68']['keys']}
else:
    answer = clip('E', M['E']['progress'][0], M['E']['progress'][0] + 12, layout='full')
beats.append(finish_beat({'id': 5, 'name': 'How it works', 'scenes': [
    {'type': 'split', 'duration': 22, 'a': {'in': sp['a_in'], 'objectPosition': sp['a_pos']}, 'b': {'in': sp['b_in']}},
    clip('D', M['D']['q_in'], M['D']['q_in'] + 2, layout='inset'),
    clip('D', M['D']['cap_in'], M['D']['cap_in'] + 2, layout='inset'),
    answer,
], 'needs_operator': [
    f"A/B sync offset estimated from DB alarm times, +-{M['B_offset_err']} s",
    'A in the split uses a static crop (30% horizontal) and may clip the watch']}, 5))

beats.append(finish_beat({'id': 6, 'name': 'Why waits', 'scenes': [
    {'type': 'miss', 'duration': 5, 'text': 'Version 1: 6 pop quizzes, 4 missed'},
    {'type': 'nudge', 'duration': 8, 'text': NUDGE},
    clip('B', M['B_goal_in'], M['B_goal_in'] + 9, layout='full'),
], 'needs_operator': ['Nudge N shown as 6; stings.py fills in the real due count']}, 6))


def still(name, dur):
    st = S[name]
    return {'type': 'still', 'duration': dur, 'src': st['file'], 'h': st['h'], 'keys': st['keys']}


if have_stills:
    prog, priv, sess = still('progress', 5), still('privacy', 5.5), still('sessions', 5.5)
else:
    prog = clip('E', *M['E']['progress'], layout='full')
    priv = clip('E', *M['E']['privacy'], layout='full')
    sess = clip('E', *M['E']['sessions'], layout='full')
beats.append(finish_beat({'id': 7, 'name': 'Results', 'scenes': [
    clip('A', M['report_in'], M['report_in'] + 6, layout='full', push=True), prog],
    'needs_operator': ['VO says "first two real sessions ... twelve questions": DB sessions 7 and 8 are 6/6 each']}, 7))
beats.append(finish_beat({'id': 8, 'name': 'Privacy', 'scenes': [priv, sess],
    'needs_operator': ['Sessions still is cropped to the top rows; a lower row names a speaker (public lecture) and is kept out of the file entirely',
                       'Dashboard row text (scores, session descriptions) stays under 40 px even zoomed; headings, numbers and rule text reach 40 px']}, 8))

# 10. Architecture, then the end card over AI6 (darkened)
beats.append(finish_beat({'id': 10, 'name': 'Architecture + end', 'scenes': [
    {'type': 'arch', 'duration': 8},
    {'type': 'end', 'duration': 4, 'url': '[repo URL placeholder]', 'line': '',
     'backdrop': {'source': 'AI6', 'in': 1.0, 'darken': 0.55}},
], 'needs_operator': ['Repo URL placeholder']}, 10))

t = 0.0
for b in beats:
    b['start'] = round(t, 2)
    t += b['duration']
    assert abs(sum(sdur(s) for s in b['scenes']) - b['duration']) < 0.02, b['id']
total = round(t, 2)
assert total < MAX_TOTAL, f'total {total}s exceeds {MAX_TOTAL}s'

# Music: only if videos/music/track.* exists. Starts at the title (never under the cold open), -30 LUFS, ducked 8 dB under VO, 2 s fades.
music = None
mfiles = sorted(glob.glob(str(MUSIC_DIR / 'track.*')))
if mfiles:
    mout = ROOT / 'proxies' / 'music.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', mfiles[0], '-af', 'loudnorm=I=-30:TP=-3:LRA=11', '-ar', '48000', '-ac', '2', str(mout)], check=True)
    title_start = next(b['start'] for b in beats if b['id'] == 2)
    duck = []
    for b in beats:
        for a in b.get('audio', []):
            if a['src'].startswith('vo_'):
                r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(ROOT / 'proxies' / a['src'])], capture_output=True, text=True)
                duck.append({'from': round(b['start'] + a['at'], 2), 'to': round(b['start'] + a['at'] + float(r.stdout.strip()), 2)})
    music = {'src': 'music.wav', 'start': title_start, 'end': total, 'duck': duck}

out = {'fps': FPS, 'width': 1920, 'height': 1080,
       'measured': {k: v for k, v in M.items() if not isinstance(v, (dict, list))}, 'beats': beats}
if music:
    out['music'] = music
(ROOT / 'src' / 'edit.json').write_text(json.dumps(out, indent=1))
print(f'edit.json written; total {total}s;', 'VO placed: ' + (', '.join(str(b['id']) for b in beats if b.get('vo_placed')) or 'none (caption-only)'), '| music:', 'yes' if music else 'no')
