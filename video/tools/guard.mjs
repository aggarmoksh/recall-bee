// Final-render guard (V2): never ship a slate, and keep the total under 2:50.
import fs from 'fs';
const edit = JSON.parse(fs.readFileSync(new URL('../src/edit.json', import.meta.url), 'utf8'));
const slates = [];
let total = 0;
for (const b of edit.beats) {
  total += b.duration;
  for (const s of b.scenes) if (s.type === 'slate') slates.push(`beat ${b.id}: SHOT ${s.id} MISSING (${s.duration}s)`);
}
if (slates.length) {
  console.error('Refusing to render the final: slates remain:\n  ' + slates.join('\n  '));
  process.exit(1);
}
if (total >= 170) {
  console.error(`Refusing to render the final: total ${total}s is not under 2:50`);
  process.exit(1);
}
console.log(`guard ok: no slates, total ${total.toFixed(1)}s`);
