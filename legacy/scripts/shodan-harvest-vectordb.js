// Playwright Shodan harvest — vector-DB stragglers
// Dorks: typesense, lancedb, vespa, redis stack vector
import { chromium } from 'playwright';
import { writeFileSync, appendFileSync } from 'fs';

const SESSION = '/home/cowboy/API-God/data/session';
const OUT      = '/home/cowboy/recon/vector-db-stragglers-2026-05-27/shodan-raw.jsonl';
const LOG      = '/home/cowboy/recon/vector-db-stragglers-2026-05-27/harvest-log.md';

const DORKS = [
  { label: 'typesense-banner',    query: '"typesense"',              maxPages: 5 },
  { label: 'typesense-html',      query: 'http.html:"typesense"',    maxPages: 3 },
  { label: 'lancedb-html',        query: 'http.html:"lancedb"',      maxPages: 5 },
  { label: 'vespa-banner',        query: '"Vespa" "document"',       maxPages: 4 },
  { label: 'vespa-html',          query: 'http.html:"vespa"',        maxPages: 3 },
];

const context = await chromium.launchPersistentContext(SESSION, {
  headless: false,
  args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
});
const page = context.pages()[0] ?? await context.newPage();

writeFileSync(LOG, `# Vector-DB Stragglers — Shodan Harvest\n_${new Date().toISOString()}_\n\n`);

const seen = new Set();
let totalNew = 0;

for (const dork of DORKS) {
  appendFileSync(LOG, `## ${dork.label}\nQuery: \`${dork.query}\`\n\n`);
  console.log(`\n[harvest] dork: ${dork.label} — "${dork.query}"`);
  let dorkNew = 0;

  for (let p = 1; p <= dork.maxPages; p++) {
    const url = `https://www.shodan.io/search?query=${encodeURIComponent(dork.query)}&page=${p}`;
    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
    } catch {
      console.log(`  page ${p}: timeout`);
      continue;
    }

    // Login wall check
    const wall = await page.evaluate(() =>
      (document.querySelector('.alert')?.textContent ?? '').toLowerCase().includes('log in')
    ).catch(() => false);
    if (wall) {
      console.log('  login wall — waiting for you to log into Shodan in the browser…');
      // Poll every 3s until the wall clears
      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        await page.reload({ waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
        const still = await page.evaluate(() =>
          (document.querySelector('.alert')?.textContent ?? '').toLowerCase().includes('log in')
        ).catch(() => true);
        if (!still) { console.log('  logged in — continuing'); break; }
        process.stdout.write('.');
      }
      p--; continue;
    }

    const results = await page.evaluate(() => {
      return [...document.querySelectorAll('.result')].map(el => {
        const ip      = el.querySelector('a.title')?.textContent?.trim();
        const port    = el.querySelector('a.text-danger')?.getAttribute('href')?.match(/:(\d+)/)?.[1];
        const org     = el.querySelector('.filter-org')?.textContent?.trim();
        const geos    = [...el.querySelectorAll('.filter-link.text-dark')];
        const country = geos[0]?.textContent?.trim();
        const city    = geos[1]?.textContent?.trim();
        const banner  = el.querySelector('pre')?.textContent?.trim()?.slice(0, 300);
        return ip ? { ip, port, org, country, city, banner } : null;
      }).filter(Boolean);
    }).catch(() => []);

    const total = await page.evaluate(() =>
      document.querySelector('.total-results')?.textContent?.replace(/,/g,'').trim()
    ).catch(() => null);

    let pageNew = 0;
    for (const r of results) {
      const key = `${r.ip}:${r.port ?? ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      appendFileSync(OUT, JSON.stringify({ ...r, dork: dork.label }) + '\n');
      pageNew++;
    }

    dorkNew += pageNew;
    console.log(`  page ${p}: ${results.length} results, ${pageNew} new (total Shodan: ${total ?? '?'})`);
    if (p === 1 && total) appendFileSync(LOG, `Total results: ${total}\n\n`);
    if (results.length === 0) break;
    await new Promise(r => setTimeout(r, 1500));
  }

  appendFileSync(LOG, `New IPs from this dork: ${dorkNew}\n\n`);
  totalNew += dorkNew;
}

console.log(`\n[harvest] done — ${totalNew} unique IP:port pairs → ${OUT}`);
appendFileSync(LOG, `## Total\n${totalNew} unique IP:port pairs\n`);
await context.close();
process.exit(0);
