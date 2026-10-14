import React from 'react';
import {Composition} from 'remotion';
import {Main, totalFrames} from './Main';
import {FPS, HEIGHT, WIDTH} from './theme';

export const Root: React.FC = () => (
  <Composition id="Main" component={Main} durationInFrames={totalFrames()} fps={FPS} width={WIDTH} height={HEIGHT} />
);
