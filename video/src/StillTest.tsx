import React from 'react';
import {StillPan} from './components/StillPan';
import m from '../tools/measured.json';

export const StillTest: React.FC = () => <StillPan src="answer68.png" h={1080} keys={(m as any).stills.answer68.keys} />;
