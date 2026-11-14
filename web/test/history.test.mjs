import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

/**
 * `GET /api/messages`, over real HTTP.
 *
 * The route is how a surface reads the past: `/api/snapshot` carries the last
 * hundred rows across every door, which is the live conversation and not the
 * history, and a person who wants what was said last week asks this route for
 * the page older than the oldest line they are looking at.
 *
 * The suite runs against an unreachable database on purpose, the way the
 * channels suite does — the contract that needs no database is the one worth
 * pinning here: a malformed `before` is a 400 about the cursor and not a 500
 * about Postgres, and a database the bridge cannot reach is a 503 that says
 * `agent_unavailable` rather than a hang.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(HERE, '..', 'server', 'index.mjs');
const CONFIG_DIR = resolve(HERE, '..', '..', 'config');

let child = null;
let home = '';
let base = '';

async function freePort() {
  const { createServer } = await import('node:net');
  return new Promise((done) => {
    const probe = createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => done(port));
    });
  });
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'clio-history-'));
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      CLIO_HOME: home,
      CLIO_CONFIG_DIR: CONFIG_DIR,
      CLIO_WEB_PORT: String(port),
      CLIO_DSN: "postgresql://ethos:ethos@127.0.0.1:1/ethos",
    },
    stdio: 'ignore',
  });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${base}/api/health`);
      if (response.ok) return;
    } catch {
      /* not yet */
    }
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error('the bridge did not start');
});

after(() => {
  child?.kill('SIGKILL');
  if (home) rmSync(home, { recursive: true, force: true });
});

test('a malformed before is a 400 about the cursor', async () => {
  const response = await fetch(`${base}/api/messages?before=not-a-date`);
  assert.equal(response.status, 400);
  const body = await response.json();
  assert.equal(body.error, 'invalid_before');
});

test('a database the bridge cannot reach is 503 agent_unavailable', async () => {
  const response = await fetch(`${base}/api/messages`);
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.error, 'agent_unavailable');
});
