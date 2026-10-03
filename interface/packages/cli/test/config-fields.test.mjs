import { spawn, spawnSync } from 'node:child_process';
import { strict as assert } from 'node:assert';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { readConfig, saveConfig } from '../dist/config.js';

const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));

function withConfig(run) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-cfg-'));
  const previousConfig = process.env.PROJECT_PHONE_CONFIG;
  const previousKey = process.env.PROJECT_PHONE_API_KEY;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  const restore = () => {
    if (previousConfig === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previousConfig;
    if (previousKey === undefined) delete process.env.PROJECT_PHONE_API_KEY;
    else process.env.PROJECT_PHONE_API_KEY = previousKey;
    rmSync(directory, { recursive: true, force: true });
  };
  const phone = (args, options = {}) => spawnSync(
    process.execPath,
    [entry, ...args],
    { encoding: 'utf8', env: { ...process.env, ...(options.env ?? {}) }, timeout: 20_000 },
  );
  let result;
  try {
    result = run({ directory, path, phone });
  } catch (error) {
    restore();
    throw error;
  }
  if (result && typeof result.then === 'function') return result.finally(restore);
  restore();
  return result;
}

const storedSettings = (path) => JSON.parse(readFileSync(path, 'utf8')).settings;

test('changing provider from the command line drops the old provider key', () => {
  withConfig(({ path, phone }) => {
    // A key is only ever written to the file by the setup wizard or the browser
    // bridge, so this is how one ends up sitting next to a provider.
    //
    // Direct mode, so this is only about the file: in agent mode the same write
    // is also handed to the agent, and a provider change with no key is refused
    // there — which is the subject of its own tests below.
    const state = readConfig();
    saveConfig({ ...state, settings: { ...state.settings, interface: 'direct', apiKey: 'sk-openai-secret' } }, { origin: 'web' });
    assert.equal(storedSettings(path).apiKey, 'sk-openai-secret');

    const result = phone(['config', 'set', 'provider', 'anthropic']);
    assert.equal(result.status, 0, result.stderr);

    const settings = storedSettings(path);
    assert.equal(settings.provider, 'anthropic');
    // The endpoint, the model and the key all belong to the provider that was
    // just left behind; keeping the key would post it to another vendor.
    assert.equal(settings.apiKey, '');
    assert.equal(settings.baseUrl, 'https://api.anthropic.com/v1');
    assert.equal(settings.model, 'claude-3-5-sonnet-latest');
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).credential.present, false);
    assert.equal(readFileSync(path, 'utf8').includes('sk-openai-secret'), false);
  });
});

test('a session key is never carried into another provider either', () => {
  withConfig(({ path, phone }) => {
    phone(['config', 'set', 'provider', 'ollama'], { env: { PROJECT_PHONE_API_KEY: 'sk-session-secret' } });
    // An environment credential never reaches the file in the first place.
    assert.equal(storedSettings(path).apiKey, '');
  });
});

test('the provider cannot be reset, and says so instead of calling it unknown', () => {
  withConfig(({ phone }) => {
    const result = phone(['config', 'unset', 'provider']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /provider cannot be reset/);
    assert.doesNotMatch(result.stderr, /Unknown field/);

    // A field that genuinely does not exist still says exactly that.
    const unknown = phone(['config', 'unset', 'nonsense']);
    assert.equal(unknown.status, 1);
    assert.match(unknown.stderr, /Unknown field nonsense/);
  });
});

test('every field can be read back as plain text', () => {
  withConfig(({ phone }) => {
    const expected = {
      provider: 'openai',
      model: 'gpt-4o-mini',
      baseUrl: 'https://api.openai.com/v1',
      language: 'en',
      theme: 'dark',
      accent: '#F97316',
      apiKey: '',
    };
    for (const [field, value] of Object.entries(expected)) {
      assert.equal(phone(['config', 'get', field]).stdout.trim(), value, field);
    }
  });
});

// ------------------------------------------------- the agent's base model

/**
 * A stand-in for the agent's interface service, answering only `/api/model`.
 *
 * In a process of its own, and that is not incidental: these tests drive the CLI
 * with `spawnSync`, which blocks this process's event loop, so a server running
 * here could never answer the connection the child is waiting on. Requests go to
 * a file rather than into memory for the same reason — the parent cannot ask a
 * question while it is blocked.
 *
 * `mode` is what the agent would have said, because the point of these tests is
 * that the terminal *reports what the agent said* rather than a status code.
 */
const AGENT_STUB = `
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
// With -e there is no script path in argv, so the first element after the
// executable is the log file rather than the second.
const [, logPath, mode] = process.argv;
writeFileSync(logPath, '');
const OK = {
  configured: true, provider: 'openai', protocol: 'openai', model: 'gpt-4o-mini',
  base_url: 'https://api.openai.com/v1', key_present: true, key_hint: '\u2022\u2022\u2022\u20221234',
};
const REASONS = {
  ok: null,
  endpoint: { status: 400, body: { error: 'invalid_endpoint' } },
  key: { status: 400, body: { error: 'key_required' } },
  model: { status: 400, body: { error: 'invalid_model' } },
  missing: { status: 404, body: { error: 'not found' } },
};
const server = createServer((request, response) => {
  const chunks = [];
  request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', () => {
    const raw = Buffer.concat(chunks).toString('utf8');
    appendFileSync(logPath, JSON.stringify({
      url: request.url,
      body: raw ? JSON.parse(raw) : null,
    }) + '\\n');
    if (request.url !== '/api/model') {
      response.writeHead(404).end();
      return;
    }
    const refusal = REASONS[mode];
    const reply = refusal ?? { status: 200, body: OK };
    response.writeHead(reply.status, { 'content-type': 'application/json' }).end(JSON.stringify(reply.body));
  });
});
// The port is announced once the listener is up, so a test never races the
// server the way a fixed sleep would.
server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port)));
`;

/** Starts the stub in its own process and resolves to a handle on it. */
function startAgent(mode = 'ok') {
  const directory = mkdtempSync(join(tmpdir(), 'phone-agent-'));
  const log = join(directory, 'requests.log');
  // The port is announced on stdout once the listener is up, so a test never
  // races the server the way a fixed sleep would.
  const child = spawn(process.execPath, ['--input-type=module', '-e', AGENT_STUB, log, mode], {
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  return new Promise((resolve, reject) => {
    child.stdout.once('data', (chunk) => resolve({
      url: `http://127.0.0.1:${String(chunk).trim()}`,
      received: () => {
        const raw = readFileSync(log, 'utf8').trim();
        return raw ? raw.split('\n').map((line) => JSON.parse(line)) : [];
      },
      close: () => { child.kill('SIGKILL'); rmSync(directory, { recursive: true, force: true }); },
    }));
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`the stub exited with ${code}`)));
  });
}

/** Points a fresh config at the stub, in whichever interface the test wants. */
function aimAtAgent(path, agent, iface) {
  const state = readConfig();
  saveConfig({
    ...state,
    settings: { ...state.settings, interface: iface, agentUrl: agent.url },
  }, { origin: 'web' });
}

test('in agent mode a model is handed to the agent, not only to the file', async () => {
  const agent = await startAgent();
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'agent');
      const result = phone(['config', 'set', 'model', 'gpt-4.1']);
      assert.equal(result.status, 0, result.stdout + result.stderr);
      assert.equal(storedSettings(path).model, 'gpt-4.1', 'the shared file is written too');
      const sent = agent.received().at(-1);
      assert.equal(sent.url, '/api/model');
      assert.equal(sent.body.model, 'gpt-4.1');
      assert.equal('api_key' in sent.body, false, 'the file holds no key, so none is sent');
    });
  } finally {
    agent.close();
  }
});

test('a base model the agent refuses is reported, and the exit code says so', async () => {
  // A model name is the one base-model field the terminal does not pre-validate,
  // so this is a refusal it can only learn from the agent. The file is written
  // either way and is what this surface believes in; what did not happen is the
  // part the agent depends on, and reporting success there would be a setting
  // that looks configured and is not.
  const agent = await startAgent('model');
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'agent');
      const result = phone(['config', 'set', 'model', 'has space']);
      assert.equal(result.status, 1);
      assert.match(result.stdout, /not a usable model name/i);
      assert.equal(storedSettings(path).model, 'has space', 'the shared file still records it');
    });
  } finally {
    agent.close();
  }
});

test('an address the agent must not be given is refused before it is asked', async () => {
  // The two ends apply the same rule, so this is caught here and the agent is
  // never troubled with it. Asserting the stub was *not* called is the point:
  // it is what makes "the two rules agree" a fact rather than a hope.
  const agent = await startAgent();
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'agent');
      for (const baseUrl of ['https://192.168.1.10/v1', 'https://169.254.169.254/latest', 'http://api.openai.com/v1']) {
        const result = phone(['config', 'set', 'baseUrl', baseUrl]);
        assert.equal(result.status, 1, baseUrl);
        assert.match(result.stderr, /Invalid value for baseUrl/i);
      }
      assert.equal(agent.received().length, 0, 'the agent is not asked about an address it would refuse');
    });
  } finally {
    agent.close();
  }
});

test('a key the command line cannot supply says where to put it', async () => {
  const agent = await startAgent('key');
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'agent');
      const result = phone(['config', 'set', 'provider', 'anthropic']);
      assert.equal(result.status, 1);
      assert.match(result.stdout, /cannot be typed on a command line/i);
    });
  } finally {
    agent.close();
  }
});

test('an agent that is not there is said to be, not quoted as a status', async () => {
  // `http_404` is what a missing route looks like, and it is not something a
  // person can act on. Nor is "the service is not answering": the address did
  // answer, and what it said was that it is not the agent's interface. The line
  // has to say which, because the two fixes are different — point the endpoint
  // somewhere else, or start the service.
  const agent = await startAgent('missing');
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'agent');
      const result = phone(['config', 'set', 'model', 'gpt-4.1']);
      assert.equal(result.status, 1);
      assert.match(result.stdout, /not serving the agent/i);
      assert.doesNotMatch(result.stdout, /http_/);
    });
  } finally {
    agent.close();
  }
});

test('a direct-provider model is never pushed anywhere', async () => {
  const agent = await startAgent();
  try {
    await withConfig(({ path, phone }) => {
      aimAtAgent(path, agent, 'direct');
      const result = phone(['config', 'set', 'model', 'gpt-4o']);
      assert.equal(result.status, 0, result.stdout + result.stderr);
      assert.equal(agent.received().length, 0, 'the agent is not involved in direct mode');
    });
  } finally {
    agent.close();
  }
});
