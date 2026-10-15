"""Aligns each VO file to its script: tools/vo_times.json = per beat, per phrase [start, end] seconds in the VO audio.

Input: work/vo/bNN.json (whisper.cpp word output, 16 kHz) and proxies/vo_NN.wav (normalized VO).
Whisper's word text is rough, so script words are matched to its words with a sequence match; unmatched words are
interpolated between matched neighbours. Each phrase start is then snapped to the end of the nearest pause (silencedetect)
when one is within 0.5 s. Run from video/: python tools/align_vo.py
"""
import difflib, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from vo_script import PHRASES  # noqa: E402


def norm(w):
    return re.sub(r'[^a-z0-9]', '', w.lower())


def asr_words(n):
    d = json.loads((ROOT / 'work' / 'vo' / f'b{n:02d}.json').read_text(encoding='utf-8'))
    out, prev_end = [], 0.0
    for s in d['transcription']:
        txt = s['text'].strip()
        end = s['offsets']['to'] / 1000
        if norm(txt):
            out.append((norm(txt), prev_end, end))
        prev_end = end
    return out


def silences(n):
    r = subprocess.run(['ffmpeg', '-v', 'info', '-i', str(ROOT / 'proxies' / f'vo_{n:02d}.wav'), '-af', 'silencedetect=noise=-38dB:d=0.12', '-f', 'null', '-'],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r'silence_start: ([0-9.]+)', r.stderr)]
    ends = [float(x) for x in re.findall(r'silence_end: ([0-9.]+)', r.stderr)]
    return list(zip(starts, ends))


def dur(n):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(ROOT / 'proxies' / f'vo_{n:02d}.wav')], capture_output=True, text=True)
    return float(r.stdout.strip())


result = {}
for n, phrases in PHRASES.items():
    spoken = [p for p in phrases if p != '||']
    script = [(i, w) for i, p in enumerate(spoken) for w in p.split() if norm(w)]
    sw = [norm(w) for _, w in script]
    asr = asr_words(n)
    aw = [a[0] for a in asr]
    times = [None] * len(sw)  # (start, end) per script word
    for blk in difflib.SequenceMatcher(None, sw, aw, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = (asr[blk.b + k][1], asr[blk.b + k][2])
    total = dur(n)
    # interpolate unmatched runs between matched neighbours, in proportion to word length
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(times) else total
        weights = [max(len(sw[k]), 1) for k in range(i, j)]
        acc, span = lo, (hi - lo)
        for k, wgt in zip(range(i, j), weights):
            d = span * wgt / sum(weights)
            times[k] = (acc, acc + d)
            acc += d
        i = j
    sil = silences(n)
    out = []
    for pi, p in enumerate(spoken):
        idx = [k for k, (pj, _) in enumerate(script) if pj == pi]
        s, e = times[idx[0]][0], times[idx[-1]][1]
        if pi > 0:  # snap the start to the end of a nearby pause
            near = [se for (_, se) in sil if abs(se - s) <= 0.5]
            if near:
                s = min(near, key=lambda x: abs(x - s))
        out.append([round(s, 3), round(e, 3)])
    # keep phrases ordered and non-overlapping
    for k in range(1, len(out)):
        out[k][0] = max(out[k][0], out[k - 1][0] + 0.05)
        out[k - 1][1] = min(out[k - 1][1], out[k][0])
    matched = sum(t is not None for t in times)
    result[str(n)] = {'dur': round(total, 3), 'phrases': out, 'words': [[sw[k], round(times[k][0], 3), round(times[k][1], 3), script[k][0]] for k in range(len(sw))]}
    print(f'beat {n}: {len(out)} phrases, dur {total:.2f}s, ASR words matched {len(set(aw) & set(sw))}')
(ROOT / 'tools' / 'vo_times.json').write_text(json.dumps(result, indent=1))
for n in result:
    print(n, [f'{s:.2f}-{e:.2f}' for s, e in result[n]['phrases']])
