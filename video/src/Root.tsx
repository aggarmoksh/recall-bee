import React from 'react';
import {Composition} from 'remotion';
import {Main, totalFrames} from './Main';
import {StillTest} from './StillTest';
import {FPS, HEIGHT, WIDTH} from './theme';

export const Root: React.FC = () => (
  <>
  <Composition id="StillTest" component={StillTest} durationInFrames={360} fps={FPS} width={WIDTH} height={HEIGHT} />
  <Composition id="Main" component={Main} durationInFrames={totalFrames()} fps={FPS} width={WIDTH} height={HEIGHT} />
  </>
);
