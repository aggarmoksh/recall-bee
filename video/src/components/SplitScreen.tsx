import React from 'react';
import {AbsoluteFill, interpolate, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {GRADE, theme} from '../theme';

export const SplitScreen: React.FC<{
  aFrom: number;
  bFrom: number;
  frames: number;
  aPosition?: string;
}> = ({aFrom, bFrom, frames, aPosition = '38% 50%'}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const push = interpolate(frame, [0, durationInFrames], [1.0, 1.06]);
  return (
  <AbsoluteFill style={{background: theme.bg, flexDirection: 'row'}}>
    <div style={{width: 960, height: 1080, overflow: 'hidden', position: 'relative'}}>
      <OffthreadVideo
        src={staticFile('A.mp4')}
        startFrom={aFrom}
        endAt={aFrom + frames}
        muted
        style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: aPosition, filter: GRADE, transform: `scale(${push})`}}
      />
    </div>
    <div style={{width: 960, height: 1080, display: 'flex', justifyContent: 'center', alignItems: 'center'}}>
      <div style={{width: 920, borderRadius: 22, overflow: 'hidden', boxShadow: '0 20px 70px rgba(0,0,0,0.55)'}}>
        <OffthreadVideo
          src={staticFile('B.mp4')}
          startFrom={bFrom}
          endAt={bFrom + frames}
          muted
          style={{width: '100%', display: 'block', filter: `brightness(1.65) ${GRADE}`}}
        />
      </div>
    </div>
  </AbsoluteFill>
  );
};
