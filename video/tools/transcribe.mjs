import {transcribe} from '@remotion/install-whisper-cpp';
import path from 'path'; import fs from 'fs';
const [,, input, out] = process.argv;
const whisperPath = path.join(process.cwd(), 'whisper.cpp');
const t0 = Date.now();
const res = await transcribe({inputPath: input, whisperPath, whisperCppVersion: '1.5.5', model: 'small.en', tokenLevelTimestamps: true, printOutput: false});
fs.writeFileSync(out, JSON.stringify(res, null, 1));
console.log('segments', res.transcription.length, 'secs', ((Date.now()-t0)/1000).toFixed(0));
