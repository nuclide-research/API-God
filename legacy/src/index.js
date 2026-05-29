import { launch }       from './browser.js';
import { attach }       from './interceptor.js';
import { loadPlugins }  from './plugins.js';
import { stats, query } from './storage.js';

const cmd = process.argv[2];

// ── api-god query [domain] [--type X] [--limit N] ──
if (cmd === 'query') {
  const domain = process.argv[3] && !process.argv[3].startsWith('--')
    ? process.argv[3] : undefined;
  const typeFlag  = flag('--type');
  const limitFlag = flag('--limit');

  const rows = query({
    domain,
    type:  typeFlag,
    limit: limitFlag ? parseInt(limitFlag, 10) : 200,
  });

  const pretty = process.argv.includes('--pretty') || process.argv.includes('-p');
  for (const row of rows) {
    if (pretty) {
      let parsed = {};
      try { parsed = JSON.parse(row.data ?? '{}'); } catch {}
      process.stdout.write(JSON.stringify({ ...parsed, _id: row.id, _ts: row.ts, _type: row.type, _url: row.url }, null, 2) + '\n');
    } else {
      process.stdout.write(JSON.stringify(row) + '\n');
    }
  }
  process.exit(0);
}

// ── api-god stats ──
if (cmd === 'stats') {
  const rows = stats();
  console.table(rows);
  process.exit(0);
}

// ── api-god start (default) ──
await loadPlugins();
console.log('[api-god] launching browser…');

const { context, page } = await launch();
await attach(context, page);

console.log('[api-god] capturing — browse normally, Ctrl+C to stop');
console.log('[api-god] query: node src/index.js query [domain]');

// Keep alive until killed
await new Promise(() => {});

// ── helpers ──
function flag(name) {
  const i = process.argv.indexOf(name);
  return i !== -1 ? process.argv[i + 1] : undefined;
}
