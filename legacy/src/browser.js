import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(fileURLToPath(import.meta.url), '../../');
const SESSION_DIR = join(ROOT, 'data/session');

mkdirSync(SESSION_DIR, { recursive: true });

export async function launch() {
  const context = await chromium.launchPersistentContext(SESSION_DIR, {
    headless: false,
    viewport: null,
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-default-browser-check',
    ],
    ignoreHTTPSErrors: true,
  });

  const page = context.pages()[0] ?? await context.newPage();

  return { context, page };
}
