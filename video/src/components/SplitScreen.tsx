import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile} from 'remotion';
import {theme} from '../theme';

export const SplitScreen: React.FC<{
  aFrom: number;
  bFrom: number;
  frames: number;
  aPosition?: string;
}> = ({aFrom, bFrom, frames, aPosition = '38% 50%'}) => (
  <AbsoluteFill style={{background: theme.bg, flexDirection: 'row'}}>
    <div style={{width: 840, height: 1080, overflow: 'hidden', position: 'relative'}}>
      <OffthreadVideo
        src={staticFile('A.mp4')}
        startFrom={aFrom}
        endAt={aFrom + frames}
        muted
        style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: aPosition}}
      />
    </div>
    <div style={{width: 1080, height: 1080, display: 'flex', justifyContent: 'center', alignItems: 'center'}}>
      <div style={{width: 1040, borderRadius: 22, overflow: 'hidden', boxShadow: '0 20px 70px rgba(0,0,0,0.55)'}}>
        <OffthreadVideo
          src={staticFile('B.mp4')}
          startFrom={bFrom}
          endAt={bFrom + frames}
          muted
          style={{width: '100%', display: 'block', filter: 'brightness(1.65)'}}
        />
      </div>
    </div>
  </AbsoluteFill>
);
