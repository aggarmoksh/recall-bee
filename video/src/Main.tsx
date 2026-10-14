import React from 'react';
import {AbsoluteFill, OffthreadVideo, Sequence, staticFile} from 'remotion';
import edit from './edit.json';
import {theme} from './theme';
import type {Beat, Edit, Scene, SourceId} from './types';
import {sceneDuration} from './types';
import {Captions, autoCaptions} from './components/Captions';
import {ArchitectureCard, EndCard, LoopCard, MissCard, NudgeCard, TitleCard} from './components/Cards';
import {PhoneFrame} from './components/PhoneFrame';
import {Slate} from './components/Slate';
import {SplitScreen} from './components/SplitScreen';
import {CornerLabel, TimeChip} from './components/TimeChip';
import {WatchInset} from './components/WatchInset';

const data = edit as unknown as Edit;
const FILE: Record<SourceId, string> = {A: 'A.mp4', B: 'B.mp4', C: 'C.mp4', D: 'D.mp4'};

export const totalFrames = (): number => Math.round(data.beats.reduce((s, b) => s + b.duration, 0) * data.fps);

const SceneView: React.FC<{scene: Scene; fps: number}> = ({scene, fps}) => {
  switch (scene.type) {
    case 'clip': {
      const from = Math.round(scene.in * fps);
      const end = Math.round(scene.out * fps);
      const video =
        scene.layout === 'phone' ? (
          <PhoneFrame src={FILE[scene.source]} startFrom={from} endAt={end} muted={!scene.audio} />
        ) : scene.layout === 'inset' ? (
          <WatchInset src={FILE[scene.source]} startFrom={from} endAt={end} />
        ) : (
          <AbsoluteFill style={{background: theme.bg}}>
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
              }}
            />
          </AbsoluteFill>
        );
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
  const captions = beat.captions ?? (beat.vo ? autoCaptions(beat.vo, beat.duration) : []);
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
      {captions.length > 0 && <Captions captions={captions} />}
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
