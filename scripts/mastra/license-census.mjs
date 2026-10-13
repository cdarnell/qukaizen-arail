// Parse package-lock.json license fields; fail on anything outside the allowlist.
import { readFileSync } from 'node:fs';
const ALLOW = new Set(['MIT','ISC','Apache-2.0','BSD-2-Clause','BSD-3-Clause','0BSD','BlueOak-1.0.0']);
const lock = JSON.parse(readFileSync(new URL('../../integrations/mastra-studio/package-lock.json', import.meta.url)));
const counts = {}; const bad = [];
for (const [p, m] of Object.entries(lock.packages)) {
  if (!p) continue;
  const l = typeof m.license === 'string' ? m.license : JSON.stringify(m.license ?? 'UNKNOWN');
  counts[l] = (counts[l] ?? 0) + 1;
  const ok = l.replace(/[()]/g,'').split(/\s+(?:OR|AND)\s+/).some((x) => ALLOW.has(x));
  if (!ok) bad.push(`${p}\t${l}`);
}
console.log(JSON.stringify(counts, null, 1));
console.log(bad.length ? 'NOT ALLOWED:\n' + bad.join('\n') : 'ALL ALLOWED');
process.exit(bad.length ? 1 : 0);
