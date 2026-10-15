export type SourceId = 'A' | 'B' | 'C' | 'D' | 'E' | 'AI1' | 'AI2' | 'AI3' | 'AI4' | 'AI5' | 'AI6';

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
  lift?: number;
  push?: boolean;
  darken?: number;
  crop?: {x: number; y: number; w: number; h: number; srcW: number; srcH: number};
  blurLeft?: {to: number; fade: number};
  chips?: Chip[];
  label?: {text: string; dur: number};
  objectPosition?: string;
  audio?: boolean;
  note?: string;
};

export type SplitScene = {
  type: 'split';
  bCrop?: {x: number; y: number; w: number; h: number};
  duration: number;
  a: {in: number; objectPosition?: string};
  b: {in: number};
  note?: string;
};

export type SlateScene = {type: 'slate'; id: string; duration: number};

export type Backdrop = {source: SourceId; in: number; darken: number};

export type StillKey = {t: number; cx: number; cy: number; z: number};

export type CardScene =
  | {type: 'title'; duration: number; title: string; sub: string; backdrop?: Backdrop}
  | {type: 'still'; duration: number; src: string; h: number; keys: StillKey[]; cut?: boolean}
  | {type: 'loop'; duration: number; steps: string[]; at?: number[]}
  | {type: 'miss'; duration: number; text: string}
  | {type: 'nudge'; duration: number; text: string}
  | {type: 'arch'; duration: number}
  | {type: 'end'; duration: number; url: string; line: string; backdrop?: Backdrop};

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
  no_fade?: boolean;
  audio?: AudioTrack[];
  scenes: Scene[];
  needs_operator?: string[];
};

export type Music = {src: string; start: number; end: number; duck: {from: number; to: number}[]; skip?: number; duck_db?: number};

export type Edit = {
  music?: Music;
  fps: number;
  width: number;
  height: number;
  measured: Record<string, number | string>;
  beats: Beat[];
};

export const sceneDuration = (s: Scene): number =>
  s.type === 'clip' ? (s.out - s.in) / (s.rate ?? 1) : s.duration;
