import React from 'react';
import {AbsoluteFill, Audio, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import edit from './edit.json';
import {theme} from './theme';
import type {Beat, ClipScene, Edit, Scene, SourceId} from './types';
import {sceneDuration} from './types';
import {Captions} from './components/Captions';
import {ArchitectureCard, EndCard, LoopCard, MissCard, NudgeCard, TitleCard} from './components/Cards';
import {PhoneFrame} from './components/PhoneFrame';
import {Slate} from './components/Slate';
import {SplitScreen} from './components/SplitScreen';
import {CornerLabel, TimeChip} from './components/TimeChip';
import {WatchInset} from './components/WatchInset';

const data = edit as unknown as Edit;
const FILE: Record<SourceId, string> = {A: 'A.mp4', B: 'B.mp4', C: 'C.mp4', D: 'D.mp4', E: 'E.mp4'};

export const totalFrames = (): number => Math.round(data.beats.reduce((s, b) => s + b.duration, 0) * data.fps);

// C shown large: 1300 px wide, cropped to the top of the Bee summary, slowly panning down as the recording scrolls.
const ScrollClip: React.FC<{scene: ClipScene; fps: number}> = ({scene, fps}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const W = 1300;
  const H = (W * 1920) / 884;
  const pan = scene.pan ?? {from: 0.15, to: 0.23};
  const y = interpolate(frame, [0, durationInFrames], [pan.from * H, pan.to * H]);
  return (
    <AbsoluteFill style={{background: theme.bg, justifyContent: 'flex-start', alignItems: 'center', paddingTop: 30}}>
      <div style={{width: W, height: 840, overflow: 'hidden', borderRadius: 30, position: 'relative'}}>
        <OffthreadVideo
          src={staticFile(FILE[scene.source])}
          startFrom={Math.round(scene.in * fps)}
          endAt={Math.round(scene.out * fps)}
          muted
          playbackRate={scene.rate ?? 1}
          style={{position: 'absolute', left: 0, top: -y, width: W, height: H}}
        />
      </div>
    </AbsoluteFill>
  );
};

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
      } else {
        const z = scene.zoom;
        const scale = z ? interpolate(frame, [0, durationInFrames], [z.from, z.to]) : 1;
        video = (
          <AbsoluteFill style={{background: theme.bg, overflow: 'hidden'}}>
            <OffthreadVideo
              src={staticFile(FILE[scene.source])}
              startFrom={from}
              endAt={end}
              muted={!scene.audio}
              style={{
                width: '100%',
                height: '100%',
                objectFit: scene.source === 'A' ? 'cover' : 'contain',
                objectPosition: scene.objectPosition ?? '50% 50%',
                filter: scene.source === 'B' ? 'brightness(1.65)' : undefined,
                transform: `scale(${scale})`,
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
    case 'split':
      return (
        <SplitScreen
          aFrom={Math.round(scene.a.in * fps)}
          bFrom={Math.round(scene.b.in * fps)}
          frames={Math.round(scene.duration * fps)}
          aPosition={scene.a.objectPosition}
        />
      );
    case 'slate':
      return <Slate id={scene.id} />;
    case 'title':
      return <TitleCard title={scene.title} sub={scene.sub} />;
    case 'loop':
      return <LoopCard steps={scene.steps} duration={scene.duration} />;
    case 'miss':
      return <MissCard text={scene.text} />;
    case 'nudge':
      return <NudgeCard text={scene.text} />;
    case 'arch':
      return <ArchitectureCard />;
    case 'end':
      return <EndCard url={scene.url} line={scene.line} />;
  }
};

const BeatView: React.FC<{beat: Beat; fps: number}> = ({beat, fps}) => {
  let at = 0;
  const captions = beat.captions ?? [];
  return (
    <AbsoluteFill style={{background: theme.bg}}>
      {beat.scenes.map((scene, i) => {
        const d = sceneDuration(scene);
        const from = Math.round(at * fps);
        at += d;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.round(d * fps)}>
            <SceneView scene={scene} fps={fps} />
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
    </AbsoluteFill>
  );
};
