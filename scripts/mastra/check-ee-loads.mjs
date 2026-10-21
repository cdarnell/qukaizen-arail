// B0.1 authoritative check. Input: file of resolved module URLs (from trace-loads.mjs).
// FAIL if any URL contains /ee/, or any ee-named module other than the pinned inert chunk loaded,
// or the installed @mastra/core version differs from the pin. Usage: node check-ee-loads.mjs <loaded-urls.txt>
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const pin = Object.fromEntries(readFileSync(path.join(here, 'ee-chunk.pin'), 'utf8').split('\n')
  .filter((l) => l && !l.startsWith('#')).map((l) => l.split('=')));
const core = JSON.parse(readFileSync(path.join(here, '../../integrations/mastra-studio/node_modules/@mastra/core/package.json'))).version;
if (process.argv[2] === '--installed') {
  // Static install-time pin: version matches, the pinned chunk exists, and it is the only ee-named file in core/dist.
  const dist = path.join(here, '../../integrations/mastra-studio/node_modules/@mastra/core/dist');
  const eeFiles = readdirSync(dist).filter((f) => /^ee[-.]/.test(f)).map((f) => `@mastra/core/dist/${f}`);
  const probs = [];
  if (core !== pin.core_version) probs.push(`@mastra/core ${core} != pinned ${pin.core_version}`);
  if (!existsSync(path.join(dist, pin.chunk.split('/').pop()))) probs.push('pinned ee chunk missing (changed?)');
  // the pinned chunk also ships as .map and a .cjs twin with the same build stem
  for (const f of eeFiles) if (!f.split('/').pop().startsWith(pin.chunk_stem + '-')) probs.push(`unexpected ee-named file: ${f}`);
  console.log(JSON.stringify({ check: 'ee-installed', core, ee_files: eeFiles, problems: probs }, null, 1));
  process.exit(probs.length ? 1 : 0);
}
const urls = [...new Set(readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean))];
const problems = [];
if (core !== pin.core_version) problems.push(`@mastra/core ${core} != pinned ${pin.core_version} (re-review the ee chunk)`);
let sawPinned = false;
for (const u of urls) {
  if (u.includes('/ee/')) problems.push(`ee path loaded: ${u}`);
  const base = u.split('/').pop();
  if (/^ee[-.]/.test(base) || /[-/]ee\.[cm]?js$/.test(u)) {
    if (u.includes('/node_modules/' + pin.chunk)) sawPinned = true;
    else problems.push(`unexpected ee-named module: ${u}`);
  }
}
console.log(JSON.stringify({ check: 'ee-loads', core, urls: urls.length, pinned_chunk_loaded: sawPinned, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
