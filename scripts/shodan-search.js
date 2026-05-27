// Shodan scraper — drives the authenticated persistent session to paginate search results.
// Uses DOM extraction (no regex, no API key).
//
// Usage:
//   node scripts/shodan-search.js <query> [--pages N] [--start-page N] [--delay N]
//
// Options:
//   --pages N       Number of pages to scrape (default: 5, 10 results each)
//   --start-page N  Start from page N (default: 1)
//   --delay N       Ms between page loads (default: 1500)
//   --headless      Run without a visible window

import { chromium }  from 'playwright';
import { save, stats } from '../src/storage.js';
import { join }        from 'path';
import { fileURLToPath } from 'url';

const ROOT        = join(fileURLToPath(import.meta.url), '../../');
const SESSION_DIR = join(ROOT, 'data/session');

const query     = process.argv.slice(2).filter(a => !a.startsWith('-'))[0];
const pages     = parseInt(flagVal('--pages')      ?? '5',    10);
const startPage = parseInt(flagVal('--start-page') ?? '1',    10);
const delay     = parseInt(flagVal('--delay')      ?? '1500', 10);
const headless  = process.argv.includes('--headless');

if (!query) {
  console.error('Usage: node scripts/shodan-search.js <query> [--pages N] [--start-page N] [--delay N] [--headless]');
  process.exit(1);
}

const context = await chromium.launchPersistentContext(SESSION_DIR, {
  headless,
  args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
});

const page = context.pages()[0] ?? await context.newPage();
let totalSaved = 0;
let totalResults = null;

console.log(`[shodan] query="${query}" pages=${pages} start=${startPage}`);

for (let p = startPage; p < startPage + pages; p++) {
  const url = `https://www.shodan.io/search?query=${encodeURIComponent(query)}&page=${p}`;
  console.log(`[shodan] → page ${p} …`);

  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
  } catch {
    console.log(`[shodan] page ${p} timeout — skipping`);
    continue;
  }

  // Check for login wall
  const needsLogin = await page.evaluate(() => {
    const alert = document.querySelector('.alert')?.textContent ?? '';
    return alert.toLowerCase().includes('log in') || alert.toLowerCase().includes('account');
  }).catch(() => false);

  if (needsLogin || page.url().includes('login') || page.url().includes('account.shodan')) {
    console.log('[shodan] login required — log into shodan.io in the browser window, then press Enter…');
    await new Promise(r => process.stdin.once('data', r));
    // Retry this page
    p--;
    continue;
  }

  // Extract total on first page
  if (totalResults === null) {
    totalResults = await page.evaluate(() => {
      const t = document.querySelector('.total-results')?.textContent?.replace(/,/g,'').trim();
      return t ? parseInt(t, 10) : null;
    }).catch(() => null);
    if (totalResults !== null) console.log(`[shodan] total results: ${totalResults.toLocaleString()}`);
  }

  // Extract all host blocks via DOM
  const hosts = await page.evaluate((q) => {
    return [...document.querySelectorAll('.result')].map(el => {
      const ip      = el.querySelector('a.title')?.textContent?.trim() ?? null;
      const org     = el.querySelector('.filter-org')?.textContent?.trim() ?? null;
      const geoEls  = [...el.querySelectorAll('.filter-link.text-dark')];
      const country = geoEls[0]?.textContent?.trim() ?? null;
      const city    = geoEls[1]?.textContent?.trim() ?? null;
      const tags    = [...el.querySelectorAll('.tag')].map(t => t.textContent.trim());
      const banner  = el.querySelector('pre')?.textContent?.trim()?.slice(0, 500) ?? null;
      const ts      = el.querySelector('.timestamp')?.textContent?.trim() ?? null;
      if (!ip) return null;
      return {
        domain:      'shodan.io',
        type:        'shodan-host',
        url:         `https://www.shodan.io/host/${ip}`,
        data:        JSON.stringify({ query: q, ip, org, country, city, tags, timestamp: ts, banner }),
      };
    }).filter(Boolean);
  }, query).catch(() => []);

  for (const h of hosts) save(h);
  totalSaved += hosts.length;
  console.log(`[shodan] page ${p}: saved ${hosts.length} hosts (running total: ${totalSaved})`);

  if (hosts.length === 0) {
    console.log('[shodan] no results on this page — stopping');
    break;
  }

  if (p < startPage + pages - 1) await new Promise(r => setTimeout(r, delay));
}

console.log(`\n[shodan] done — ${totalSaved} hosts saved for query="${query}"`);
await context.close();
process.exit(0);

function flagVal(name) {
  const i = process.argv.indexOf(name);
  return i !== -1 ? process.argv[i + 1] : undefined;
}
