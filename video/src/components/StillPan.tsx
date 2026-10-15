import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';
import type {StillKey} from '../types';

// A full-page dashboard capture shown through a slow virtual camera. Coordinates are CSS px of the 1920-wide capture;
// z is the zoom relative to a 1920 px-wide view (z = 2 shows 960 css px across the frame).
export const StillPan: React.FC<{src: string; h: number; keys: StillKey[]}> = ({src, h, keys}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const ts = keys.map((k) => k.t);
  const opts = {easing: Easing.inOut(Easing.cubic), extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const};
  const cx = interpolate(t, ts, keys.map((k) => k.cx), opts);
  const cy = interpolate(t, ts, keys.map((k) => k.cy), opts);
  const z = interpolate(t, ts, keys.map((k) => k.z), opts);
  return (
    <AbsoluteFill style={{background: theme.bg, overflow: 'hidden'}}>
      <Img
        src={staticFile(src)}
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: 1920,
          height: h,
          transformOrigin: '0 0',
          transform: `translate(${960 - cx * z}px, ${540 - cy * z}px) scale(${z})`,
        }}
      />
    </AbsoluteFill>
  );
};
