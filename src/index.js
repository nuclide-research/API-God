import { launch }  from './browser.js';
import { attach }  from './interceptor.js';
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

  for (const row of rows) process.stdout.write(JSON.stringify(row) + '\n');
  process.exit(0);
}

// ── api-god stats ──
if (cmd === 'stats') {
  const rows = stats();
  console.table(rows);
  process.exit(0);
}

// ── api-god start (default) ──
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
