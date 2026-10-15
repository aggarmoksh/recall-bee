"""Builds src/edit.json (V4). Run from video/: python tools/make_edit.py

Measured values live in tools/measured.json; the VO script (as phrases) in tools/vo_script.py; per-phrase VO timing
in tools/vo_times.json (python tools/align_vo.py). Captions are whole phrases (never broken mid-phrase) timed to the VO audio.
Shots inside each VO beat are cut to the narration. Music is proxies/music.mp3; AI audio is never used.
"""
import glob, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from vo_script import PHRASES  # noqa: E402

M = json.loads((ROOT / 'tools' / 'measured.json').read_text())
VT_PATH = ROOT / 'tools' / 'vo_times.json'
VT = json.loads(VT_PATH.read_text()) if VT_PATH.exists() else {}
CONFIG = json.loads((ROOT / 'config.json').read_text()) if (ROOT / 'config.json').exists() else {}
FPS = 30
AT = 0.25  # VO starts this long after its beat starts (lets the fade-in finish)
VO_DIR = pathlib.Path(os.environ['RECALL_VO_DIR']) if os.environ.get('RECALL_VO_DIR') else ROOT.parent / 'videos' / 'vo'
MUSIC_FILE = ROOT / 'proxies' / 'music.mp3'
VO_LUFS = -14.0  # raised from -16 after the review (the VO read as low)
MUSIC_LUFS = VO_LUFS - 18  # music sits 18 dB below the VO
MUSIC_SKIP = 9.0  # the track's first seconds are near-silent; start 9 s into the song
DUCK_DB = 8
MAX_TOTAL = 170.0
REPO_URL = os.environ.get('REPO_URL') or CONFIG.get('REPO_URL') or '[repo URL placeholder]'  # single source: video/config.json


def clip(source, tin, tout, layout='full', **kw):
    d = {'type': 'clip', 'source': source, 'in': round(tin, 3), 'out': round(tout, 3), 'layout': layout}
    d.update(kw)
    return d


def sdur(s):
    return ((s['out'] - s['in']) / s.get('rate', 1)) if s['type'] == 'clip' else s['duration']


MAX_CAPTION_CHARS = 96  # two lines at 54 px in a 1560 px box


def pack_groups(phrases):
    """Whole phrases only. Returns [(caption text, [spoken phrase indices])]. Sentences stay together when they fit;
    a long sentence splits at its phrase boundaries; "||" forces a break."""
    items, idx = [], 0
    for p in phrases:
        if p == '||':
            items.append(('||', None))
        else:
            items.append((p, idx))
            idx += 1

    def tl(lst):
        return len(' '.join(p for p, _ in lst))

    sentences, cur = [], []
    for p, i in items:
        if p == '||':
            if cur:
                sentences.append(cur)
                cur = []
            sentences.append('BREAK')
            continue
        cur.append((p, i))
        if p[-1] in '.!?':
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    groups, buf = [], []
    for sent in sentences:
        if sent == 'BREAK':
            if buf:
                groups.append(buf)
                buf = []
            continue
        if tl(sent) <= MAX_CAPTION_CHARS:
            units = [sent]
        else:
            units, acc = [], []
            for it in sent:
                if acc and tl(acc + [it]) > MAX_CAPTION_CHARS:
                    units.append(acc)
                    acc = [it]
                else:
                    acc.append(it)
            units.append(acc)
        for u in units:
            if buf and tl(buf + u) <= MAX_CAPTION_CHARS:
                buf = buf + u
            else:
                if buf:
                    groups.append(buf)
                buf = u
    if buf:
        groups.append(buf)
    return [(' '.join(p for p, _ in g), [i for _, i in g]) for g in groups]


def ph(n, i):
    """Beat-relative (start, end) of spoken phrase i of beat n, in seconds."""
    s, e = VT[str(n)]['phrases'][i]
    return s + AT, e + AT


def vo_captions(n, length):
    groups = pack_groups(PHRASES[n])
    out = []
    for gi, (text, idx) in enumerate(groups):
        start = max(0.0, ph(n, idx[0])[0] - 0.05)
        end = ph(n, idx[-1])[1] + 0.3
        if idx[-1] == len(VT[str(n)]['phrases']) - 1:
            end = max(end, VT[str(n)]['dur'] + AT)  # the last phrase ends with the audio
        out.append({'text': text, 'from': round(start, 2), 'to': round(min(end, length - 0.05), 2)})
    for k in range(len(out) - 1):  # captions never overlap
        out[k]['to'] = round(min(out[k]['to'], out[k + 1]['from'] - 0.02), 2)
    return out


def word_t(n, phrase_i, word):
    """Beat-relative start of the first occurrence of `word` in spoken phrase `phrase_i` of beat n."""
    w = re.sub(r'[^a-z0-9]', '', word.lower())
    for ww, s, _e, pi in VT[str(n)]['words']:
        if pi == phrase_i and ww == w:
            return s + AT
    raise KeyError((n, phrase_i, word))


def vo_info(n):
    """videos/vo/beatNN.* -> normalized proxies/vo_NN.wav (-16 LUFS) and its duration; None if there is no file."""
    files = sorted(glob.glob(str(VO_DIR / f'beat{n:02d}.*')))
    if not files:
        return None
    out = ROOT / 'proxies' / f'vo_{n:02d}.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', files[0], '-af', f'loudnorm=I={VO_LUFS}:TP=-1.5:LRA=11', '-ar', '48000', '-ac', '2', str(out)], check=True)
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(out)], capture_output=True, text=True)
    return {'file': f'vo_{n:02d}.wav', 'dur': float(r.stdout.strip())}


def beat_len(n, design):
    """beat length = max(visual length, VO length + 0.6 s)"""
    if str(n) in VT:
        return round(max(design, VT[str(n)]['dur'] + 0.6), 3)
    return design


def finish_beat(b, n):
    vis = round(sum(sdur(s) for s in b['scenes']), 3)
    vo = vo_info(n) if n in PHRASES else None
    b['duration'] = vis
    b['vo_placed'] = bool(vo)
    if vo:
        assert vis >= round(vo['dur'] + 0.6, 3) - 0.011, (n, vis, vo['dur'])
        b['audio'] = b.get('audio', []) + [{'src': vo['file'], 'at': AT}]
        if 'captions' not in b:
            b['captions'] = vo_captions(n, vis)
    return b


def scale_keys(keys, k):
    return [{**key, 't': round(key['t'] * k, 3)} for key in keys]


c = M['cold']
sp = M['split']
S = M['stills']
have_stills = all((ROOT / 'proxies' / f).exists() for f in ('answer68.png', 'progress.png', 'privacy.png', 'sessions_top.png'))
NUDGE = "Recall: 6 questions are buzzing for you. Say Hey Bee, let's do a Recall session when you can talk."
beats = []

# 1. Cold open: A only; live audio replaced by the denoised, -16 LUFS track (tools/cold_audio.py).
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

# 3. AI1 on "Bee hears everything I learn:", AI4 on "lectures, study sessions, podcasts.", C on "It can summarize...",
#    AI2 on "But in an interview..." (ends on the blank page).
L3 = beat_len(3, 14)
t1, t2, t3 = ph(3, 1)[0] - 0.1, ph(3, 2)[0] - 0.1, ph(3, 3)[0] - 0.1
ai1 = M['AI1']
d_ai1, d_ai4, d_c, d_ai2 = t1, t2 - t1, t3 - t2, L3 - t3
AI1_IN, AI1_OUT = 1.5, 5.0  # stays inside the face shot (it cuts at 5.125 s); slowed to fit the phrase
AI2_OUT = 6.0
beats.append(finish_beat({'id': 3, 'name': 'Problem', 'scenes': [
    clip('AI1', AI1_IN, AI1_OUT, crop=ai1['crop'], blurLeft=ai1['blur'], rate=round((AI1_OUT - AI1_IN) / d_ai1, 4)),
    clip('AI4', 3.0, 3.0 + d_ai4, lift=1.7),
    clip('C', M['C']['in'], M['C']['in'] + round(d_c * M['C']['rate'], 3), layout='scroll', rate=M['C']['rate'],
         pan={'from': M['C']['pan'][0], 'to': M['C']['pan'][1]}),
    clip('AI2', AI2_OUT - d_ai2, AI2_OUT),
],
    'needs_operator': [
        'AI1: the shot is a 1.4x crop, slowed to 0.75x, with the laptop-screen side blurred (no 2 s window is free of the player UI while the student is in frame)',
        'AI2 still shows illegible handwriting before it fades to the blank page (rejected in V2.5, used because the brief calls for it)']}, 3))

# 4. AI5 lead-in (2 s), then the loop card building as the narration names each step
L4 = beat_len(4, 14)
ai5 = 2.0
at = [word_t(4, 1, 'bee'), word_t(4, 1, 'quizzes'), word_t(4, 1, 'wrist'), word_t(4, 2, 'answer'), word_t(4, 3, 'answer'), word_t(4, 3, 'sheet') + 0.5]
at = [round(max(0.3, x - ai5), 2) for x in at]
beats.append(finish_beat({'id': 4, 'name': 'Loop', 'scenes': [
    clip('AI5', M['AI5']['in'], M['AI5']['in'] + ai5),
    {'type': 'loop', 'duration': round(L4 - ai5, 3), 'steps': ['Bee hears', 'Questions', 'Your watch', 'Your voice', 'Graded', 'Next review'], 'at': at}]}, 4))

# 5. split (A+B) while the pipeline is described, D inset on "When I'm free ... alarm", answer page on "Recall reads ..."
L5 = beat_len(5, 38)
t_split = ph(5, 5)[0] - 0.1
t_d = ph(5, 7)[0] - 0.1
d_still = L5 - t_d
v = lambda i: ph(5, i)[0] - t_d  # noqa: E731  (still-relative start of spoken phrase i)
ak = [(0.0, 960, 270, 1.5), (v(7) + 0.9, 960, 280, 1.55),            # question + badge, then
      (v(7) + 1.0, 716, 452, 2.2), (v(8) - 0.05, 730, 458, 2.25),    # your spoken answer
      (v(8), 1204, 452, 2.2), (v(9) - 0.05, 1215, 458, 2.25),        # rubric checks
      (v(9), 960, 600, 2.0), (v(10) - 0.05, 960, 604, 2.05),         # "Sent to your wrist"
      (v(10), 960, 400, 1.25), (d_still, 960, 405, 1.3)]             # whole upper page (never the source line)
if have_stills:
    answer = {'type': 'still', 'duration': round(d_still, 3), 'src': S['answer68']['file'], 'h': S['answer68']['h'], 'cut': True,
              'keys': [{'t': round(t_, 3), 'cx': cx, 'cy': cy, 'z': z} for t_, cx, cy, z in ak]}
else:
    answer = clip('E', M['E']['progress'][0], M['E']['progress'][0] + d_still, layout='full')
half = (t_d - t_split) / 2
beats.append(finish_beat({'id': 5, 'name': 'How it works', 'scenes': [
    {'type': 'split', 'duration': round(t_split, 3), 'a': {'in': sp['a_in'], 'objectPosition': sp['a_pos']}, 'b': {'in': sp['b_in']}},
    clip('D', M['D']['q_in'], M['D']['q_in'] + half, layout='inset'),
    clip('D', M['D']['cap_in'], M['D']['cap_in'] + (t_d - t_split - half), layout='inset'),
    answer,
], 'needs_operator': [
    f"A/B sync offset estimated from DB alarm times, +-{M['B_offset_err']} s",
    'The dashboard crop (greeting, daily goal, question card) is scaled to the right half WIDTH; filling the half HEIGHT would need a zoom that cuts the cards',
    'The answer page file is cropped above its "Source" line, whose server-rendered date reads D/M/YYYY (12/10/2026)']}, 5))

# 6. Miss card -> nudge card -> B daily-goal area, cut to the narration
L6 = beat_len(6, 22)
t_a, t_b = ph(6, 3)[0] - 0.1, ph(6, 5)[0] - 0.1
d_b = L6 - t_b
beats.append(finish_beat({'id': 6, 'name': 'Why waits', 'scenes': [
    {'type': 'miss', 'duration': round(t_a, 3), 'text': 'Version 1: 6 pop quizzes, 4 missed'},
    {'type': 'nudge', 'duration': round(t_b - t_a, 3), 'text': NUDGE},
    clip('B', round(637.3 - d_b, 3), 637.3, layout='full'),
], 'needs_operator': ['Nudge N shown as 6; stings.py fills in the real due count']}, 6))


def still(name, dur):
    st = S[name]
    return {'type': 'still', 'duration': round(dur, 3), 'src': st['file'], 'h': st['h'], 'keys': scale_keys(st['keys'], dur / st['design']) }


# 7. A's final report on "In my first two real sessions, I started each one with a single sentence", Progress on "...twelve questions"
L7 = beat_len(7, 11)
t7 = ph(7, 2)[0] - 0.1
if have_stills:
    prog = still('progress', L7 - t7)
else:
    prog = clip('E', *M['E']['progress'], layout='full')
beats.append(finish_beat({'id': 7, 'name': 'Results', 'scenes': [
    clip('A', M['report_in'], M['report_in'] + t7, layout='full', push=True), prog],
    'needs_operator': ['VO says "first two real sessions ... twelve questions": DB sessions 7 and 8 are 6/6 each']}, 7))

# 8. Privacy page through "...never touches audio.", Sessions on "This page shows what it ignored, and why."
L8 = beat_len(8, 11)
t8 = ph(8, 3)[0] - 0.1
if have_stills:
    priv, sess = still('privacy', t8), still('sessions', L8 - t8)
else:
    priv = clip('E', *M['E']['privacy'], layout='full')
    sess = clip('E', *M['E']['sessions'], layout='full')
beats.append(finish_beat({'id': 8, 'name': 'Privacy', 'scenes': [priv, sess],
    'needs_operator': ['Sessions still is cropped to the top rows; a lower row names a speaker (public lecture) and is kept out of the file entirely',
                       'Dashboard row text (scores, session descriptions) stays under 40 px even zoomed; headings, numbers and rule text reach 40 px']}, 8))

# 10. Architecture through "open source under MIT.", then the end card over AI6 on "Bee remembers what you heard..."
L10 = beat_len(10, 12)
t10 = ph(10, 2)[0] - 0.1
beats.append(finish_beat({'id': 10, 'name': 'Architecture + end', 'scenes': [
    {'type': 'arch', 'duration': round(t10, 3)},
    {'type': 'end', 'duration': round(L10 - t10, 3), 'url': REPO_URL, 'line': '',
     'backdrop': {'source': 'AI6', 'in': 1.0, 'darken': 0.55}},
], 'needs_operator': ['Repo URL is a placeholder until REPO_URL is set (video/config.json)' if REPO_URL.startswith('[') else 'Repo URL from config']}, 10))

t = 0.0
for b in beats:
    b['start'] = round(t, 2)
    t += b['duration']
    assert abs(sum(sdur(s) for s in b['scenes']) - b['duration']) < 0.02, b['id']
total = round(t, 2)
assert total < MAX_TOTAL, f'total {total}s exceeds {MAX_TOTAL}s'

# Music (proxies/music.mp3): starts at the title (never under the cold open), -34 LUFS (18 dB under the VO), a further 8 dB down under VO,
# 2 s fade in, 3 s fade out ending with the end card.
music = None
if MUSIC_FILE.exists():
    mout = ROOT / 'proxies' / 'music.wav'
    af = f'loudnorm=I={MUSIC_LUFS}:TP=-3:LRA=20'
    p1 = subprocess.run(['ffmpeg', '-v', 'info', '-i', str(MUSIC_FILE), '-af', af + ':print_format=json', '-f', 'null', '-'], capture_output=True, text=True)
    j = json.loads(re.search(r'\{[^{}]*"input_i"[^{}]*\}', p1.stderr, re.S).group(0))
    af2 = (f"{af}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
           f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(MUSIC_FILE), '-af', af2, '-ar', '48000', '-ac', '2', str(mout)], check=True)
    title_start = next(b['start'] for b in beats if b['id'] == 2)
    duck = []
    for b in beats:
        for a in b.get('audio', []):
            if a['src'].startswith('vo_'):
                r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(ROOT / 'proxies' / a['src'])], capture_output=True, text=True)
                duck.append({'from': round(b['start'] + a['at'], 2), 'to': round(b['start'] + a['at'] + float(r.stdout.strip()), 2)})
    music = {'src': 'music.wav', 'start': title_start, 'end': total, 'duck': duck, 'skip': MUSIC_SKIP}

out = {'fps': FPS, 'width': 1920, 'height': 1080,
       'measured': {k: v for k, v in M.items() if not isinstance(v, (dict, list))}, 'beats': beats}
if music:
    out['music'] = music
(ROOT / 'src' / 'edit.json').write_text(json.dumps(out, indent=1))
print(f'edit.json written; total {total}s;', 'VO placed: ' + (', '.join(str(b['id']) for b in beats if b.get('vo_placed')) or 'none (caption-only)'), '| music:', 'yes' if music else 'no')
print('beat lengths:', {b['id']: round(b['duration'], 2) for b in beats})
