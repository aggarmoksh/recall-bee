import React from 'react';
import {AbsoluteFill, Audio, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import edit from './edit.json';
import {GRADE, REAL_SOURCES, theme} from './theme';
import type {Backdrop, Beat, ClipScene, Edit, Scene, SourceId} from './types';
import {sceneDuration} from './types';
import {Captions} from './components/Captions';
import {ArchitectureCard, EndCard, LoopCard, MissCard, NudgeCard, TitleCard} from './components/Cards';
import {PhoneFrame} from './components/PhoneFrame';
import {Slate} from './components/Slate';
import {SplitScreen} from './components/SplitScreen';
import {StillPan} from './components/StillPan';
import {CornerLabel, TimeChip} from './components/TimeChip';
import {WatchInset} from './components/WatchInset';

const data = edit as unknown as Edit;
const FILE: Record<SourceId, string> = {
  A: 'A.mp4', B: 'B.mp4', C: 'C.mp4', D: 'D.mp4', E: 'E.mp4',
  AI1: 'AI1.mp4', AI2: 'AI2.mp4', AI3: 'AI3.mp4', AI4: 'AI4.mp4', AI5: 'AI5.mp4', AI6: 'AI6.mp4',
};
const FADE_S = 0.25; // a scene dips out for 0.25 s and the next dips in for 0.25 s: one 0.5 s fade per cut

export const totalFrames = (): number => Math.round(data.beats.reduce((s, b) => s + b.duration, 0) * data.fps);

// Same gentle grade on every real source (A, B, C, D); B's footage is also dim, so it is brightened first.
const gradeFor = (source: SourceId): string | undefined =>
  source === 'B' ? `brightness(1.65) ${GRADE}` : REAL_SOURCES.includes(source) ? GRADE : undefined;

// C shown large: 1500 px wide in a 1000 px window centred in the frame, cropped to the top of the Bee summary, slowly panning down.
const ScrollClip: React.FC<{scene: ClipScene; fps: number}> = ({scene, fps}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const W = 1500;
  const H = (W * 1920) / 884;
  const pan = scene.pan ?? {from: 0.15, to: 0.23};
  const y = interpolate(frame, [0, durationInFrames], [pan.from * H, pan.to * H]);
  return (
    <AbsoluteFill style={{background: theme.bg, justifyContent: 'center', alignItems: 'center'}}>
      <div style={{width: W, height: 1000, overflow: 'hidden', borderRadius: 30, position: 'relative'}}>
        <OffthreadVideo
          src={staticFile(FILE[scene.source])}
          startFrom={Math.round(scene.in * fps)}
          endAt={Math.round(scene.out * fps)}
          muted
          playbackRate={scene.rate ?? 1}
          style={{position: 'absolute', left: 0, top: -y, width: W, height: H, filter: gradeFor(scene.source)}}
        />
      </div>
    </AbsoluteFill>
  );
};

// A crop (at most 1.5x) of a 720p AI shot, with a soft blur over the left side where a laptop screen would show.
const CropClip: React.FC<{scene: ClipScene; fps: number}> = ({scene, fps}) => {
  const c = scene.crop!;
  const k = 1920 / c.w;
  const video = (blur?: number) => (
    <OffthreadVideo
      src={staticFile(FILE[scene.source])}
      startFrom={Math.round(scene.in * fps)}
      endAt={Math.round(scene.out * fps)}
      muted
      playbackRate={scene.rate ?? 1}
      style={{position: 'absolute', left: -c.x * k, top: -c.y * k, width: c.srcW * k, height: c.srcH * k, filter: blur ? `blur(${blur}px)` : undefined}}
    />
  );
  const b = scene.blurLeft;
  return (
    <AbsoluteFill style={{background: theme.bg, overflow: 'hidden'}}>
      {video()}
      {b && (
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: `${b.fade * 100}%`,
            height: '100%',
            overflow: 'hidden',
            WebkitMaskImage: `linear-gradient(to right, #000 ${(b.to / b.fade) * 100}%, transparent 100%)`,
            maskImage: `linear-gradient(to right, #000 ${(b.to / b.fade) * 100}%, transparent 100%)`,
          }}
        >
          {video(38)}
        </div>
      )}
    </AbsoluteFill>
  );
};

const BackdropVideo: React.FC<{b: Backdrop; fps: number; frames: number}> = ({b, fps, frames}) => (
  <AbsoluteFill>
    <OffthreadVideo
      src={staticFile(FILE[b.source])}
      startFrom={Math.round(b.in * fps)}
      endAt={Math.round(b.in * fps) + frames}
      muted
      style={{width: '100%', height: '100%', objectFit: 'cover', filter: `brightness(${1 - b.darken})`}}
    />
  </AbsoluteFill>
);

const SceneView: React.FC<{scene: Scene; fps: number}> = ({scene, fps}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  switch (scene.type) {
    case 'clip': {
      const from = Math.round(scene.in * fps);
      const end = Math.round(scene.out * fps);
      let video: React.ReactNode;
      if (scene.layout === 'phone') {
        video = <PhoneFrame src={FILE[scene.source]} startFrom={from} endAt={end} muted={!scene.audio} />;
      } else if (scene.layout === 'inset') {
        video = <WatchInset src={FILE[scene.source]} startFrom={from} endAt={end} />;
      } else if (scene.layout === 'scroll') {
        video = <ScrollClip scene={scene} fps={fps} />;
      } else if (scene.crop) {
        video = <CropClip scene={scene} fps={fps} />;
      } else {
        const z = scene.zoom;
        const zoom = z ? interpolate(frame, [0, durationInFrames], [z.from, z.to]) : 1;
        const push = scene.push ? interpolate(frame, [0, durationInFrames], [1.0, 1.06]) : 1; // wrist shots: slow push-in
        const darken = scene.darken ? `brightness(${1 - scene.darken}) ` : scene.lift ? `brightness(${scene.lift}) ` : '';
        video = (
          <AbsoluteFill style={{background: theme.bg, overflow: 'hidden'}}>
            <OffthreadVideo
              src={staticFile(FILE[scene.source])}
              startFrom={from}
              endAt={end}
              playbackRate={scene.rate ?? 1}
              muted={!scene.audio}
              style={{
                width: '100%',
                height: '100%',
                objectFit: scene.source === 'A' || scene.source.startsWith('AI') ? 'cover' : 'contain',
                objectPosition: scene.objectPosition ?? '50% 50%',
                filter: `${darken}${gradeFor(scene.source) ?? ''}`.trim() || undefined,
                transform: `scale(${zoom * push})`,
                transformOrigin: z ? `${z.ox} ${z.oy}` : '50% 50%',
              }}
            />
          </AbsoluteFill>
        );
      }
      return (
        <AbsoluteFill>
          {video}
          {scene.chips?.map((c) => <TimeChip key={c.text + c.at} text={c.text} at={c.at} dur={c.dur} />)}
          {scene.label && <CornerLabel text={scene.label.text} dur={scene.label.dur} />}
        </AbsoluteFill>
      );
    }
    case 'still':
      return <StillPan src={scene.src} h={scene.h} keys={scene.keys} cut={scene.cut} />;
    case 'split':
      return (
        <SplitScreen
          aFrom={Math.round(scene.a.in * fps)}
          bFrom={Math.round(scene.b.in * fps)}
          frames={Math.round(scene.duration * fps)}
          aPosition={scene.a.objectPosition}
          bCrop={scene.bCrop}
        />
      );
    case 'slate':
      return <Slate id={scene.id} />;
    case 'title':
      return (
        <AbsoluteFill style={{background: theme.bg}}>
          {scene.backdrop && <BackdropVideo b={scene.backdrop} fps={fps} frames={durationInFrames} />}
          <TitleCard title={scene.title} sub={scene.sub} transparent={!!scene.backdrop} />
        </AbsoluteFill>
      );
    case 'loop':
      return <LoopCard steps={scene.steps} duration={scene.duration} at={scene.at} />;
    case 'miss':
      return <MissCard text={scene.text} />;
    case 'nudge':
      return <NudgeCard text={scene.text} />;
    case 'arch':
      return <ArchitectureCard />;
    case 'end':
      return (
        <AbsoluteFill style={{background: theme.bg}}>
          {scene.backdrop && <BackdropVideo b={scene.backdrop} fps={fps} frames={durationInFrames} />}
          <EndCard url={scene.url} line={scene.line} transparent={!!scene.backdrop} />
        </AbsoluteFill>
      );
  }
};

// Every cut is a fade (0.25 s out + 0.25 s in) except the hard cuts inside the cold open.
const FadeWrap: React.FC<{fadeIn: boolean; fadeOut: boolean; children: React.ReactNode}> = ({fadeIn, fadeOut, children}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const f = Math.max(1, Math.round(FADE_S * fps));
  const inO = fadeIn ? Math.min(1, frame / f) : 1;
  const outO = fadeOut ? Math.min(1, (durationInFrames - frame) / f) : 1;
  const o = Math.max(0, Math.min(inO, outO));
  return <AbsoluteFill style={{opacity: o}}>{children}</AbsoluteFill>;
};

const BeatView: React.FC<{beat: Beat; fps: number}> = ({beat, fps}) => {
  let at = 0;
  const captions = beat.captions ?? [];
  const n = beat.scenes.length;
  return (
    <AbsoluteFill style={{background: theme.bg}}>
      {beat.scenes.map((scene, i) => {
        const d = sceneDuration(scene);
        const from = Math.round(at * fps);
        at += d;
        const fadeIn = beat.no_fade ? false : true;
        const fadeOut = beat.no_fade ? i === n - 1 : true;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.round(d * fps)}>
            <FadeWrap fadeIn={fadeIn} fadeOut={fadeOut}>
              <SceneView scene={scene} fps={fps} />
            </FadeWrap>
          </Sequence>
        );
      })}
      {beat.audio?.map((a) => (
        <Sequence key={a.src} from={Math.round(a.at * fps)}>
          <Audio src={staticFile(a.src)} volume={a.volume ?? 1} />
        </Sequence>
      ))}
      {captions.length > 0 && <Captions captions={captions} bottom={beat.caption_bottom} />}
    </AbsoluteFill>
  );
};

const MusicBed: React.FC<{m: NonNullable<Edit['music']>; fps: number}> = ({m, fps}) => {
  const DUCK = 0.398; // -8 dB under the voiceover
  const volume = (f: number) => {
    const t = m.start + f / fps;
    const fade = Math.min(
      interpolate(t, [m.start, m.start + 2], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
      interpolate(t, [m.end - 3, m.end], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
    );
    let g = 1;
    for (const r of m.duck) {
      g = Math.min(g, interpolate(t, [r.from - 0.3, r.from, r.to, r.to + 0.3], [1, DUCK, DUCK, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
    }
    return fade * g;
  };
  return (
    <Sequence from={Math.round(m.start * fps)} durationInFrames={Math.round((m.end - m.start) * fps)}>
      <Audio src={staticFile(m.src)} volume={volume} startFrom={Math.round((m.skip ?? 0) * fps)} />
    </Sequence>
  );
};

export const Main: React.FC = () => {
  let at = 0;
  return (
    <AbsoluteFill style={{background: theme.bg}}>
      {data.beats.map((beat) => {
        const from = Math.round(at * data.fps);
        at += beat.duration;
        return (
          <Sequence key={beat.id} from={from} durationInFrames={Math.round(beat.duration * data.fps)}>
            <BeatView beat={beat} fps={data.fps} />
          </Sequence>
        );
      })}
      {data.music && <MusicBed m={data.music} fps={data.fps} />}
    </AbsoluteFill>
  );
};
