import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {theme} from '../theme';

// Readability rules (V2): nothing that must be read is below 40 px, titles are 96 px or more.

const Base: React.FC<{children: React.ReactNode; transparent?: boolean}> = ({children, transparent}) => (
  <AbsoluteFill
    style={{
      background: transparent ? 'transparent' : theme.bg,
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

export const TitleCard: React.FC<{title: string; sub: string; transparent?: boolean}> = ({title, sub, transparent}) => {
  const o = useFade();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 200}});
  return (
    <Base transparent={transparent}>
      <div style={{opacity: o, textAlign: 'center', transform: `scale(${0.96 + 0.04 * s})`, textShadow: transparent ? '0 4px 30px rgba(0,0,0,0.65)' : undefined}}>
        <div style={{fontSize: 220, fontWeight: 800, letterSpacing: -7, color: theme.accent}}>{title}</div>
        <div style={{fontSize: 56, fontWeight: 500, marginTop: 10, color: theme.text}}>{sub}</div>
      </div>
    </Base>
  );
};

// Six boxes in one row, 1840 px wide (96% of the frame), one short line each, 46 px text.
export const LoopCard: React.FC<{steps: string[]; duration: number; at?: number[]}> = ({steps, duration, at: times}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const per = (duration - 1.2) / steps.length;
  return (
    <Base>
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: 1840}}>
        {steps.map((s, i) => {
          const at = times ? times[i] : 0.3 + i * per;
          const on = t >= at;
          const o = interpolate(t, [at, at + 0.35], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
          return (
            <React.Fragment key={s}>
              <div
                style={{
                  opacity: on ? o : 0.12,
                  background: on ? theme.surface2 : theme.surface,
                  border: `4px solid ${on ? theme.accent : theme.surface2}`,
                  borderRadius: 26,
                  padding: '0 22px',
                  height: 170,
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  fontSize: 42,
                  fontWeight: 800,
                  textAlign: 'center',
                  whiteSpace: 'nowrap',
                }}
              >
                {s}
              </div>
              {i < steps.length - 1 && (
                <div
                  style={{
                    fontSize: 56,
                    fontWeight: 800,
                    color: theme.lavender,
                    opacity: on ? o : 0.12,
                  }}
                >
                  {'→'}
                </div>
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
      <div style={{opacity: o, textAlign: 'center', fontSize: 112, fontWeight: 800, lineHeight: 1.1, maxWidth: 1700}}>
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
      <div style={{opacity: o, width: 1600}}>
        <div style={{fontSize: 44, fontWeight: 700, color: theme.muted, letterSpacing: 2, marginBottom: 20}}>
          THE NUDGE RECALL SENDS
        </div>
        <div
          style={{
            background: theme.surface2,
            borderLeft: `12px solid ${theme.accent}`,
            borderRadius: 22,
            padding: '50px 60px',
            fontSize: 68,
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

const Box: React.FC<{title: string; line: string; accent?: boolean; height?: number}> = ({title, line, accent, height = 250}) => (
  <div
    style={{
      background: theme.surface2,
      border: `4px solid ${accent ? theme.accent : theme.surface2}`,
      borderRadius: 26,
      padding: '0 30px',
      width: 560,
      height,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
    }}
  >
    <div style={{fontSize: 50, fontWeight: 800, color: accent ? theme.accent : theme.text, whiteSpace: 'nowrap'}}>{title}</div>
    <div style={{fontSize: 40, color: theme.muted, marginTop: 10, whiteSpace: 'nowrap'}}>{line}</div>
  </div>
);

// Three columns, 1840 px wide (96% of the frame): Bee CLI <-> Recall daemon <-> (Bedrock / Next.js).
export const ArchitectureCard: React.FC = () => {
  const o = useFade();
  return (
    <Base>
      <div style={{opacity: o, display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: 1840}}>
        <Box title="Bee CLI" line="transcripts + todos" height={300} />
        <div style={{fontSize: 80, fontWeight: 800, color: theme.lavender}}>{'↔'}</div>
        <Box title="Recall daemon" line="FastAPI, SQLite, SM-2" accent height={300} />
        <div style={{fontSize: 80, fontWeight: 800, color: theme.lavender}}>{'↔'}</div>
        <div style={{display: 'flex', flexDirection: 'column', gap: 40}}>
          <Box title="Amazon Bedrock" line="Claude Haiku 4.5" height={200} />
          <Box title="Next.js dashboard" line="read-only view" height={200} />
        </div>
      </div>
    </Base>
  );
};

export const EndCard: React.FC<{url: string; line: string; transparent?: boolean}> = ({url, line, transparent}) => {
  const o = useFade();
  return (
    <Base transparent={transparent}>
      <div style={{opacity: o, textAlign: 'center', textShadow: transparent ? '0 4px 30px rgba(0,0,0,0.65)' : undefined}}>
        <div style={{fontSize: 200, fontWeight: 800, color: theme.accent, letterSpacing: -6}}>Recall</div>
        {line && <div style={{fontSize: 54, marginTop: 6}}>{line}</div>}
        <div style={{fontSize: 46, marginTop: line ? 44 : 30, color: theme.muted}}>{url}</div>
      </div>
    </Base>
  );
};
