import subprocess, sys, statistics
FPS=4; W,H=84,102
cmd=["ffmpeg","-v","error","-i","../videos/ScreenRecording_10-07-2026 03-44-56_1.mp4","-vf",f"fps={FPS},crop=836:1020:184:1280,scale={W}:{H},format=gray","-f","rawvideo","-"]
p=subprocess.Popen(cmd,stdout=subprocess.PIPE)
fr=[]
while True:
    b=p.stdout.read(W*H)
    if len(b)<W*H: break
    top=b[:W*28]  # top ~27% of the display (where banners drop in)
    fr.append((sum(top)/len(top), sum(b)/len(b)))
print(len(fr),"frames")
# deviation of top-band brightness vs rolling median of 21 frames, ignoring Recall-notification region (t<55s)
flag=[]
for i,(t,a) in enumerate(fr):
    lo,hi=max(0,i-20),min(len(fr),i+21)
    med=statistics.median(x[0] for x in fr[lo:hi])
    flag.append(abs(t-med)>6)
rng=[];s=None
for i,f in enumerate(flag):
    if f and s is None: s=i
    if not f and s is not None: rng.append((s/FPS,(i-1)/FPS)); s=None
if s is not None: rng.append((s/FPS,(len(fr)-1)/FPS))
for a,b in rng: print(f"{a:7.2f} - {b:7.2f}")
