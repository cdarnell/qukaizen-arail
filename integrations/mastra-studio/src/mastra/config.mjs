// Shared by the Studio entry (index.ts) and run-once.mjs so both see the same storage and exporter.
// Only MastraStorageExporter is configured: no cloud/platform exporter, no workflows, datasets, auth.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Mastra } from '@mastra/core';
import { Agent } from '@mastra/core/agent';
import { LibSQLStore } from '@mastra/libsql';
import { Observability, MastraStorageExporter } from '@mastra/observability';

const here = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_DB = path.resolve(here, '../../../../lab/data/mastra/mastra.db');
export const SCORER_ID = 'arail-probe-report-grounded-v0';
export const SCORER_LABEL = 'model-graded probe - not a 3DEval measurement';

export function buildMastra() {
  mkdirSync(path.dirname(process.env.ARAIL_MASTRA_DB ?? DEFAULT_DB), { recursive: true });
  const researcherDriver = new Agent({
    id: 'researcher-driver',
    name: 'researcher-driver',
    instructions: 'Call researcher_run_once with the given goal and return its report.',
    model: 'ollama/llama3.2:1b',
  });
  return new Mastra({
    // Loopback only: `mastra dev` has no --host flag and otherwise binds *:4111 (gate B0.2 finding F11).
    server: { host: '127.0.0.1' },
    agents: { researcherDriver },
    storage: new LibSQLStore({
      id: 'arail-mastra',
      url: `file:${process.env.ARAIL_MASTRA_DB ?? DEFAULT_DB}`,
    }),
    observability: new Observability({
      configs: {
        default: { serviceName: 'arail-mastra', exporters: [new MastraStorageExporter()] },
      },
    }),
  });
}
