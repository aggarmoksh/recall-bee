"""Builds proxies/cold_audio.wav: the cold open's live audio (4 segments of A), lightly denoised and
loudness-normalized to about -16 LUFS (two-pass loudnorm, linear). Run from video/."""
import json, re, subprocess, pathlib

root = pathlib.Path(__file__).resolve().parents[1]
M = json.loads((root / 'tools' / 'measured.json').read_text())
c = M['cold']
segs = [(c['phrase_in'], c['phrase_out']), (c['q1_in'], c['q1_out']), (c['ans_in'], c['ans_out']), (c['fb_in'], c['fb_out'])]

parts = []
for i, (a, b) in enumerate(segs):
    parts.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS[s{i}]")
concat = ''.join(f'[s{i}]' for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[cat]"
pre = "highpass=f=70,afftdn=nr=10:nf=-45:tn=1"  # light noise reduction
src = str(root / 'proxies' / 'A.mp4')
fg = ';'.join(parts + [concat, f"[cat]{pre}[pre]"])

# pass 1: measure
r = subprocess.run(['ffmpeg', '-v', 'info', '-i', src, '-filter_complex', fg + ';[pre]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json[o]',
                    '-map', '[o]', '-f', 'null', '-'], capture_output=True, text=True)
j = json.loads(re.search(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr, re.S).group(0))
ln = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
      f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
out = root / 'proxies' / 'cold_audio.wav'
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src, '-filter_complex', fg + f';[pre]{ln},aresample=48000[o]', '-map', '[o]',
                '-ac', '2', '-ar', '48000', str(out)], check=True)
print('measured input I =', j['input_i'], 'LUFS; wrote', out)
