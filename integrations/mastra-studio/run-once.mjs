#!/usr/bin/env node
// Drive ONE Researcher run through Mastra and print exactly one JSON line on stdout.
// Usage: node run-once.mjs '{"goal": "...", "exp_id": "..."}'   (or JSON on stdin)
// Exit 3: guard env missing. Other nonzero: failure; stdout is still one {"error": code} line.
import { readFileSync } from 'node:fs';
import path from 'node:path';

const REQUIRED = {
  MASTRA_TELEMETRY_DISABLED: '1',
  MASTRA_OFFLINE: '1',
  MASTRA_AUTO_REFRESH_PROVIDERS: 'false',
};
// The result line is the ONLY thing that may reach stdout: capture the real writer, then
// reroute everything else (library logging via console.* or process.stdout) to stderr.
const writeResult = process.stdout.write.bind(process.stdout);
process.stdout.write = (...a) => process.stderr.write(...a);
const out = (obj) => writeResult(JSON.stringify(obj) + '\n');
const fail = (code, exit = 1) => { out({ error: code }); process.exit(exit); };

for (const [k, v] of Object.entries(REQUIRED)) if (process.env[k] !== v) fail(`env_${k}_not_${v}`, 3);
if (process.env.MASTRA_PLATFORM_ACCESS_TOKEN) fail('platform_token_present', 3);
// EE guard: never run with anything that could switch on Mastra enterprise features.
for (const k of ['MASTRA_LICENSE_KEY', 'MASTRA_EE_LICENSE']) if (process.env[k]) fail('ee_license_present', 3);

const dbPath = process.env.ARAIL_MASTRA_DB;
const dataDir = process.env.ARAIL_DATA_DIR;
if (!dbPath || !dataDir || !path.resolve(dbPath).startsWith(path.resolve(dataDir) + path.sep)) fail('bad_db_path', 3);

let req;
try {
  const raw = process.argv[2] ?? readFileSync(0, 'utf8');
  req = JSON.parse(raw);
  if (typeof req.goal !== 'string' || !req.goal.trim() || req.goal.length > 2000) throw new Error('goal');
} catch { fail('bad_request'); }

const pyBin = process.env.ARAIL_PYTHON;
if (!process.env.ARAIL_ROOT) fail('no_arail_root', 3);
if (!pyBin) fail('no_python', 3);

// Everything below writes to stderr only; stdout carries the single result line.
console.log = (...a) => console.error(...a);

try {
  const { buildMastra, SCORER_ID } = await import('./src/mastra/config.mjs');
  const { MCPClient } = await import('@mastra/mcp');
  const { SpanType } = await import('@mastra/core/observability');
  const { runProbe } = await import('./src/mastra/run-probe.mjs');
  const mastra = buildMastra();
  const mcp = new MCPClient({
    id: `arail-researcher-${process.pid}`,
    timeout: 14 * 60 * 1000,
    servers: {
      arail: {
        command: pyBin,
        args: ['-m', 'arail.mcp.researcher_tool'],
        cwd: process.env.ARAIL_ROOT,
        env: {
          ...Object.fromEntries(['PATH', 'HOME', 'ARAIL_DATA_DIR', 'ARAIL_EXPERIMENTS_DIR', 'ARAIL_ROOT', 'LAB_ROOT', 'LAB_PKB', 'PYTHONPATH', 'LAB_TIER', 'LAB_MODE',
            'OLLAMA_HOST', 'ARAIL_OLLAMA_URL'].filter((k) => process.env[k]).map((k) => [k, process.env[k]])),
          ...REQUIRED,
        },
      },
    },
  });
  let result;
  try {
    result = await runProbe({ mastra, mcp, goal: req.goal, SpanType, scorerId: SCORER_ID });
  } finally {
    await mcp.disconnect().catch(() => {});
  }
  await mastra.observability.flush();
  await mastra.observability.shutdown();
  out({ ...result, experiment_id: result.experiment_id ?? req.exp_id ?? null });
  process.exit(0);
} catch (e) {
  console.error(e);
  fail(`run_failed:${(e && e.name) || 'Error'}`);
}
