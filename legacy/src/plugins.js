import { readdirSync } from 'fs';
import { join }        from 'path';
import { fileURLToPath } from 'url';

const PLUGIN_DIR = join(fileURLToPath(import.meta.url), '../plugins');

const plugins = [];

export async function loadPlugins() {
  const files = readdirSync(PLUGIN_DIR).filter(f => f.endsWith('.js') && !f.startsWith('_'));
  for (const file of files) {
    const mod = await import(join(PLUGIN_DIR, file));
    plugins.push(mod.default);
    console.log(`[api-god] plugin loaded: ${mod.default.name}`);
  }
}

// Returns plugin-transformed records if a plugin matches, otherwise null.
export async function runOnResponse(url, body) {
  for (const plugin of plugins) {
    if (!plugin.match(url)) continue;
    if (!plugin.onResponse) continue;
    const result = await plugin.onResponse(url, body);
    if (result) return result;
  }
  return null;
}

// Returns plugin-transformed records for a WS frame, or null to fall through to raw save.
export async function runOnWebSocket(frame) {
  for (const plugin of plugins) {
    const matchFn = plugin.matchWS ?? plugin.match;
    if (!matchFn(frame.url)) continue;
    if (!plugin.onWebSocket) continue;
    const result = await plugin.onWebSocket(frame);
    if (result) return result;
  }
  return null;
}

// Called after a page finishes loading — plugins can inject fetch() calls via page.evaluate().
// Returns records to save, or null.
export async function runOnPageLoad(url, page) {
  for (const plugin of plugins) {
    if (!plugin.match(url)) continue;
    if (!plugin.onPageLoad) continue;
    try {
      const result = await plugin.onPageLoad(url, page);
      if (result) return result;
    } catch {}
  }
  return null;
}
