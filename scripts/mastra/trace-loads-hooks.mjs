import { appendFileSync } from 'node:fs';
const out = process.env.TRACE_LOADS_OUT;
export async function resolve(specifier, context, next) {
  const r = await next(specifier, context);
  if (out) appendFileSync(out, r.url + '\n');
  return r;
}
