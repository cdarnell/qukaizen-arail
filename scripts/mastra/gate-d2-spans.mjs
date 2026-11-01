// B0.4: judge D2 from the trace JSON that Studio itself serves (loopback API).
// Usage: node gate-d2-spans.mjs <traceId> [studioBase=http://127.0.0.1:4111]
const [traceId, base = 'http://127.0.0.1:4111'] = process.argv.slice(2);
if (!new URL(base).hostname.match(/^(127\.0\.0\.1|localhost|\[::1\])$/)) throw new Error('studio base must be loopback');
const body = await (await fetch(`${base}/api/observability/traces/${traceId}`)).json();
const spans = body.spans ?? [];
const nonEmpty = (v) => v != null && JSON.stringify(v) !== '{}' && JSON.stringify(v) !== '""';
const root = spans.find((s) => !s.parentSpanId);
const tool = spans.find((s) => s.name === 'researcher_run_once' && s.parentSpanId === root?.spanId);
const kids = spans.filter((s) => tool && s.parentSpanId === tool.spanId);
const withIO = kids.filter((s) => nonEmpty(s.input) && nonEmpty(s.output));
const kinds = [...new Set(kids.map((s) => String(s.name).split(':')[0]))];
const need = ['planning', 'model call', 'experiment'];
const verdict = root && tool && kids.length >= 3 && withIO.length === kids.length &&
  need.every((k) => kinds.includes(k)) ? 'PASS' : 'FAIL';
console.log(JSON.stringify({ gate: 'D2', verdict, traceId, root: root?.name, root_type: root?.spanType,
  tool: tool?.name, tool_type: tool?.spanType, child_count: kids.length, children_with_input_and_output: withIO.length,
  child_kinds: kinds, spans: spans.map((s) => ({ spanId: s.spanId, parent: s.parentSpanId, name: s.name,
    type: s.spanType, input: s.input, output: s.output, startedAt: s.startedAt })) }, null, 1));
process.exit(verdict === 'PASS' ? 0 : 1);
