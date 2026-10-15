import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';
import type {Caption} from '../types';

// Captions are authored in whole phrases (tools/make_edit.py), so a caption never breaks mid-phrase.
export const Captions: React.FC<{captions: Caption[]; bottom?: number}> = ({captions, bottom = 56}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const c = captions.find((x) => t >= x.from && t < x.to);
  if (!c) return null;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: bottom}}>
      {c.label && (
        <div
          style={{
            fontFamily: theme.font,
            fontSize: 42,
            fontWeight: 800,
            color: theme.accentText,
            background: theme.accent,
            padding: '4px 22px',
            borderRadius: 12,
            marginBottom: 10,
          }}
        >
          {c.label}
        </div>
      )}
      <div
        style={{
          maxWidth: 1740,
          textAlign: 'center',
          fontFamily: theme.font,
          fontSize: 54,
          lineHeight: 1.18,
          fontWeight: 700,
          color: theme.text,
          background: 'rgba(18,16,17,0.82)',
          padding: '14px 36px',
          borderRadius: 18,
        }}
      >
        {c.text}
      </div>
    </AbsoluteFill>
  );
};
