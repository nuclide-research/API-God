// Quick navigation test — launches the persistent session, goes to a URL,
// waits for network idle, then reports what was captured.
// Usage: node scripts/nav.js <url> [--wait <ms>]
import { launch }       from '../src/browser.js';
import { attach }       from '../src/interceptor.js';
import { loadPlugins }  from '../src/plugins.js';
import { query, stats } from '../src/storage.js';

const url   = process.argv[2] ?? 'https://www.shodan.io';
const waitI = process.argv.indexOf('--wait');
const wait  = waitI !== -1 ? parseInt(process.argv[waitI + 1], 10) : 3000;

await loadPlugins();
const { context, page } = await launch();
await attach(context, page);

console.log(`[nav] → ${url}`);
await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
console.log(`[nav] idle — waiting ${wait}ms for async saves…`);
await page.waitForTimeout(wait);

const host = new URL(url).hostname.replace(/^www\./, '');
const rows = query({ domain: host, limit: 100 });

console.log(`\n[nav] captured for ${host}: ${rows.length} records`);
if (rows.length > 0) {
  const byType = {};
  for (const r of rows) byType[r.type] = (byType[r.type] ?? 0) + 1;
  console.table(byType);

  // Show any non-request/response types in full
  const interesting = rows.filter(r => !['request', 'response'].includes(r.type));
  if (interesting.length > 0) {
    console.log('\n[nav] structured records:');
    for (const r of interesting) console.log(JSON.stringify(r));
  }
}

console.log('\n[nav] all domains:');
console.table(stats());

await context.close();
process.exit(0);
