import Database from 'better-sqlite3';
import { mkdirSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(fileURLToPath(import.meta.url), '../../');
const DB_PATH = join(ROOT, 'data/captures.db');

mkdirSync(join(ROOT, 'data'), { recursive: true });

const db = new Database(DB_PATH);

db.exec(`
  CREATE TABLE IF NOT EXISTS captures (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    domain  TEXT    NOT NULL,
    type    TEXT    NOT NULL,
    url     TEXT,
    status  INTEGER,
    method  TEXT,
    data    TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_domain_ts ON captures(domain, ts);
  CREATE INDEX IF NOT EXISTS idx_type      ON captures(type);
`);

const insert = db.prepare(`
  INSERT INTO captures (domain, type, url, status, method, data)
  VALUES (@domain, @type, @url, @status, @method, @data)
`);

export function save(record) {
  insert.run({
    domain: record.domain ?? '',
    type:   record.type   ?? 'unknown',
    url:    record.url    ?? null,
    status: record.status ?? null,
    method: record.method ?? null,
    data:   typeof record.data === 'string' ? record.data : JSON.stringify(record.data ?? null),
  });
}

export function query({ domain, type, since, limit = 500 } = {}) {
  const conditions = [];
  const params = [];

  if (domain) { conditions.push('domain = ?'); params.push(domain); }
  if (type)   { conditions.push('type = ?');   params.push(type); }
  if (since)  { conditions.push('ts >= ?');    params.push(since); }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  return db.prepare(`SELECT * FROM captures ${where} ORDER BY ts DESC LIMIT ?`)
           .all(...params, limit);
}

export function stats() {
  return db.prepare(`
    SELECT domain, type, COUNT(*) as count, MAX(ts) as last_seen
    FROM captures GROUP BY domain, type ORDER BY count DESC
  `).all();
}
