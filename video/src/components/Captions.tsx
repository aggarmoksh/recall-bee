import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';
import type {Caption} from '../types';

export const Captions: React.FC<{captions: Caption[]}> = ({captions}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const c = captions.find((x) => t >= x.from && t < x.to);
  if (!c) return null;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 56}}>
      <div
        style={{
          maxWidth: 1560,
          textAlign: 'center',
          fontFamily: theme.font,
          fontSize: 58,
          lineHeight: 1.18,
          fontWeight: 700,
          color: theme.text,
          background: 'rgba(18,16,17,0.78)',
          padding: '14px 36px',
          borderRadius: 18,
          maxHeight: 2 * 58 * 1.18 + 28,
          overflow: 'hidden',
        }}
      >
        {c.text}
      </div>
    </AbsoluteFill>
  );
};

const MAX_CHARS_PER_LINE = 38;

// Splits VO text into chunks of at most two lines and spreads them over the beat in proportion to word count.
export const autoCaptions = (vo: string, duration: number, offset = 0): Caption[] => {
  const words = vo.split(/\s+/).filter(Boolean);
  const chunks: string[][] = [];
  let cur: string[] = [];
  for (const w of words) {
    const next = [...cur, w].join(' ');
    const endsSentence = /[.!?]$/.test(cur[cur.length - 1] ?? '');
    if (cur.length > 0 && (next.length > MAX_CHARS_PER_LINE * 2 || (endsSentence && next.length > MAX_CHARS_PER_LINE))) {
      chunks.push(cur);
      cur = [w];
    } else {
      cur.push(w);
    }
  }
  if (cur.length) chunks.push(cur);
  const total = words.length;
  const usable = Math.max(duration - offset - 0.3, 1);
  let at = offset + 0.15;
  return chunks.map((ch) => {
    const len = (ch.length / total) * usable;
    const cap = {text: ch.join(' '), from: at, to: at + len};
    at += len;
    return cap;
  });
};
