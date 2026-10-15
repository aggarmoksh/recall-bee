#!/usr/bin/env bash
# Renders renders/<name>-raw.mp4 with Remotion, then masters the audio to -14 LUFS (two-pass loudnorm, linear) with the
# video stream copied: renders/<name>.mp4. Run from video/. Usage: tools/render_final.sh final-v4.1
set -e
name="$1"
export TEMP="$(pwd -W)\work\tmp" TMP="$(pwd -W)\work\tmp"
mkdir -p work/tmp renders
rm -f "renders/$name-raw.mp4" "renders/$name.mp4"
npx remotion render src/index.ts Main "renders/$name-raw.mp4" --concurrency=2 --codec=h264 --audio-codec=aac > "work/render-$name.log" 2>&1
p1=$(ffmpeg -v info -i "renders/$name-raw.mp4" -af "loudnorm=I=-14:TP=-1.5:LRA=20:print_format=json" -f null - 2>&1 | python -c "
import sys,re,json
t=sys.stdin.read(); j=json.loads(re.search(r'\{[^{}]*\"input_i\"[^{}]*\}',t,re.S).group(0)); print(f\"{j['input_i']} {j['input_tp']} {j['input_lra']} {j['input_thresh']} {j['target_offset']}\")")
set -- $p1
ffmpeg -y -v error -i "renders/$name-raw.mp4" -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=20:measured_I=$1:measured_TP=$2:measured_LRA=$3:measured_thresh=$4:offset=$5:linear=true" -c:a aac -b:a 192k -ar 48000 -movflags +faststart "renders/$name.mp4"
echo "DONE $name pass1 I=$1 TP=$2" > "work/render-$name.done"
