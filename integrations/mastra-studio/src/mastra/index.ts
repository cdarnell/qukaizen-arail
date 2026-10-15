import { Mastra } from '@mastra/core';
import { Agent } from '@mastra/core/agent';
import { LibSQLStore } from '@mastra/libsql';

// Gate-build skeleton: one driver agent, local LibSQL only. No exporters beyond defaults,
// no workflows/datasets/auth. The MCP client and scorer arrive after the D1/D2 gates.
const researcherDriver = new Agent({
  name: 'researcher-driver',
  instructions: 'Call researcher_run_once with the given goal and return its report.',
  model: 'ollama/llama3.2:1b' as never,
});

export const mastra = new Mastra({
  // Loopback only: `mastra dev` has no --host flag and otherwise binds *:4111 (gate B0.2 finding F11).
  server: { host: '127.0.0.1' },
  agents: { researcherDriver },
  storage: new LibSQLStore({
    id: 'arail-mastra',
    url: `file:${process.env.ARAIL_MASTRA_DB ?? '../../lab/data/mastra/mastra.db'}`,
  }),
});
