import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';

export const TimeChip: React.FC<{text: string; at: number; dur: number}> = ({text, at, dur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const opacity = interpolate(t, [at, at + 0.2, at + dur - 0.2, at + dur], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  if (t < at || t > at + dur) return null;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center', paddingTop: 70, opacity}}>
      <div
        style={{
          background: theme.accent,
          color: theme.accentText,
          fontFamily: theme.font,
          fontWeight: 800,
          fontSize: 54,
          padding: '12px 34px',
          borderRadius: 999,
          letterSpacing: -1,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

export const CornerLabel: React.FC<{text: string; dur: number}> = ({text, dur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  if (t > dur) return null;
  const opacity = interpolate(t, [0, 0.3, dur - 0.4, dur], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <div
      style={{
        position: 'absolute',
        left: 48,
        top: 48,
        opacity,
        background: 'rgba(18,16,17,0.82)',
        color: theme.text,
        fontFamily: theme.font,
        fontSize: 30,
        fontWeight: 600,
        padding: '10px 22px',
        borderRadius: 14,
        borderLeft: `6px solid ${theme.accent}`,
      }}
    >
      {text}
    </div>
  );
};
