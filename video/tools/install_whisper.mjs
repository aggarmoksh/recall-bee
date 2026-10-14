import {installWhisperCpp, downloadWhisperModel} from '@remotion/install-whisper-cpp';
import path from 'path';
const to = path.join(process.cwd(), 'whisper.cpp');
console.log('installing whisper.cpp to', to);
const r = await installWhisperCpp({to, version: '1.5.5', printOutput: false});
console.log('installed', JSON.stringify(r));
const m = await downloadWhisperModel({model: 'small.en', folder: to, printOutput: false});
console.log('model', JSON.stringify(m));
