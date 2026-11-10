// NODE_OPTIONS=--import <this>: log every resolved module URL to $TRACE_LOADS_OUT (default stderr).
import { register } from 'node:module';
register('./trace-loads-hooks.mjs', import.meta.url);
