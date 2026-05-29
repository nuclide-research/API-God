// Quick smoke test — launches browser, hits a public JSON API,
// verifies request + response captures land in the DB.
import { launch }      from './src/browser.js';
import { attach }      from './src/interceptor.js';
import { loadPlugins } from './src/plugins.js';
import { query, stats } from './src/storage.js';

await loadPlugins();
const { context, page } = await launch();
await attach(context, page);

console.log('[test] navigating to httpbin.org/json …');
await page.goto('https://httpbin.org/json', { waitUntil: 'networkidle' });

// Give interceptor a tick to flush async saves
await page.waitForTimeout(1000);

const responses = query({ domain: 'httpbin.org', type: 'response' });
const requests  = query({ domain: 'httpbin.org', type: 'request'  });

console.log(`\n[test] results:`);
console.log(`  requests captured : ${requests.length}`);
console.log(`  responses captured: ${responses.length}`);

if (responses.length > 0) {
  const r = responses[0];
  const parsed = JSON.parse(r.data);
  console.log(`  status            : ${r.status}`);
  console.log(`  body preview      : ${parsed.body?.slice(0, 80) ?? '(no body)'}`);
  console.log('\n[test] PASS — capture layer working');
} else {
  console.log('\n[test] FAIL — no responses captured');
}

console.log('\n[test] all domains in DB:');
console.table(stats());

await context.close();
process.exit(0);
