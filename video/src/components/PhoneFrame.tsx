import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile} from 'remotion';
import {GRADE, theme} from '../theme';

// Plain rounded frame: no device branding, no notch, no buttons.
export const PhoneFrame: React.FC<{src: string; startFrom: number; endAt: number; muted?: boolean}> = ({
  src,
  startFrom,
  endAt,
  muted = true,
}) => (
  <AbsoluteFill style={{background: theme.bg, justifyContent: 'center', alignItems: 'center'}}>
    <div
      style={{
        height: 1000,
        aspectRatio: '884 / 1920',
        borderRadius: 46,
        border: `10px solid ${theme.surface2}`,
        overflow: 'hidden',
        boxShadow: '0 30px 90px rgba(0,0,0,0.6)',
        background: '#000',
      }}
    >
      <OffthreadVideo
        src={staticFile(src)}
        startFrom={startFrom}
        endAt={endAt}
        muted={muted}
        style={{width: '100%', height: '100%', objectFit: 'cover', filter: GRADE}}
      />
    </div>
  </AbsoluteFill>
);
