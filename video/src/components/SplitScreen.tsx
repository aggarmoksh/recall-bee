import React from 'react';
import {AbsoluteFill, interpolate, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {GRADE, theme} from '../theme';

const A_W = 900;
const B_W = 1020;
// The dashboard recording, cropped to the greeting, daily goal and question card (below the browser chrome and header band).
const DEFAULT_CROP = {x: 55, y: 232, w: 1280, h: 743};

export const SplitScreen: React.FC<{
  aFrom: number;
  bFrom: number;
  frames: number;
  aPosition?: string;
  bCrop?: {x: number; y: number; w: number; h: number};
}> = ({aFrom, bFrom, frames, aPosition = '38% 50%', bCrop = DEFAULT_CROP}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const push = interpolate(frame, [0, durationInFrames], [1.0, 1.06]);
  const k = B_W / bCrop.w; // scale the crop to the full width of the right half
  return (
    <AbsoluteFill style={{background: theme.bg, flexDirection: 'row'}}>
      <div style={{width: A_W, height: 1080, overflow: 'hidden', position: 'relative'}}>
        <OffthreadVideo
          src={staticFile('A.mp4')}
          startFrom={aFrom}
          endAt={aFrom + frames}
          muted
          style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: aPosition, filter: GRADE, transform: `scale(${push})`}}
        />
      </div>
      <div style={{width: 1920 - A_W, height: 1080, display: 'flex', justifyContent: 'center', alignItems: 'center'}}>
        <div style={{width: B_W, height: bCrop.h * k, borderRadius: 20, overflow: 'hidden', position: 'relative', boxShadow: '0 20px 70px rgba(0,0,0,0.55)'}}>
          <OffthreadVideo
            src={staticFile('B.mp4')}
            startFrom={bFrom}
            endAt={bFrom + frames}
            muted
            style={{
              position: 'absolute',
              left: -bCrop.x * k,
              top: -bCrop.y * k,
              width: 1410 * k,
              height: 1080 * k,
              filter: `brightness(1.65) ${GRADE}`,
            }}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};
