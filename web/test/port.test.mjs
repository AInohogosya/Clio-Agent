import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

/**
 * What the bridge says when it cannot have the port.
 *
 * The supervisor starts this process alongside an agent that may already have one
 * running, so the second bridge finding the port held is an ordinary outcome rather
 * than a fault — and it used to arrive as an uncaught `error` event: a Node stack
 * trace on stderr, filed under `ethos-web.log` where nobody reads it, and a child
 * the supervisor records as dead. The information that was actually worth having —
 * another bridge is serving the interface, and the page is right there — was in
 * none of it.
 *
 * So: one sentence, naming the port and the URL, and an exit that says "nothing to
 * do here" rather than "I failed". The first bridge must be untouched by it.
 *
 * "Nothing to do" is a claim about the page, so it is checked rather than assumed.
 * A port can be held by a bridge that started before the interface was built: it
 * answers its whole API and serves no page, and a start command that reported that
 * as success left a person reading a 404 and being told the interface was up. The
 * second bridge asks the port for the page, and takes "someone else already has it"
 * as an exit 0 only when the answer is really a page.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(HERE, '..', 'server', 'index.mjs');
const CONFIG_DIR = resolve(HERE, '..', '..', 'config');

let first = null;
let home = '';
let port = 0;
let base = '';
let builtPage = '';

/** A directory holding one real index.html, so "is the page served" has a real answer. */
function makeBuild() {
  const dir = mkdtempSync(join(tmpdir(), 'ethos-dist-'));
  writeFileSync(join(dir, 'index.html'), '<!doctype html><title>Clio Agent</title>');
  return dir;
}

async function freePort() {
  return new Promise((resolvePort) => {
    const probe = createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port: found } = probe.address();
      probe.close(() => resolvePort(found));
    });
  });
}

function startBridge(overrides = {}) {
  return spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      ETHOS_HOME: home,
      ETHOS_CONFIG_DIR: CONFIG_DIR,
      ETHOS_WEB_PORT: String(port),
      ETHOS_PHONE_DIST: builtPage,
      ...overrides,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'ethos-port-'));
  port = await freePort();
  base = `http://127.0.0.1:${port}`;
  builtPage = makeBuild();
  first = startBridge();
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
  first?.kill('SIGKILL');
  if (home) rmSync(home, { recursive: true, force: true });
  if (builtPage) rmSync(builtPage, { recursive: true, force: true });
});

/** Runs a second bridge to its end, and returns what it said and how it left. */
function runSecond() {
  return new Promise((done) => {
    const child = startBridge();
    let said = '';
    child.stdout.on('data', (chunk) => {
      said += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      said += chunk.toString();
    });
    child.on('close', (code) => done({ code, said }));
  });
}

test('a second bridge on a taken port says so and leaves quietly', async () => {
  const { code, said } = await runSecond();
  assert.match(said, new RegExp(`${port} is already in use`), said);
  assert.match(said, new RegExp(`http://127\\.0\\.0\\.1:${port}`), said);
  assert.doesNotMatch(said, /at .*node:internal/, 'a stack trace is not an explanation');
  assert.equal(code, 0, 'nothing failed here, so nothing should be reported as a failure');
});

test('the bridge already serving the port is untouched', async () => {
  const response = await fetch(`${base}/api/health`);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.ok, true);
});

test('the port is only reported as served when a page actually comes back', async () => {
  const response = await fetch(`${base}/`);
  assert.equal(response.status, 200, 'the bridge that holds the port serves the page');
  assert.match(response.headers.get('content-type') ?? '', /text\/html/);
});

test('a port held by something that serves no page is reported, not called a success', async () => {
  // The case that made `ethos up` and `ethos status` confidently wrong: a bridge
  // that took the port while the interface was unbuilt. It answers its whole API —
  // so every liveness check says the interface is up — and 404s the page.
  const emptyBuild = mkdtempSync(join(tmpdir(), 'ethos-dist-empty-'));
  const portWithoutPage = await freePort();
  const holder = startBridge({ ETHOS_PHONE_DIST: emptyBuild, ETHOS_WEB_PORT: String(portWithoutPage) });
  const holderBase = `http://127.0.0.1:${portWithoutPage}`;
  try {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        if ((await fetch(`${holderBase}/api/health`)).ok) break;
      } catch {
        /* not yet */
      }
      await new Promise((done) => setTimeout(done, 50));
    }

    const said = await new Promise((done) => {
      const child = startBridge({ ETHOS_PHONE_DIST: emptyBuild, ETHOS_WEB_PORT: String(portWithoutPage) });
      let output = '';
      child.stdout.on('data', (chunk) => { output += chunk.toString(); });
      child.stderr.on('data', (chunk) => { output += chunk.toString(); });
      child.on('close', (code) => done({ code, output }));
    });

    assert.match(said.output, new RegExp(`${portWithoutPage} is already in use`), said.output);
    assert.doesNotMatch(said.output, /Nothing to do/, 'a port with no page on it is not a success');
    assert.match(said.output, /not serving the interface|answered 503/, said.output);
    assert.equal(said.code, 1, 'a start that leaves the page unreachable has failed, however healthy the API is');
  } finally {
    holder.kill('SIGKILL');
    rmSync(emptyBuild, { recursive: true, force: true });
  }
});

test('a build that appears after startup is served without a restart', async () => {
  // The ordering this used to lose: the bridge was started first, the interface was
  // built afterwards, and the build was never picked up — the process had already
  // decided, once, that it would not serve a page.
  const lateBuild = mkdtempSync(join(tmpdir(), 'ethos-dist-late-'));
  const latePort = await freePort();
  const bridge = startBridge({ ETHOS_PHONE_DIST: lateBuild, ETHOS_WEB_PORT: String(latePort) });
  const lateBase = `http://127.0.0.1:${latePort}`;
  try {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        if ((await fetch(`${lateBase}/api/health`)).ok) break;
      } catch {
        /* not yet */
      }
      await new Promise((done) => setTimeout(done, 50));
    }

    const before = await fetch(`${lateBase}/`);
    assert.equal(before.status, 503, 'an unbuilt interface is reported, not served as a 404');
    assert.match(await before.text(), /not built yet/, 'and it says how to build it');

    writeFileSync(join(lateBuild, 'index.html'), '<!doctype html><title>Clio Agent</title>');

    const after = await fetch(`${lateBase}/`);
    assert.equal(after.status, 200, 'the page appears as soon as the build lands');
    assert.match(await after.text(), /Clio Agent/);
  } finally {
    bridge.kill('SIGKILL');
    rmSync(lateBuild, { recursive: true, force: true });
  }
});
