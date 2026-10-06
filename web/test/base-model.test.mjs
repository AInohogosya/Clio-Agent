import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { chmodSync, copyFileSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

/**
 * The bridge's base-model routes, over real HTTP.
 *
 * This is the only route on the bridge that writes a credential, so it is the
 * one where the rules and the routes have to be checked together: which things
 * are refused, what the file ends up containing, who is allowed to ask. The pure
 * rules are re-checked on the agent's side when it reads the file, so a mistake
 * here degrades to "the agent ignored it" rather than to a redirected agent —
 * but a mistake that silently ignores a person's settings is still a bug, and it
 * is only visible from out here.
 *
 * No database is needed: these routes touch the filesystem and nothing else, and
 * the process is expected to keep serving while Postgres is down.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(HERE, '..', 'server', 'index.mjs');
const CONFIG_DIR = resolve(HERE, '..', '..', 'config');

let child = null;
let home = '';
let base = '';

/** An ephemeral port, so a parallel run cannot collide with anything. */
async function freePort() {
  const { createServer } = await import('node:net');
  return new Promise((resolve) => {
    const probe = createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'ethos-bridge-'));
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [SERVER], {
    // A key in the environment for one vendor and one vendor's second name, so
    // the environment half of this file is checked against a real process rather
    // than against a stubbed one: the point is which variables *this* process can
    // see, and only this process can answer that.
    env: {
      ...process.env,
      ETHOS_HOME: home,
      ETHOS_CONFIG_DIR: CONFIG_DIR,
      ETHOS_WEB_PORT: String(port),
      OPENAI_API_KEY: 'sk-openai-from-env-1234',
      GOOGLE_API_KEY: 'google-from-env-5678',
      DEEPSEEK_API_KEY: '',
      ETHOS_TEST_VENDOR_KEY: 'sk-custom-vendor-4321',
    },
    stdio: 'ignore',
  });
  // Wait for the listener rather than a fixed sleep: a test that runs before the
  // server is up fails for a reason that has nothing to do with the code.
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${base}/api/model`);
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

const key = 'sk-or-v1-example-key-abcd1234';

function write(body, headers = {}) {
  return fetch(`${base}/api/model`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

function read() {
  return fetch(`${base}/api/model`);
}

// ------------------------------------------------------------------- writing

test('nothing configured reads as nothing', async () => {
  const body = await (await read()).json();
  assert.deepEqual(body, {
    configured: false, provider: '', protocol: '', model: '', base_url: '',
    key_present: false, key_hint: '', key_env: '', key_env_present: false,
  });
});

test('a base model is written, and the file belongs to its owner alone', async () => {
  const response = await write({
    provider: 'openrouter',
    model: 'stealth/space-bunny-alpha',
    base_url: 'https://openrouter.ai/api/v1',
    api_key: key,
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.configured, true);
  // The vendor a person picked, beside the adapter it maps to.
  assert.equal(body.provider, 'openrouter');
  assert.equal(body.protocol, 'openai_compat');
  assert.equal(body.key_present, true);
  assert.equal(body.key_hint, '••••1234');

  const file = join(home, 'base_model.yaml');
  assert.equal(statSync(file).mode & 0o777, 0o600, 'a key must not be readable by other accounts');
  const written = readFileSync(file, 'utf8');
  assert.match(written, /model: stealth\/space-bunny-alpha/);
  assert.match(written, new RegExp(`api_key: ${key}`));
});

test('the key is never echoed back', async () => {
  const raw = await (await read()).text();
  assert.equal(raw.includes(key), false);
  assert.match(raw, /"key_hint":"••••1234"/);
});

test('changing the model keeps the key that is already on file', async () => {
  await write({ provider: 'openrouter', model: 'openai/gpt-4o-mini' });
  const body = await (await read()).json();
  assert.equal(body.model, 'openai/gpt-4o-mini');
  assert.equal(body.key_present, true, 'an untouched key field means keep, not clear');
  assert.match(readFileSync(join(home, 'base_model.yaml'), 'utf8'), new RegExp(`api_key: ${key}`));
});

test('a key does not travel to a different vendor', async () => {
  // OpenRouter and Groq share the OpenAI-compatible protocol, so comparing
  // protocols would hand one company's credential to another company's endpoint.
  const response = await write({ provider: 'groq', model: 'llama-3.3-70b-versatile' });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: 'key_required' });
});

test('a key can be removed on purpose', async () => {
  const response = await write({ provider: 'openrouter', model: 'openai/gpt-4o-mini', clear_key: true });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: 'key_required' });
});

test('a provider that needs no key is set without one', async () => {
  const response = await write({ provider: 'ollama', model: 'llama3.2', base_url: 'http://127.0.0.1:11434/v1' });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.provider, 'ollama');
  assert.equal(body.key_present, false);
});

test('the catalogue can be restored', async () => {
  const response = await write({ enabled: false });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).configured, false);
  assert.throws(() => statSync(join(home, 'base_model.yaml')));
  // Removing what is not there is the state it was already in, not an error.
  assert.equal((await write({ enabled: false })).status, 200);
});

// ------------------------------------------------------------------ refusing

test('a provider the agent has no adapter for is refused', async () => {
  const response = await write({ provider: 'skynet', model: 'm', api_key: key });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: 'unknown_provider' });
});

test('a model name that is not one is refused', async () => {
  for (const model of ['', 'has space', '-leading', 'x'.repeat(257)]) {
    const response = await write({ provider: 'openai', model, api_key: key });
    assert.equal(response.status, 400, model.slice(0, 20));
    assert.deepEqual(await response.json(), { error: 'invalid_model' });
  }
});

test('an address the agent must not post its reasoning to is refused', async () => {
  const refused = [
    'http://api.openai.com/v1',
    'https://169.254.169.254/latest/meta-data',
    'https://192.168.1.10/v1',
    'https://evil.internal/v1',
    'https://user:pw@api.openai.com/v1',
    'file:///etc/passwd',
  ];
  for (const base_url of refused) {
    const response = await write({ provider: 'openai', model: 'gpt-4o-mini', base_url, api_key: key });
    assert.equal(response.status, 400, base_url);
    assert.deepEqual(await response.json(), { error: 'invalid_endpoint' });
  }
});

test('a refused write leaves the previous one alone', async () => {
  await write({ provider: 'openrouter', model: 'good/model', api_key: key });
  await write({ provider: 'openai', model: 'bad model', api_key: key });
  assert.equal((await (await read()).json()).model, 'good/model');
});

// ------------------------------------------------------------------- origin

test('another site cannot install one', async () => {
  // The bridge listens on loopback, which is exactly where a page in somebody's
  // browser can also reach. A cross-origin write would be a way to aim the
  // agent's reasoning at an address the page chose.
  for (const site of ['cross-site', 'same-site']) {
    const response = await write(
      { provider: 'openai', model: 'gpt-4o-mini', api_key: key },
      { 'sec-fetch-site': site },
    );
    assert.equal(response.status, 403, site);
    assert.deepEqual(await response.json(), { error: 'cross_origin_refused' });
  }
});

test('a foreign Origin is refused too', async () => {
  const response = await write(
    { provider: 'openai', model: 'gpt-4o-mini', api_key: key },
    { origin: 'http://evil.example' },
  );
  assert.equal(response.status, 403);
});

test('this origin, and a terminal with none, are the owner', async () => {
  const sameOrigin = await write(
    { provider: 'openai', model: 'gpt-4o-mini', api_key: key },
    { 'sec-fetch-site': 'same-origin', origin: base },
  );
  assert.equal(sameOrigin.status, 200);
  // No Origin and no Sec-Fetch-Site is curl or a terminal: the same owner, by a
  // route no browser can take.
  assert.equal((await write({ provider: 'openai', model: 'gpt-4o-mini', api_key: key })).status, 200);
});

test('a body that is not an object is refused rather than half-applied', async () => {
  for (const body of ['[]', '"text"', 'null']) {
    const response = await fetch(`${base}/api/model`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
    });
    assert.equal(response.status, 400, body);
  }
});

// ---------------------------------------------------------------- discovering

/**
 * A provider that answers `/models`, and records what it was sent.
 *
 * In this process, unlike the bridge, because nothing here blocks the event
 * loop: these tests `await` the bridge over HTTP rather than spawning a child
 * that has to be answered while the parent is stuck.
 */
function startProvider(handler) {
  const seen = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      seen.push({ url: request.url, headers: request.headers });
      handler(response, request);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({
      seen,
      baseUrl: `http://127.0.0.1:${server.address().port}`,
      close: () => new Promise((done) => server.close(done)),
    }));
  });
}

const openAiShape = (ids) => (response) => {
  response.writeHead(200, { 'content-type': 'application/json' })
    .end(JSON.stringify({ data: ids.map((id) => ({ id })) }));
};

async function discover(body) {
  const response = await fetch(`${base}/api/models`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

/** Puts a stored base model in place, so there is a key to carry over. */
async function store(provider, key, modelUrl) {
  await write({
    provider,
    model: 'stored/model',
    base_url: modelUrl,
    api_key: key,
  });
}

test('a catalogue is fetched from the provider, and the key never comes back', async () => {
  const provider = await startProvider(openAiShape(['b/2', 'a/1']));
  try {
    const { status, body } = await discover({
      provider: 'openrouter',
      base_url: provider.baseUrl,
      api_key: key,
    });
    assert.equal(status, 200);
    assert.equal(body.source, 'remote');
    // Sorted, so the list does not reshuffle between two fetches of the same thing.
    assert.deepEqual(body.models, ['a/1', 'b/2']);
    assert.equal(JSON.stringify(body).includes(key), false);
  } finally {
    await provider.close();
  }
});

test('an id the agent could not use is not offered', async () => {
  // Better a short list than a long one of names that would be chosen and then
  // silently refused when the agent read the file back.
  const provider = await startProvider(openAiShape([
    'vendor/good-model',
    'has a space',
    'new\nline',
    '-leading-dash',
    'x'.repeat(257),
  ]));
  try {
    const { body } = await discover({ provider: 'openrouter', base_url: provider.baseUrl, api_key: key });
    assert.deepEqual(body.models, ['vendor/good-model']);
  } finally {
    await provider.close();
  }
});

test('each vendor is sent the credential its own API expects', async () => {
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    // The header each API expects, and the value it expects in it. A bearer
    // header carries a scheme and the two that do not must not have one, or the
    // vendor answers 400 rather than 401 and the reason is unreadable.
    const expected = {
      openai: (h, k) => h.authorization === `Bearer ${k}` && h['x-api-key'] === undefined,
      openrouter: (h, k) => h.authorization === `Bearer ${k}`,
      groq: (h, k) => h.authorization === `Bearer ${k}`,
      deepseek: (h, k) => h.authorization === `Bearer ${k}`,
      mistral: (h, k) => h.authorization === `Bearer ${k}`,
      xai: (h, k) => h.authorization === `Bearer ${k}`,
      anthropic: (h, k) => h['x-api-key'] === k && h.authorization === undefined,
      gemini: (h, k) => h['x-goog-api-key'] === k && h.authorization === undefined,
    };
    for (const [vendor, accepts] of Object.entries(expected)) {
      provider.seen.length = 0;
      const sentKey = `sk-${vendor}`;
      await discover({ provider: vendor, base_url: provider.baseUrl, api_key: sentKey });
      const sent = provider.seen.at(-1);
      assert.ok(accepts(sent.headers, sentKey), `${vendor}: ${JSON.stringify(sent.headers.authorization ?? sent.headers['x-api-key'] ?? sent.headers['x-goog-api-key'])}`);
      assert.equal(sent.url, '/models');
    }
    // Anthropic also needs its version header, or it answers 400.
    provider.seen.length = 0;
    await discover({ provider: 'anthropic', base_url: provider.baseUrl, api_key: 'sk' });
    assert.equal(provider.seen.at(-1).headers['anthropic-version'], '2023-06-01');
  } finally {
    await provider.close();
  }
});

test('a local provider is sent no credential at all', async () => {
  // Some local servers answer an unexpected Authorization with a 401, and it
  // has no use for one.
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    for (const vendor of ['ollama', 'lmstudio']) {
      provider.seen.length = 0;
      await discover({ provider: vendor, base_url: provider.baseUrl, api_key: 'irrelevant' });
      const sent = provider.seen.at(-1).headers;
      assert.equal(sent.authorization, undefined, vendor);
      assert.equal(sent['x-api-key'], undefined, vendor);
    }
  } finally {
    await provider.close();
  }
});

test('the request carries no cookie and no referrer', async () => {
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: key });
    const sent = provider.seen.at(-1).headers;
    assert.equal(sent.cookie, undefined);
    assert.equal(sent.referer, undefined);
  } finally {
    await provider.close();
  }
});

test('a stored key is used when none is typed, for the same vendor only', async () => {
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    await store('openrouter', 'sk-stored', provider.baseUrl);
    await discover({ provider: 'openrouter', base_url: provider.baseUrl });
    assert.equal(provider.seen.at(-1).headers.authorization, 'Bearer sk-stored');

    provider.seen.length = 0;
    const other = await discover({ provider: 'groq', base_url: provider.baseUrl });
    // A different vendor must not inherit it, even though both speak the same
    // protocol: that would post one company's credential to another.
    assert.equal(other.body.source, 'offline');
    assert.equal(other.body.error, 'missing_credentials');
    assert.equal(provider.seen.length, 0, 'the provider was never troubled with it');
  } finally {
    await provider.close();
  }
});

test("gemini's shape is read, and its models/ prefix stripped", async () => {
  const provider = await startProvider((response) => {
    response.writeHead(200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ models: [{ name: 'models/gemini-2.5-pro' }, { name: 'models/gemini-2.0-flash' }] }));
  });
  try {
    const { body } = await discover({ provider: 'gemini', base_url: provider.baseUrl, api_key: key });
    assert.equal(body.source, 'remote');
    assert.deepEqual(body.models, ['gemini-2.0-flash', 'gemini-2.5-pro']);
  } finally {
    await provider.close();
  }
});

test('a redirect is refused rather than followed with the key attached', async () => {
  const provider = await startProvider((response) => {
    response.writeHead(302, { location: 'http://elsewhere.example/models' });
    response.end();
  });
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: key });
    assert.equal(body.source, 'offline');
    assert.equal(body.error, 'redirect_refused');
  } finally {
    await provider.close();
  }
});

test('every way of failing answers with a list and a reason, and never throws', async () => {
  const shapes = {
    network: { provider: 'openai', base_url: 'http://127.0.0.1:9', api_key: key },
    invalid_endpoint: { provider: 'openai', base_url: 'https://192.168.1.10/v1', api_key: key },
    missing_credentials: { provider: 'groq', base_url: 'http://127.0.0.1:9' },
    unknown_provider: { provider: 'skynet' },
  };
  for (const [reason, body] of Object.entries(shapes)) {
    const { status, body: answer } = await discover(body);
    assert.equal(status, 200, reason);
    assert.equal(answer.source, 'offline', reason);
    assert.ok(Array.isArray(answer.models), reason);
    if (reason !== 'unknown_provider') assert.ok(answer.models.length > 0, `${reason} has a fallback`);
  }
});

test('a provider that answers 401 says so rather than pretending', async () => {
  const provider = await startProvider((response) => {
    response.writeHead(401);
    response.end('no');
  });
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: 'wrong' });
    assert.equal(body.source, 'offline');
    assert.equal(body.error, 'http_401');
  } finally {
    await provider.close();
  }
});

test('a provider that answers 200 with nothing in it is an empty catalogue', async () => {
  const provider = await startProvider((response) => {
    response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ data: [] }));
  });
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: key });
    assert.equal(body.source, 'offline');
    assert.equal(body.error, 'empty_catalog');
  } finally {
    await provider.close();
  }
});

test('a catalogue full of unusable names is an empty catalogue too', async () => {
  const provider = await startProvider((response) => {
    response.writeHead(200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ data: [{ id: 'has a space' }, { nope: 1 }, 'string'] }));
  });
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: key });
    assert.equal(body.error, 'empty_catalog');
  } finally {
    await provider.close();
  }
});

test('a body that is not a list at all is an empty catalogue, not a crash', async () => {
  const provider = await startProvider((response) => {
    response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(['a', 'b']));
  });
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: key });
    assert.equal(body.error, 'empty_catalog');
  } finally {
    await provider.close();
  }
});

test('another site cannot use this as a probe', async () => {
  // It is a request this process makes on somebody's behalf, to an address they
  // supplied, with a credential they supplied.
  const response = await fetch(`${base}/api/models`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'sec-fetch-site': 'cross-site' },
    body: JSON.stringify({ provider: 'openai', api_key: key }),
  });
  assert.equal(response.status, 403);
});

// --------------------------------------------------------- the environment
//
// A person who keeps their keys in their shell has already done the hard part,
// and this process is the only one that can see it: a browser has no
// environment at all. So the key is read here, named to the page and never
// valued, and using it is a decision the page asks for rather than a thing it
// can do by itself.

const ENV_KEY = 'sk-openai-from-env-1234';

function readEnvKeys() {
  return fetch(`${base}/api/env-keys`).then((response) => response.json());
}

test('every provider is reported, whether or not it has a key', async () => {
  // Every vendor, not only the ones with something in them: a form can only
  // offer the button for a provider somebody has already picked, so the answer
  // to "which providers could I use" cannot depend on the current selection.
  // The quick picks lead, in their order on the screen, and the hosted list
  // follows them.
  const { keys } = await readEnvKeys();
  assert.equal(keys.length, 111);
  assert.deepEqual(keys.map((entry) => entry.provider).slice(0, 10), [
    'openai', 'anthropic', 'gemini', 'openrouter', 'deepseek',
    'mistral', 'groq', 'xai', 'ollama', 'lmstudio',
  ]);
  for (const entry of keys) {
    assert.ok(Array.isArray(entry.variables) && entry.variables.length, entry.provider);
    assert.equal(typeof entry.present, 'boolean', entry.provider);
  }
});

test('a key in the environment is reported as a name and a hint, never a value', async () => {
  const raw = await (await fetch(`${base}/api/env-keys`)).text();
  assert.equal(raw.includes(ENV_KEY), false, 'a page that could read the key could post it');
  const { keys } = JSON.parse(raw);
  const openai = keys.find((entry) => entry.provider === 'openai');
  assert.deepEqual(openai, {
    provider: 'openai',
    variables: ['OPENAI_API_KEY'],
    present: true,
    variable: 'OPENAI_API_KEY',
    hint: '••••1234',
  });
  // A variable that is set to nothing is not a key, and is not offered as one.
  assert.equal(keys.find((entry) => entry.provider === 'deepseek').present, false);
  // A vendor with two names reports the one that is set, so the button can name
  // the variable the person actually exported.
  const gemini = keys.find((entry) => entry.provider === 'gemini');
  assert.equal(gemini.variable, 'GOOGLE_API_KEY');
  assert.equal(gemini.hint, '••••5678');
});

test('the environment is spent on a catalogue when nothing else is given', async () => {
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    const { status, body } = await discover({ provider: 'openai', base_url: provider.baseUrl });
    assert.equal(status, 200);
    assert.equal(body.source, 'remote');
    assert.equal(body.key_source, 'environment');
    assert.equal(provider.seen.at(-1).headers.authorization, `Bearer ${ENV_KEY}`);
  } finally {
    await provider.close();
  }
});

test('a key just typed is the one the catalogue is fetched with', async () => {
  // The whole point of the change: what somebody pasted is what the request goes
  // out with, not the variable that happens to be set on this machine.
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl, api_key: 'sk-typed-9999' });
    assert.equal(body.source, 'remote');
    assert.equal(body.key_source, 'typed');
    assert.equal(provider.seen.at(-1).headers.authorization, 'Bearer sk-typed-9999');
  } finally {
    await provider.close();
  }
});

test('a vendor with no key anywhere is still told it has none', async () => {
  // The environment is a last resort, not a blanket: a vendor with nothing in it
  // must not be sent a request on the strength of somebody else's key.
  const { body } = await discover({ provider: 'groq', base_url: 'http://127.0.0.1:9' });
  assert.equal(body.source, 'offline');
  assert.equal(body.error, 'missing_credentials');
  assert.equal(body.key_source, 'none');
});

test('a base model can be saved against the variable rather than a key', async () => {
  const response = await write({ provider: 'openai', model: 'gpt-4o-mini', use_env: true });
  assert.equal(response.status, 200);
  const body = await response.json();
  // The name is the record; the value is not, anywhere.
  assert.equal(body.key_env, 'OPENAI_API_KEY');
  assert.equal(body.key_env_present, true);
  assert.equal(body.key_present, false);
  assert.equal(body.key_hint, '');
  const written = readFileSync(join(home, 'base_model.yaml'), 'utf8');
  assert.match(written, /api_key_env: OPENAI_API_KEY/);
  assert.equal(written.includes(ENV_KEY), false);
  assert.equal(statSync(join(home, 'base_model.yaml')).mode & 0o777, 0o600);
});

test('a variable the provider has not got is refused, and nothing is written', async () => {
  const response = await write({ provider: 'mistral', model: 'mistral-small-latest', use_env: true });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: 'env_key_missing' });
  assert.match(readFileSync(join(home, 'base_model.yaml'), 'utf8'), /api_key_env: OPENAI_API_KEY/);
});

test('changing the model keeps reading the same variable', async () => {
  // The reference belongs to the vendor, not to one model of it, and dropping it
  // on every save would mean a model change silently cost the agent its key.
  const response = await write({ provider: 'openai', model: 'gpt-4o' });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).key_env, 'OPENAI_API_KEY');
});

test('a key typed over a variable replaces it rather than hiding behind it', async () => {
  // The gateway prefers the environment, so a file holding both would keep using
  // the variable and quietly ignore what was just typed.
  const response = await write({ provider: 'openai', model: 'gpt-4o', api_key: 'sk-typed-1234' });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.key_present, true);
  assert.equal(body.key_env, '');
  const written = readFileSync(join(home, 'base_model.yaml'), 'utf8');
  assert.match(written, /api_key: sk-typed-1234/);
  assert.match(written, /api_key_env: ''/);
});

test('the variable a stored base model names is the one a catalogue is fetched with', async () => {
  // A hand-written file may name a variable this table knows nothing about, and
  // the agent honours whatever the file says. Reading it here is what keeps the
  // form and the agent from disagreeing about whether a model is reachable.
  writeFileSync(join(home, 'base_model.yaml'), [
    'vendor: openai',
    'provider: openai',
    'model: gpt-4o',
    'base_url: https://api.openai.com/v1',
    "api_key: ''",
    'api_key_env: ETHOS_TEST_VENDOR_KEY',
    'tiers: [T1]',
  ].join('\n'), { mode: 0o600 });
  const provider = await startProvider(openAiShape(['a/1']));
  try {
    const { body } = await discover({ provider: 'openai', base_url: provider.baseUrl });
    assert.equal(body.source, 'remote');
    assert.equal(provider.seen.at(-1).headers.authorization, 'Bearer sk-custom-vendor-4321');
    // And the stored file says so, so the form can name it under the field.
    assert.equal((await (await read()).json()).key_env, 'ETHOS_TEST_VENDOR_KEY');
  } finally {
    await provider.close();
  }
});

test('a named variable this process cannot see is reported as absent, not as a key', async () => {
  // The file and the process disagree often enough — a file written by one shell
  // and read by another — and reporting a credential that would not be there is
  // how a form ends up promising a key it cannot deliver.
  writeFileSync(join(home, 'base_model.yaml'), [
    'vendor: openai', 'provider: openai', 'model: gpt-4o',
    "api_key: ''", 'api_key_env: NOT_EXPORTED_HERE',
  ].join('\n'), { mode: 0o600 });
  const body = await (await read()).json();
  assert.equal(body.key_env, 'NOT_EXPORTED_HERE');
  assert.equal(body.key_env_present, false);
  assert.equal(body.key_present, false);
});

// ------------------------------------------------------- where the agent lives
//
// `paths.home` in `config/ethos.yaml` is `~/.ethos`, and the agent expands that
// with `os.path.expanduser`. This bridge has to land on the same directory, or it
// reads and writes a file the agent never looks at — and a base model saved into
// the wrong place fails in a way that says nothing about where the wrong place
// was. So the expansion is checked against a home of our own, with no
// `ETHOS_HOME` to short-circuit it.

/** A bridge with a `HOME` we chose, so nothing here can touch a real account. */
async function startWithHome(configDir, home) {
  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const started = spawn(process.execPath, [SERVER], {
    env: { ...process.env, HOME: home, ETHOS_CONFIG_DIR: configDir, ETHOS_WEB_PORT: String(port) },
    stdio: 'ignore',
  });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      if ((await fetch(`${origin}/api/model`)).ok) return { origin, stop: () => started.kill('SIGKILL') };
    } catch {
      /* not yet */
    }
    await new Promise((done) => setTimeout(done, 50));
  }
  started.kill('SIGKILL');
  throw new Error('the bridge did not start');
}

/** A config directory carrying only `paths.home`, and the config it needs to run. */
function configWithHome(value) {
  const dir = mkdtempSync(join(tmpdir(), 'ethos-bridge-config-'));
  writeFileSync(join(dir, 'ethos.yaml'), `paths:\n  home: ${value}\n`, 'utf8');
  for (const name of ['permissions.yaml', 'models.yaml', 'rhythm.yaml']) {
    copyFileSync(join(CONFIG_DIR, name), join(dir, name));
  }
  return dir;
}

const saveTo = (origin, body) => fetch(`${origin}/api/model`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

const temporary = (label) => mkdtempSync(join(tmpdir(), `ethos-bridge-${label}-`));
const discard = (...paths) => paths.forEach((path) => rmSync(path, { recursive: true, force: true }));

test('a tilde in paths.home is the home directory, not the root of the filesystem', async () => {
  // The regression this whole block exists for. Stripping the `~` off `~/.ethos`
  // leaves `/.ethos`, and `path.resolve` reads that as absolute — so the bridge
  // addressed a path at the root of the disk, could not create it, and answered a
  // save with an error naming neither the path nor the problem.
  const configDir = configWithHome('~/.ethos');
  const fakeHome = temporary('home');
  const bridge = await startWithHome(configDir, fakeHome);
  try {
    const response = await saveTo(bridge.origin, { provider: 'openrouter', model: 'a/b', api_key: key });
    assert.equal(response.status, 200, await response.text());
    const file = join(fakeHome, '.ethos', 'base_model.yaml');
    assert.ok(statSync(file).isFile(), `expected ${file} to exist`);
    assert.equal((await (await fetch(`${bridge.origin}/api/model`)).json()).model, 'a/b');
  } finally {
    bridge.stop();
    discard(configDir, fakeHome);
  }
});

test('the same file is read back on the next run, because it is the agent file', async () => {
  // Across processes, which is the point: the file has to be the one the gateway
  // opens, so a base model saved in one session is still there in the next rather
  // than appearing to have been lost — or, worse, having gone somewhere else.
  const configDir = configWithHome('~/.ethos');
  const fakeHome = temporary('home');
  try {
    const first = await startWithHome(configDir, fakeHome);
    try {
      assert.equal((await saveTo(first.origin, { provider: 'openrouter', model: 'kept/model', api_key: key })).status, 200);
    } finally {
      first.stop();
    }
    const second = await startWithHome(configDir, fakeHome);
    try {
      const body = await (await fetch(`${second.origin}/api/model`)).json();
      assert.equal(body.configured, true);
      assert.equal(body.model, 'kept/model');
    } finally {
      second.stop();
    }
  } finally {
    discard(configDir, fakeHome);
  }
});

test('a tilde on its own is the home directory itself', async () => {
  // Quoted, because a bare `~` is null in YAML and means "unset" rather than a
  // path; a hand-edited file that means the home says so in quotes.
  const configDir = configWithHome('"~"');
  const fakeHome = temporary('home');
  const bridge = await startWithHome(configDir, fakeHome);
  try {
    assert.equal((await saveTo(bridge.origin, { provider: 'openrouter', model: 'a/b', api_key: key })).status, 200);
    assert.ok(statSync(join(fakeHome, 'base_model.yaml')).isFile());
  } finally {
    bridge.stop();
    discard(configDir, fakeHome);
  }
});

test('an absolute paths.home is taken as it is written', async () => {
  // The one form that needs no expansion at all, and the one a deployment sets
  // when its agent lives somewhere other than the account's home.
  const elsewhere = temporary('elsewhere');
  const configDir = configWithHome(elsewhere);
  const bridge = await startWithHome(configDir, temporary('home'));
  try {
    assert.equal((await saveTo(bridge.origin, { provider: 'openrouter', model: 'a/b', api_key: key })).status, 200);
    assert.ok(statSync(join(elsewhere, 'base_model.yaml')).isFile());
  } finally {
    bridge.stop();
    discard(configDir, elsewhere);
  }
});

test('a home that is the filesystem root is not used', async () => {
  // Refused rather than obeyed. A home of `/` is the signature of the expansion
  // having gone wrong, and carrying on would mean writing into a sealed system
  // volume; the answer is the conventional location instead, which somebody can
  // see and correct, rather than a path at the root of the disk.
  const configDir = configWithHome('/');
  const fakeHome = temporary('home');
  const bridge = await startWithHome(configDir, fakeHome);
  try {
    assert.equal((await saveTo(bridge.origin, { provider: 'openrouter', model: 'a/b', api_key: key })).status, 200);
    assert.ok(statSync(join(fakeHome, '.ethos', 'base_model.yaml')).isFile());
    assert.throws(() => statSync('/.ethos'));
  } finally {
    bridge.stop();
    discard(configDir, fakeHome);
  }
});

test('a home this process cannot write is refused with something to act on', async (t) => {
  // Thrown, this arrives as a 503, which reads as "the agent is not running" —
  // advice to start something that is already running, for a problem that is a
  // directory. Named, it is the one thing somebody can go and look at.
  if (typeof process.getuid === 'function' && process.getuid() === 0) {
    t.skip('root writes anywhere');
    return;
  }
  const readonly = temporary('readonly');
  const configDir = configWithHome(readonly);
  const bridge = await startWithHome(configDir, temporary('home'));
  try {
    chmodSync(readonly, 0o500);
    const response = await saveTo(bridge.origin, { provider: 'openrouter', model: 'a/b', api_key: key });
    assert.equal(response.status, 400);
    const body = await response.json();
    assert.equal(body.error, 'home_unwritable');
    assert.match(body.detail, new RegExp(readonly.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  } finally {
    bridge.stop();
    chmodSync(readonly, 0o700);
    discard(configDir, readonly);
  }
});
