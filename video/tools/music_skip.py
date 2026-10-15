"""Finds where in the music track the bed should start (V4.1, step A2). Run from video/ after make_edit.py wrote proxies/music.wav.

Rule: measure the 2 s at the current MUSIC_SKIP; if they are more than 4 dB below the track's integrated level, move to the first
point at or after 9 s where the forward short-term loudness (3 s window starting at that point) stays within 3 dB of the
integrated level for 5 s.
"""
import re, subprocess, sys

CUR = float(sys.argv[1]) if len(sys.argv) > 1 else 9.0
WAV = 'proxies/music.wav'


def run(args):
    return subprocess.run(args, capture_output=True, text=True).stderr


integrated = float(re.search(r'I:\s+(-?[0-9.]+) LUFS', run(['ffmpeg', '-v', 'info', '-i', WAV, '-af', 'ebur128', '-f', 'null', '-']).split('Summary')[-1]).group(1))
open('work/music_S.txt', 'w').close()
run(['ffmpeg', '-v', 'error', '-i', WAV, '-af', 'ebur128=metadata=1,ametadata=mode=print:file=work/music_S.txt', '-f', 'null', '-'])
t, S = None, []
for line in open('work/music_S.txt', encoding='utf-8'):
    m = re.search(r'pts_time:([0-9.]+)', line)
    if m:
        t = float(m.group(1))
    m = re.search(r'lavfi.r128.S=(-?[0-9.]+)', line)
    if m and t is not None:
        S.append((t, float(m.group(1))))
series = dict((round(a, 1), b) for a, b in S)


def forward(x):  # short-term loudness of the 3 s starting at x
    return series.get(round(x + 3.0, 1))


def two_sec(x):
    out = run(['ffmpeg', '-v', 'info', '-ss', str(x), '-t', '2', '-i', WAV, '-af', 'ebur128', '-f', 'null', '-'])
    return float(re.search(r'I:\s+(-?[0-9.]+) LUFS', out.split('Summary')[-1]).group(1))


start_level = two_sec(CUR)
print(f'track integrated {integrated:.1f} LUFS; first 2 s at skip {CUR}s: {start_level:.1f} LUFS ({start_level - integrated:+.1f} dB)')
if start_level >= integrated - 4:
    print(f'keep MUSIC_SKIP = {CUR}')
    sys.exit(0)
x = 9.0
while x < 120:
    vals = [forward(x + k / 10) for k in range(0, 51)]
    if all(v is not None for v in vals) and all(abs(v - integrated) <= 3 for v in vals):
        print(f'move MUSIC_SKIP {CUR} -> {x:.1f}  (forward short-term within 3 dB of {integrated:.1f} for 5 s; 2 s level there {two_sec(x):.1f} LUFS)')
        break
    x = round(x + 0.1, 1)
else:
    print('no stable point found')
