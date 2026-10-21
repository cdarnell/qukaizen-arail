// One probe: root agent span -> MCP tool span -> child spans from steps[] -> score.
// Public span API only (ObservabilityInstance.startSpan / Span.createChildSpan).
const OLLAMA = process.env.ARAIL_OLLAMA_URL ?? 'http://127.0.0.1:11434';

export async function scoreReport(goal, report) {
  const url = new URL('/api/chat', OLLAMA);
  if (!['127.0.0.1', 'localhost', '::1'].includes(url.hostname)) throw new Error('scorer_endpoint_not_loopback');
  const res = await fetch(url, {
    method: 'POST',
    body: JSON.stringify({
      model: process.env.ARAIL_PROBE_MODEL ?? 'llama3.2:1b', stream: false, format: 'json',
      messages: [{ role: 'user', content:
        `Goal: ${goal}\nReport: ${String(report).slice(0, 4000)}\n` +
        'Does the report address the goal? Reply JSON {"score": <number 0..1>}.' }],
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) throw new Error('model_unavailable');
  const body = await res.json();
  const n = Number(JSON.parse(body.message.content).score);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

// MCPClient tool results arrive as {result: ...}, {structuredContent: ...} or MCP content[] text.
export function unwrap(res) {
  let v = res?.structuredContent ?? res?.result ?? res;
  if (v && !Array.isArray(v.steps) && Array.isArray(v.content)) {
    const t = v.content.find((c) => c.type === 'text');
    if (t) { try { v = JSON.parse(t.text); } catch { /* fall through */ } }
  }
  if (typeof v === 'string') { try { v = JSON.parse(v); } catch { /* fall through */ } }
  return v?.result && Array.isArray(v.result.steps) ? v.result : v;
}

export async function runProbe({ mastra, mcp, goal, SpanType, scorerId }) {
  const inst = mastra.observability.getDefaultInstance();
  const root = inst.startSpan({
    type: SpanType.AGENT_RUN, name: 'researcher-driver', entityType: 'agent', entityId: 'researcher-driver',
    input: { goal }, metadata: { source: 'arail', kind: 'probe' },
  });
  const tool = root.createChildSpan({
    type: SpanType.TOOL_CALL, name: 'researcher_run_once', input: { goal },
  });
  let res;
  try {
    const tools = await mcp.listTools();
    const t = tools.arail_researcher_run_once;
    if (!t) throw new Error('tool_missing');
    res = await t.execute({ goal }, {});
  } catch (e) {
    tool.error({ error: e, endSpan: true });
    root.error({ error: e, endSpan: true });
    throw e;
  }
  const payload = unwrap(res);
  if (!payload || !Array.isArray(payload.steps)) {
    const e = new Error('bad_tool_output');
    tool.error({ error: e, endSpan: true }); root.error({ error: e, endSpan: true });
    throw e;
  }
  for (const s of payload.steps) {
    const child = tool.createChildSpan({
      type: SpanType.GENERIC, name: s.name, input: { summary: s.input_summary },
      startTime: new Date(s.start_ts * 1000), attributes: { kind: s.kind },
    });
    child.end({ output: { summary: s.output_summary } });
  }
  tool.end({ output: { experiment_id: payload.experiment_id, steps: payload.steps.length } });
  const score = await scoreReport(goal, payload.report);
  root.end({ output: { report: String(payload.report).slice(0, 2000) } });
  const traceId = root.traceId ?? root.getTraceId?.() ?? root.id;
  await mastra.observability.addScore({
    traceId, spanId: root.id,
    score: { scorerId, score, reason: 'model-graded probe - not a 3DEval measurement' },
  }).catch(() => {});
  return { mastra_trace_id: traceId, scorer_id: scorerId, score, mastra_version: '1.34.0',
           core_version: '1.76.0', experiment_id: payload.experiment_id ?? null,
           arail_trace_id: payload.trace_id ?? null };
}
