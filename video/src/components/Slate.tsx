import React from 'react';
import {AbsoluteFill} from 'remotion';
import {theme} from '../theme';

export const Slate: React.FC<{id: string}> = ({id}) => (
  <AbsoluteFill
    style={{
      background: '#555a5f',
      color: '#d8dcdf',
      fontFamily: theme.font,
      justifyContent: 'center',
      alignItems: 'center',
      fontSize: 88,
      fontWeight: 800,
      letterSpacing: 4,
    }}
  >
    SHOT {id} MISSING
  </AbsoluteFill>
);
