import {loadFont} from '@remotion/google-fonts/Inter';

export const {fontFamily} = loadFont('normal', {weights: ['500', '700', '800'], subsets: ['latin']});

// Palette taken from the dashboard's dark theme (web/app/globals.css): amber and lavender on near-black.
export const theme = {
  bg: '#121011',
  surface: '#1d1a1b',
  surface2: '#2b2634',
  text: '#f5f1ee',
  muted: '#a89f98',
  accent: '#e8bf3f', // amber
  accentText: '#171123',
  lavender: '#a78bfa',
  bad: '#f87171',
  good: '#4ade80',
  font: fontFamily,
};

// One gentle grade on the real footage (A, B, C, D): slightly warm, a little more contrast.
export const GRADE = 'contrast(1.07) saturate(1.04) sepia(0.08)';
export const REAL_SOURCES = ['A', 'B', 'C', 'D'];

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
