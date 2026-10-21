// QA (sprint 2026-10-13-mastra-studio-spike): run with `node --test integrations/mastra-studio/test/`
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = mkdtempSync(path.join(tmpdir(), 'qa-mastra-'));
const base = {
  PATH: process.env.PATH, HOME: process.env.HOME, MASTRA_TELEMETRY_DISABLED: '1', MASTRA_OFFLINE: '1',
  MASTRA_AUTO_REFRESH_PROVIDERS: 'false', ARAIL_DATA_DIR: data, ARAIL_MASTRA_DB: path.join(data, 'mastra', 'mastra.db'),
};
const run = (env, input) => spawnSync(process.execPath, ['run-once.mjs'], { cwd: DIR, env, input, encoding: 'utf8', timeout: 60_000 });
const oneLine = (r) => { const l = r.stdout.split('\n').filter((x) => x.trim()); assert.equal(l.length, 1, r.stdout); return JSON.parse(l[0]); };

test('guard env: each missing flag exits 3 with one JSON line', () => {
  for (const k of ['MASTRA_TELEMETRY_DISABLED', 'MASTRA_OFFLINE', 'MASTRA_AUTO_REFRESH_PROVIDERS']) {
    const env = { ...base }; delete env[k];
    const r = run(env, '{"goal":"g"}');
    assert.equal(r.status, 3); assert.match(oneLine(r).error, /^env_/);
  }
  const r = run({ ...base, MASTRA_OFFLINE: 'true' }, '{"goal":"g"}');
  assert.equal(r.status, 3);
});

test('licence / platform vars refuse to run', () => {
  for (const k of ['MASTRA_LICENSE_KEY', 'MASTRA_EE_LICENSE', 'MASTRA_PLATFORM_ACCESS_TOKEN']) {
    const r = run({ ...base, [k]: 'x' }, '{"goal":"g"}');
    assert.equal(r.status, 3, k); oneLine(r);
  }
});

test('db path outside the data dir (traversal / relative) is refused', () => {
  for (const db of [path.join(data, '..', 'evil.db'), 'lab/data/mastra/mastra.db', data, data + 'x/mastra.db']) {
    const r = run({ ...base, ARAIL_MASTRA_DB: db }, '{"goal":"g"}');
    assert.equal(r.status, 3, db); assert.deepEqual(oneLine(r), { error: 'bad_db_path' });
  }
});

test('malformed requests give one bad_request line', () => {
  for (const inp of ['', 'nope', '[]', 'null', '{"goal":""}', '{"goal":"   "}', `{"goal":"${'x'.repeat(2001)}"}`, '{"goal":{"a":1}}']) {
    const r = run(base, inp);
    assert.deepEqual(oneLine(r), { error: 'bad_request' }, inp);
  }
});

test('scorer refuses a non-loopback model endpoint (no egress)', async () => {
  const r = spawnSync(process.execPath, ['-e',
    "import('./src/mastra/run-probe.mjs').then(m=>m.scoreReport('g','r')).then(v=>console.log('OK'),e=>console.log('ERR '+e.message))"],
  { cwd: DIR, env: { PATH: process.env.PATH, ARAIL_OLLAMA_URL: 'http://example.com:11434' }, encoding: 'utf8', timeout: 30_000 });
  assert.equal(r.stdout.trim(), 'ERR scorer_endpoint_not_loopback');
});

test('unwrap tolerates every documented MCP result shape and garbage', async () => {
  const { unwrap } = await import(path.join(DIR, 'src/mastra/run-probe.mjs'));
  const p = { steps: [], report: 'r' };
  assert.deepEqual(unwrap({ structuredContent: p }), p);
  assert.deepEqual(unwrap({ result: p }), p);
  assert.deepEqual(unwrap({ content: [{ type: 'text', text: JSON.stringify(p) }] }), p);
  assert.deepEqual(unwrap(JSON.stringify(p)), p);
  for (const bad of [null, undefined, 'x', 42, { content: [{ type: 'text', text: '{' }] }]) {
    const v = unwrap(bad);
    assert.ok(!(v && Array.isArray(v.steps)));
  }
});
