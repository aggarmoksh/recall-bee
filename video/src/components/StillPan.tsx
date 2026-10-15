import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';
import type {StillKey} from '../types';

// A full-page dashboard capture shown through a slow virtual camera. Coordinates are CSS px of the 1920-wide capture;
// z is the zoom relative to a 1920 px-wide view (z = 2 shows 960 css px across the frame).
// With cut=true the keys are read as pairs (start framing, end framing): the camera drifts slowly inside each pair and
// hard-cuts to the next pair, so two framings are never blended.
export const StillPan: React.FC<{src: string; h: number; keys: StillKey[]; cut?: boolean}> = ({src, h, keys, cut}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  let cx: number, cy: number, z: number;
  if (cut) {
    let i = 0;
    for (let k = 0; k + 1 < keys.length; k += 2) if (t >= keys[k].t) i = k;
    const a = keys[i];
    const b = keys[i + 1];
    const p = Math.min(1, Math.max(0, (t - a.t) / Math.max(0.001, b.t - a.t)));
    cx = a.cx + (b.cx - a.cx) * p;
    cy = a.cy + (b.cy - a.cy) * p;
    z = a.z + (b.z - a.z) * p;
  } else {
    const ts = keys.map((k) => k.t);
    const opts = {easing: Easing.inOut(Easing.cubic), extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const};
    cx = interpolate(t, ts, keys.map((k) => k.cx), opts);
    cy = interpolate(t, ts, keys.map((k) => k.cy), opts);
    z = interpolate(t, ts, keys.map((k) => k.z), opts);
  }
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
