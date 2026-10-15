export type SourceId = 'A' | 'B' | 'C' | 'D' | 'E';

export type Chip = {text: string; at: number; dur: number};

export type ClipScene = {
  type: 'clip';
  source: SourceId;
  in: number;
  out: number;
  layout: 'full' | 'phone' | 'inset' | 'scroll';
  zoom?: {from: number; to: number; ox: string; oy: string};
  pan?: {from: number; to: number};
  rate?: number;
  chips?: Chip[];
  label?: {text: string; dur: number};
  objectPosition?: string;
  audio?: boolean;
  note?: string;
};

export type SplitScene = {
  type: 'split';
  duration: number;
  a: {in: number; objectPosition?: string};
  b: {in: number};
  note?: string;
};

export type SlateScene = {type: 'slate'; id: string; duration: number};

export type CardScene =
  | {type: 'title'; duration: number; title: string; sub: string}
  | {type: 'loop'; duration: number; steps: string[]}
  | {type: 'miss'; duration: number; text: string}
  | {type: 'nudge'; duration: number; text: string}
  | {type: 'arch'; duration: number}
  | {type: 'end'; duration: number; url: string; line: string};

export type Scene = ClipScene | SplitScene | SlateScene | CardScene;

export type Caption = {text: string; from: number; to: number; label?: string};

export type AudioTrack = {src: string; at: number; volume?: number};

export type Beat = {
  id: number;
  name: string;
  start: number;
  duration: number;
  vo?: string;
  captions?: Caption[];
  caption_bottom?: number;
  audio?: AudioTrack[];
  scenes: Scene[];
  needs_operator?: string[];
};

export type Edit = {
  fps: number;
  width: number;
  height: number;
  measured: Record<string, number | string>;
  beats: Beat[];
};

export const sceneDuration = (s: Scene): number =>
  s.type === 'clip' ? (s.out - s.in) / (s.rate ?? 1) : s.duration;
