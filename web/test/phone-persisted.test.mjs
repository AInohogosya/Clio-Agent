import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

/**
 * The first-launch flag the page cannot compute for itself.
 *
 * The page never sees the filesystem, so "is this a fresh install?" is the
 * bridge's answer to give: whether the shared config file exists at all. The
 * dev bridge has always said so; the served page's bridge once fell back to
 * "no answer yet", which reads as first launch — and so every restart arrived
 * as a fresh install asking for provider setup again. The flag is now on the
 * snapshot, and this keeps it there.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(HERE, '..', 'server', 'index.mjs');
const CONFIG_DIR = resolve(HERE, '..', '..', 'config');

let home = '';
let configFile = '';
let base = '';
let builtPage = '';
let child = null;
let port = 0;

/** A directory holding one real index.html, so the page check has a real answer. */
function makeBuild() {
  const dir = mkdtempSync(join(tmpdir(), 'clio-dist-'));
  writeFileSync(join(dir, 'index.html'), '<!doctype html><title>Clio Agent</title>');
  return dir;
}

function startBridge(overrides = {}) {
  return spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      CLIO_HOME: home,
      CLIO_CONFIG_DIR: CONFIG_DIR,
      CLIO_WEB_PORT: String(port),
      CLIO_PHONE_DIST: builtPage,
      ...overrides,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

async function freePort() {
  const { createServer } = await import('node:net');
  return new Promise((resolvePort) => {
    const probe = createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port: found } = probe.address();
      probe.close(() => resolvePort(found));
    });
  });
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'clio-persisted-'));
  configFile = join(home, 'phone-config.json');
  builtPage = makeBuild();
  port = await freePort();
  base = `http://127.0.0.1:${port}`;
  child = startBridge({ PROJECT_PHONE_CONFIG: configFile });
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
  if (builtPage) rmSync(builtPage, { recursive: true, force: true });
});

/** The page's own handshake: same-origin, as the served page would make it. */
async function handshake() {
  const response = await fetch(`${base}/__phone/session`, {
    headers: { origin: base, 'sec-fetch-site': 'same-origin' },
  });
  assert.equal(response.status, 200);
  return response.json();
}

test('a home with no config file reads as first launch', async () => {
  const { token } = await handshake();
  assert.equal(typeof token === 'string' && token.length > 0, true);
  const persisted = await handshake();
  assert.equal(persisted.state.persisted, false);
});

test('a home whose config file exists reads as not first launch', async () => {
  writeFileSync(configFile, JSON.stringify({ version: 2 }));
  const { state } = await handshake();
  assert.equal(state.persisted, true);
});
