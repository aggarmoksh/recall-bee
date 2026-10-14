import subprocess
FPS=2; W,H=120,30
cmd=["ffmpeg","-v","error","-i","proxies/B.mp4","-vf",f"fps={FPS},crop=1200:300:85:680,scale={W}:{H},format=gray","-f","rawvideo","-"]
p=subprocess.Popen(cmd,stdout=subprocess.PIPE)
prev=None;i=0;ev=[]
while True:
    b=p.stdout.read(W*H)
    if len(b)<W*H: break
    if prev is not None:
        d=sum(abs(x-y) for x,y in zip(b,prev))/len(b)
        if d>4: ev.append((i/FPS,round(d,1)))
    prev=b;i+=1
print(len(ev),"change events")
last=-99
for t,d in ev:
    if t-last>3: print(f"{t:7.1f}s  diff={d}")
    last=t
