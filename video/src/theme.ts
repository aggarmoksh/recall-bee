import {loadFont} from '@remotion/google-fonts/Geist';

export const {fontFamily} = loadFont();

export const theme = {
  bg: '#121011',
  surface: '#1d1a1c',
  surface2: '#27232a',
  text: '#f5f2ee',
  muted: '#9a928b',
  accent: '#ffd84d',
  accentText: '#1c1917',
  bad: '#f26b6b',
  good: '#5ccf8a',
  font: fontFamily,
};

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
