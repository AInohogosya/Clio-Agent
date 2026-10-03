import { strict as assert } from 'node:assert';
import { createServer, request as httpRequest } from 'node:http';
import { connect } from 'node:net';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';
import { phoneBridge } from '../bridge-plugin.ts';
import { bridgePath, TOKEN_HEADER } from '../src/bridge-protocol.ts';

/**
 * The bridge, over real HTTP.
 *
 * The unit tests beside this file check the rules; this one checks that the
 * rules and the routes still fit together, which is the part that a refactor
 * can quietly break: a route renamed on one side only, a body limit that no
 * longer matches what the file accepts, a completion that stops being a
 * stream. None of that is visible from the pure functions.
 */

/** A provider the bridge can reach on loopback, with no credential. */
function startProvider(handler) {
  const received = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      received.push({ url: request.url, body: raw ? JSON.parse(raw) : null });
      handler(response, received.at(-1));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({
      received,
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
      close: () => new Promise((done) => server.close(done)),
    }));
  });
}

/** A Vite-shaped server the plugin's middleware can be mounted on. */
function mount(plugin) {
  let handler = null;
  plugin.configureServer({ middlewares: { use: (fn) => { handler = fn; } }, config: { logger: { info: () => undefined } } });
  // The middleware answers bridge routes itself and only calls `next()` for the
  // rest, so that is what decides whether this server still owes a response.
  const server = createServer((request, response) => {
    let passed = false;
    handler(request, response, () => { passed = true; });
    if (passed && !response.writableEnded) response.writeHead(404).end();
  });
  return { server, plugin };
}

function serve(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
}

async function withBridge(run, providerHandler, setup) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-bridge-'));
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = join(directory, 'config.json');
  const provider = await startProvider(providerHandler);
  const { plugin, server } = mount(phoneBridge());
  const origin = await serve(server);
  try {
    // The state is read when the bridge is first touched, so the configuration
    // has to be in place before the session request or the bridge would answer
    // from a snapshot taken before it existed.
    if (setup) await setup(provider);
    const session = await (await fetch(`${origin}${bridgePath('session')}`)).json();
    return await run({ origin, token: session.token, provider, plugin, server });
  } finally {
    await new Promise((done) => server.close(done));
    await provider.close();
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  }
}

/** Points the shared file at the stub provider, before the bridge reads it. */
async function configureStub(provider) {
  const { saveConfig } = await import('@project-phone/core/store');
  const { createSettings } = await import('@project-phone/core');
  saveConfig({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    settings: createSettings({ provider: 'ollama', model: 'stub-small', baseUrl: provider.baseUrl }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, { origin: 'cli' });
}

const json = (response, payload, status = 200) => {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(JSON.stringify(payload));
};

test('a completion is streamed back frame by frame', async () => {
  await withBridge(async ({ origin, token, provider }) => {
    const { readConfig } = await import('@project-phone/core/store');
    assert.equal(readConfig().settings.model, 'stub-small');

    const response = await fetch(`${origin}${bridgePath('complete')}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token, host: '127.0.0.1' },
      body: JSON.stringify({ prompt: 'are you there' }),
    });
    assert.equal(response.status, 200, 'a keyless local provider is allowed through');
    assert.match(response.headers.get('content-type') ?? '', /ndjson/);

    const body = await response.text();
    const frames = body.trim().split('\n').map((line) => JSON.parse(line));
    assert.ok(frames.some((frame) => frame.type === 'delta'), 'the answer arrives in pieces');
    assert.equal(frames.at(-1).type, 'done');
    assert.equal(frames.at(-1).text, 'the line is open');

    // The bridge really asked the provider, and it really signed nothing.
    const call = provider.received.at(-1);
    assert.match(call.url, /\/chat\/completions$/);
    assert.equal(call.body.stream, true);
  }, (response) => {
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'the line ' } }] })}\n\n`);
    response.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'is open' } }] })}\n\n`);
    response.write('data: [DONE]\n\n');
    response.end();
  }, configureStub);
});

test('a provider refusal is reported to the page as an error frame', async () => {
  await withBridge(async ({ origin, token }) => {
    const response = await fetch(`${origin}${bridgePath('complete')}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token },
      body: JSON.stringify({ prompt: 'are you there' }),
    });
    const frames = (await response.text()).trim().split('\n').map((line) => JSON.parse(line));
    const failure = frames.find((frame) => frame.type === 'error');
    assert.ok(failure, 'the page is told the request failed');
    assert.equal(typeof failure.error, 'string');
    assert.notEqual(failure.error, '');
  }, (response) => json(response, { error: { message: 'stub refused' } }, 503), configureStub);
});

test('a request without the token is refused before anything is read', async () => {
  await withBridge(async ({ origin }) => {
    const response = await fetch(`${origin}${bridgePath('state')}`);
    assert.equal(response.status, 401);
    const body = await response.json();
    assert.equal(body.error, 'unauthorized');
  }, (response) => json(response, {}));
});

test('a rebound host header is refused', async () => {
  await withBridge(async ({ origin, token }) => {
    // `fetch` will not let a caller forge `Host`, so the DNS-rebinding shape is
    // replayed over a raw socket: a loopback peer claiming an authority the
    // client controls, carrying a token that is otherwise perfectly good.
    const status = await rawRequest(origin, [
      `GET ${bridgePath('state')} HTTP/1.1`,
      'Host: attacker.example',
      `${TOKEN_HEADER}: ${token}`,
      'Connection: close',
      '', '',
    ].join('\r\n'));
    assert.equal(status, 403, 'the claimed authority decides, not the peer address');

    const loopback = await rawRequest(origin, [
      `GET ${bridgePath('state')} HTTP/1.1`,
      'Host: 127.0.0.1',
      `${TOKEN_HEADER}: ${token}`,
      'Connection: close',
      '', '',
    ].join('\r\n'));
    assert.equal(loopback, 200, 'the same request with a loopback authority is answered');
  }, (response) => json(response, {}));
});

/** Sends a hand-written request and returns the status line's code. */
function rawRequest(origin, raw) {
  const url = new URL(origin);
  return new Promise((resolve, reject) => {
    const socket = connect(Number(url.port), url.hostname, () => {
      socket.write(raw);
    });
    let received = '';
    socket.setEncoding('utf8');
    socket.on('data', (chunk) => { received += chunk; });
    socket.on('error', reject);
    socket.on('close', () => resolve(Number(received.split(' ')[1] ?? 0)));
  });
}

test('a cross-site origin is refused', async () => {
  await withBridge(async ({ origin, token }) => {
    const response = await fetch(`${origin}${bridgePath('state')}`, {
      headers: { [TOKEN_HEADER]: token, origin: 'https://attacker.example' },
    });
    assert.equal(response.status, 403);
  }, (response) => json(response, {}));
});

test('a state push that changes nothing does not rewrite the file', async () => {
  await withBridge(async ({ origin, token }) => {
    const { readConfig } = await import('@project-phone/core/store');
    const before = readConfig();
    const settings = before.settings;

    const post = () => fetch(`${origin}${bridgePath('state')}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token },
      body: JSON.stringify({ settings: { ...settings, apiKey: '' }, messages: [] }),
    });
    assert.equal((await post()).status, 200);
    assert.equal(readConfig().revision, before.revision, 'an identical push costs no write');

    await post();
    assert.equal(readConfig().revision, before.revision);

    const changed = await fetch(`${origin}${bridgePath('state')}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token },
      body: JSON.stringify({ settings: { ...settings, apiKey: '', theme: 'light' }, messages: [] }),
    });
    assert.equal(changed.status, 200);
    assert.equal(readConfig().revision, before.revision + 1, 'a real change still lands');
  }, (response) => json(response, {}), configureStub);
});

test('an unknown bridge route is a 404, not a crash', async () => {
  await withBridge(async ({ origin, token }) => {
    const response = await fetch(`${origin}/__phone/nope`, {
      headers: { [TOKEN_HEADER]: token },
    });
    assert.equal(response.status, 404);
  }, (response) => json(response, {}));
});

test('a body larger than the file can hold is refused, not truncated', async () => {
  await withBridge(async ({ origin, token }) => {
    // The declared length is refused before a byte is read, so the client can
    // claim a body far larger than the file can hold without sending one. The
    // old budget was a fraction of that, which is how a long conversation came
    // to stop syncing without saying so.
    const status = await new Promise((resolve, reject) => {
      const url = new URL(origin);
      const call = httpRequest({
        host: url.hostname,
        port: Number(url.port),
        method: 'POST',
        path: bridgePath('state'),
        headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token, 'content-length': '17000000' },
      }, (response) => {
        response.resume();
        response.on('end', () => resolve(response.statusCode));
      });
      call.on('error', reject);
      call.end('{}');
    });
    assert.equal(status, 413);
  }, (response) => json(response, {}));
});

test('an empty completion request is refused before the provider is asked', async () => {
  await withBridge(async ({ origin, token, provider }) => {
    const response = await fetch(`${origin}${bridgePath('complete')}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token },
      body: JSON.stringify({ prompt: '   ' }),
    });
    // No provider was asked, because the request never got that far.
    assert.equal(response.status, 400);
    assert.equal(provider.received.length, 0);
  }, (response) => json(response, {}));
});
