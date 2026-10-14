import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';

const Base: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill
    style={{
      background: theme.bg,
      fontFamily: theme.font,
      color: theme.text,
      justifyContent: 'center',
      alignItems: 'center',
    }}
  >
    {children}
  </AbsoluteFill>
);

const useFade = (inFrames = 10) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [0, inFrames], [0, 1], {extrapolateRight: 'clamp'});
};

export const TitleCard: React.FC<{title: string; sub: string}> = ({title, sub}) => {
  const o = useFade();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 200}});
  return (
    <Base>
      <div style={{opacity: o, textAlign: 'center', transform: `scale(${0.96 + 0.04 * s})`}}>
        <div style={{fontSize: 190, fontWeight: 800, letterSpacing: -6, color: theme.accent}}>{title}</div>
        <div style={{fontSize: 52, fontWeight: 500, marginTop: 10, color: theme.text}}>{sub}</div>
      </div>
    </Base>
  );
};

export const LoopCard: React.FC<{steps: string[]; duration: number}> = ({steps, duration}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const per = (duration - 1.2) / steps.length;
  return (
    <Base>
      <div style={{display: 'flex', alignItems: 'center', gap: 22}}>
        {steps.map((s, i) => {
          const at = 0.3 + i * per;
          const on = t >= at;
          const o = interpolate(t, [at, at + 0.35], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
          return (
            <React.Fragment key={s}>
              <div
                style={{
                  opacity: on ? o : 0.12,
                  background: on ? theme.surface2 : theme.surface,
                  border: `3px solid ${on ? theme.accent : theme.surface2}`,
                  borderRadius: 26,
                  width: 250,
                  height: 250,
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  alignItems: 'center',
                  gap: 14,
                }}
              >
                <div style={{fontSize: 40, fontWeight: 800, color: theme.accent}}>{i + 1}</div>
                <div style={{fontSize: 40, fontWeight: 700, textAlign: 'center', padding: '0 14px', lineHeight: 1.1}}>{s}</div>
              </div>
              {i < steps.length - 1 && (
                <div style={{fontSize: 56, color: on ? theme.accent : theme.surface2, opacity: on ? o : 0.12}}>{'>'}</div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </Base>
  );
};

export const MissCard: React.FC<{text: string}> = ({text}) => {
  const o = useFade();
  return (
    <Base>
      <div style={{opacity: o, textAlign: 'center', fontSize: 100, fontWeight: 800, lineHeight: 1.1, maxWidth: 1500}}>
        {text.split(',')[0]},
        <span style={{color: theme.bad}}>{text.split(',').slice(1).join(',')}</span>
      </div>
    </Base>
  );
};

export const NudgeCard: React.FC<{text: string}> = ({text}) => {
  const o = useFade();
  return (
    <Base>
      <div style={{opacity: o, width: 1300}}>
        <div style={{fontSize: 34, fontWeight: 700, color: theme.muted, letterSpacing: 2, marginBottom: 18}}>
          THE NUDGE RECALL SENDS
        </div>
        <div
          style={{
            background: theme.surface2,
            borderLeft: `10px solid ${theme.accent}`,
            borderRadius: 20,
            padding: '46px 56px',
            fontSize: 62,
            fontWeight: 600,
            lineHeight: 1.25,
          }}
        >
          {text}
        </div>
      </div>
    </Base>
  );
};

const Box: React.FC<{title: string; lines: string[]; accent?: boolean}> = ({title, lines, accent}) => (
  <div
    style={{
      background: theme.surface2,
      border: `3px solid ${accent ? theme.accent : theme.surface2}`,
      borderRadius: 24,
      padding: '30px 34px',
      width: 400,
    }}
  >
    <div style={{fontSize: 42, fontWeight: 800, color: accent ? theme.accent : theme.text}}>{title}</div>
    <div style={{fontSize: 30, color: theme.muted, marginTop: 12, lineHeight: 1.3}}>
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  </div>
);

export const ArchitectureCard: React.FC = () => {
  const o = useFade();
  return (
    <Base>
      <div style={{opacity: o, display: 'flex', alignItems: 'center', gap: 36}}>
        <Box title="Bee CLI" lines={['proxy, stream', 'live transcripts', 'todos with alarms']} />
        <div style={{fontSize: 56, color: theme.accent}}>{'<>'}</div>
        <Box title="Recall daemon" lines={['FastAPI, SQLite', 'SM-2 scheduling']} accent />
        <div style={{display: 'flex', flexDirection: 'column', gap: 28}}>
          <Box title="Amazon Bedrock" lines={['Claude Haiku 4.5']} />
          <Box title="Next.js dashboard" lines={['read-only view']} />
        </div>
      </div>
    </Base>
  );
};

export const EndCard: React.FC<{url: string; line: string}> = ({url, line}) => {
  const o = useFade();
  return (
    <Base>
      <div style={{opacity: o, textAlign: 'center'}}>
        <div style={{fontSize: 150, fontWeight: 800, color: theme.accent, letterSpacing: -4}}>Recall</div>
        <div style={{fontSize: 48, marginTop: 6}}>{line}</div>
        <div style={{fontSize: 40, marginTop: 44, color: theme.muted}}>{url}</div>
      </div>
    </Base>
  );
};
