import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile} from 'remotion';
import {theme} from '../theme';

// D (already cropped to the watch display) shown on its own, never beside B.
export const WatchInset: React.FC<{src: string; startFrom: number; endAt: number}> = ({src, startFrom, endAt}) => (
  <AbsoluteFill style={{background: theme.bg, justifyContent: 'center', alignItems: 'center'}}>
    <div
      style={{
        height: 900,
        aspectRatio: '836 / 1020',
        borderRadius: 64,
        overflow: 'hidden',
        border: `8px solid ${theme.surface2}`,
        boxShadow: '0 30px 90px rgba(0,0,0,0.6)',
        background: '#000',
      }}
    >
      <OffthreadVideo
        src={staticFile(src)}
        startFrom={startFrom}
        endAt={endAt}
        muted
        style={{width: '100%', height: '100%', objectFit: 'cover'}}
      />
    </div>
  </AbsoluteFill>
);
