export type SourceId = 'A' | 'B' | 'C' | 'D';

export type Chip = {text: string; at: number; dur: number};

export type ClipScene = {
  type: 'clip';
  source: SourceId;
  in: number;
  out: number;
  layout: 'full' | 'phone' | 'inset';
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

export type Caption = {text: string; from: number; to: number};

export type Beat = {
  id: number;
  name: string;
  start: number;
  duration: number;
  vo?: string;
  captions?: Caption[];
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
  s.type === 'clip' ? s.out - s.in : s.duration;
