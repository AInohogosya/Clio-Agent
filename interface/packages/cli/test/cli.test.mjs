import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { strict as assert } from 'node:assert';
import { chmodSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createSettings } from '../../core/dist/index.js';
import { readConfig, saveConfig, writeConfig } from '../dist/config.js';

const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));

/**
 * A provider, for real.
 *
 * The reply these tests used to get came from a table of canned sentences
 * inside the program, which meant a passing test proved nothing about whether a
 * provider could be reached. This is an actual HTTP server on loopback speaking
 * the OpenAI-compatible shape, so every `send` below exercises the whole path:
 * the config file, the endpoint check, the request, the parse, the transcript
 * and the write back.
 */
function startProvider(behaviour = {}) {
  const received = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      received.push({ url: request.url, method: request.method, headers: request.headers, raw });
      if (request.url?.endsWith('/models')) {
        const body = JSON.stringify({ data: [{ id: 'stub-small' }, { id: 'stub-large' }] });
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(body);
        return;
      }
      if (behaviour.status && behaviour.status >= 400) {
        response.writeHead(behaviour.status, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ error: { message: behaviour.detail ?? 'stub refused' } }));
        return;
      }
      const body = JSON.stringify({
        choices: [{ message: { role: 'assistant', content: behaviour.answer ?? 'An answer from the stub.' }, finish_reason: 'stop' }],
      });
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(body);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        received,
        baseUrl: `http://127.0.0.1:${port}/v1`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

/** Runs the CLI and collects its output, failing loudly rather than hanging. */
function runPhone(args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [entry, ...args], {
      env: { ...process.env, ...(options.env ?? {}) },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`phone ${args.join(' ')} timed out\n${stdout}\n${stderr}`));
    }, 20_000);
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.on('close', (status) => {
      clearTimeout(timer);
      resolve({ status: status ?? 0, stdout, stderr });
    });
    child.stdin.end(typeof options.input === 'string' ? options.input : undefined);
  });
}

async function withConfig(run, behaviour) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-cli-'));
  const provider = await startProvider(behaviour);
  const previousConfig = process.env.PROJECT_PHONE_CONFIG;
  const previousKey = process.env.PROJECT_PHONE_API_KEY;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  // A keyless local provider, pointed at the stub. This is the configuration
  // that needs no account and no secret, which is exactly why it is the easiest
  // thing to get right and was the easiest thing to get wrong.
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    // `direct` is stated, not inherited: the default interface is the agent, and
    // these fixtures are about reaching a model provider over HTTP.
    settings: createSettings({ interface: 'direct', provider: 'ollama', model: 'stub-small', baseUrl: provider.baseUrl }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });

  const restore = async () => {
    if (previousConfig === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previousConfig;
    if (previousKey === undefined) delete process.env.PROJECT_PHONE_API_KEY;
    else process.env.PROJECT_PHONE_API_KEY = previousKey;
    rmSync(directory, { recursive: true, force: true });
    await provider.close();
  };

  const helpers = {
    directory,
    path,
    provider,
    phone: (args, options = {}) => runPhone(args, options),
  };
  try {
    const result = await run(helpers);
    await restore();
    return result;
  } catch (error) {
    await restore();
    throw error;
  }
}

// ------------------------------------------------------------------ credentials

test('the environment credential is never written to the config file', async () => {
  await withConfig(async ({ phone, path }) => {
    const environmentKey = 'environment-only-provider-secret';
    const result = await phone(['status'], { env: { PROJECT_PHONE_API_KEY: environmentKey } });
    assert.equal(result.status, 0, result.stderr);

    // In-process the environment override applies the same way it does in a child.
    process.env.PROJECT_PHONE_API_KEY = environmentKey;
    try {
      const state = readConfig();
      assert.equal(state.settings.apiKey, environmentKey);
      assert.equal(state.credential.source, 'environment');
      saveConfig(state, { origin: 'cli' });
    } finally {
      delete process.env.PROJECT_PHONE_API_KEY;
    }
    assert.equal(readFileSync(path, 'utf8').includes(environmentKey), false);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).settings.apiKey, '');
  });
});

test('status identifies an environment credential as session-only', async () => {
  await withConfig(async ({ phone }) => {
    const result = await phone(['status'], { env: { PROJECT_PHONE_API_KEY: 'environment-status-secret' } });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /environment/i);
  });
});

test('a secret is refused as an argument but accepted from the environment', async () => {
  await withConfig(async ({ phone }) => {
    const refused = await phone(['config', 'set', 'apiKey', 'sk-should-be-refused']);
    assert.equal(refused.status, 1);
    assert.match(refused.stderr, /API keys are never accepted as arguments/);
    assert.ok(!refused.stdout.includes('sk-should-be-refused'));
  });
});

// -------------------------------------------------------------------- plumbing

test('the config file stays owner-only after every command', async () => {
  await withConfig(async ({ phone, path }) => {
    await phone(['config', 'set', 'accent', '#0EA5E9']);
    await phone(['send', 'hello']);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).settings.accent, '#0EA5E9');
  });
});

test('version and help answer on their own', async () => {
  await withConfig(async ({ phone }) => {
    const version = await phone(['--version']);
    assert.equal(version.status, 0);
    assert.match(version.stdout, /Clio Agent 3 Beta 1 — released \d{4}-\d{2}-\d{2} \(JST\)/);

    const help = await phone(['help', '--width', '80']);
    assert.equal(help.status, 0);
    assert.match(help.stdout, /phone status/);
    assert.match(help.stdout, /phone config/);
  });
});

test('the status report names every setting', async () => {
  await withConfig(async ({ phone }) => {
    const result = await phone(['status', '--width', '76']);
    assert.equal(result.status, 0, result.stderr);
    for (const label of ['Provider', 'Model', 'Endpoint', 'Key', 'Language', 'Theme', 'Accent']) {
      assert.ok(result.stdout.includes(label), `missing ${label}`);
    }
    assert.ok(result.stdout.includes('COORDINATION'));
  });
});

test('every box in the output is exactly the requested width', async () => {
  await withConfig(async ({ phone }) => {
    for (const width of [64, 80, 100]) {
      const result = await phone(['status', '--width', String(width), '--no-color']);
      assert.equal(result.status, 0, result.stderr);
      const boxRows = result.stdout.split('\n').filter((line) => /^╭|^│|^╰|^├/.test(line));
      assert.ok(boxRows.length > 0);
      for (const row of boxRows) {
        assert.equal([...row].length, width, `width ${width}: ${JSON.stringify(row)}`);
      }
    }
  });
});

test('json output is parseable and never carries the key', async () => {
  await withConfig(async ({ phone }) => {
    const result = await phone(['status', '--json'], { env: { PROJECT_PHONE_API_KEY: 'json-secret-value' } });
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    assert.equal(payload.settings.apiKey, '');
    assert.equal(payload.credential.present, true);
    assert.equal(payload.credentialSource, 'environment');
    assert.equal(payload.credential.source, 'environment');
    assert.ok(!result.stdout.includes('json-secret-value'));
    assert.equal(typeof payload.revision, 'number');
  });
});

// ------------------------------------------------------------------ round trip

test('a message argument is sent and the answer is written to the shared file', async () => {
  await withConfig(async ({ phone, path, provider }) => {
    const result = await phone(['send', 'hello there', '--width', '76']);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /hello there/);
    assert.match(result.stdout, /An answer from the stub/);

    // The provider really was asked, over HTTP, with the model from the file.
    const call = provider.received.find((entry) => entry.url?.endsWith('/chat/completions'));
    assert.ok(call, 'the stub saw a completion request');
    assert.equal(JSON.parse(call.raw).model, 'stub-small');

    const state = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(state.messages.length, 2);
    assert.equal(state.messages[0].role, 'user');
    assert.equal(state.messages[0].text, 'hello there');
    assert.equal(state.messages[1].role, 'assistant');
    assert.equal(state.messages[1].text, 'An answer from the stub.');
    assert.equal(state.origin, 'cli');
  });
});

test('a bare word is treated as the message', async () => {
  await withConfig(async ({ phone, path }) => {
    const result = await phone(['a', 'quiet', 'question', '--json']);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).message.text, 'a quiet question');
    assert.equal(JSON.parse(result.stdout).reply.text, 'An answer from the stub.');
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).messages[0].text, 'a quiet question');
  });
});

test('the message case survives the round trip', async () => {
  await withConfig(async ({ phone, provider }) => {
    await phone(['send', 'Why Is THIS Cased?']);
    const call = provider.received.find((entry) => entry.url?.endsWith('/chat/completions'));
    assert.equal(JSON.parse(call.raw).messages[0].content, 'Why Is THIS Cased?');
  });
});

test('piped stdin drives a single message', async () => {
  await withConfig(async ({ phone, path }) => {
    const result = await phone(['send', '--json'], { input: 'from a pipe\n' });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).message.text, 'from a pipe');
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).messages.length, 2);
  });
});

test('piped lines drive a full session and share one transcript', async () => {
  await withConfig(async ({ phone, path, provider }) => {
    const result = await phone(['--width', '76'], { input: 'first\nsecond\n' });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /first/);
    assert.match(result.stdout, /second/);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).messages.length, 4);

    // The second turn carries the first, which is what a conversation is.
    const calls = provider.received.filter((entry) => entry.url?.endsWith('/chat/completions'));
    assert.equal(calls.length, 2);
    const second = JSON.parse(calls[1].raw).messages;
    assert.equal(second.length, 3);
    assert.deepEqual(second.map((turn) => turn.role), ['user', 'assistant', 'user']);
  });
});

test('history reflects the shared transcript and clear empties it', async () => {
  await withConfig(async ({ phone, path }) => {
    await phone(['send', 'remember this']);
    const history = await phone(['history', '--width', '76']);
    assert.match(history.stdout, /remember this/);

    const cleared = await phone(['clear', '--width', '76']);
    assert.equal(cleared.status, 0, cleared.stderr);
    assert.match(cleared.stdout, /cleared/i);
    assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')).messages, []);

    const empty = await phone(['history']);
    assert.match(empty.stdout, /No messages yet/);
  });
});

test('an idle session writes nothing, so the file is not churned', async () => {
  await withConfig(async ({ phone, path }) => {
    await phone(['send', 'once']);
    const first = readFileSync(path, 'utf8');
    await phone(['status']);
    await phone(['status']);
    assert.equal(readFileSync(path, 'utf8'), first, 'reading never rewrites the file');
  });
});

// ------------------------------------------------------------------- failures

test('a provider refusal is reported, and nothing is invented in its place', async () => {
  await withConfig(async ({ phone, path }) => {
    const result = await phone(['send', 'are you there', '--json']);
    // A failure is an honest exit code on both output paths, so a script can
    // tell the difference without reading the payload.
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.equal(JSON.parse(result.stdout).reply, null);
    assert.match(JSON.parse(result.stdout).error, /401/);

    // The question stays in the transcript; no answer appears that the provider
    // never gave.
    const state = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(state.messages.length, 1);
    assert.equal(state.messages[0].role, 'user');
  }, { status: 401, detail: 'Incorrect API key provided' });

  await withConfig(async ({ phone }) => {
    const result = await phone(['send', 'are you there']);
    assert.equal(result.status, 1, 'a failure is an honest exit code');
    assert.match(result.stderr, /401/);
    assert.match(result.stderr, /Incorrect API key provided/);
    assert.doesNotMatch(result.stdout, /I hear you/);
  }, { status: 401, detail: 'Incorrect API key provided' });
});

test('an unreachable provider is reported rather than answered for', async () => {
  // The endpoint is a port nothing is listening on.
  const directory = mkdtempSync(join(tmpdir(), 'phone-cli-down-'));
  const path = join(directory, 'config.json');
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = path;
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    settings: createSettings({ interface: 'direct', provider: 'ollama', model: 'stub-small', baseUrl: 'http://127.0.0.1:9/v1' }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });
  try {
    const result = await runPhone(['send', 'anyone home'], {});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /provider|reach/i);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).messages.length, 1);
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

test('an unconfigured line says so before a message is sent', async () => {
  await withConfig(async ({ phone, provider, path, directory }) => {
    // The agent link, pointed at a port nothing is listening on. The default
    // address is this machine's own interface service, and a test that points at
    // it passes or fails depending on whether the machine running it happens to
    // have an agent up — which is how this one came to be satisfied by a live
    // agent rather than by the behaviour it names.
    const unconfigured = { ...JSON.parse(readFileSync(path, 'utf8')) };
    unconfigured.settings = createSettings({
      interface: 'agent',
      agentUrl: 'http://127.0.0.1:1',
    });
    const agentConfig = join(directory, 'agent.json');
    writeFileSync(agentConfig, `${JSON.stringify(unconfigured, null, 2)}\n`, { mode: 0o600 });
    const agent = await phone(['send', 'hello', '--json'], {
      env: { PROJECT_PHONE_CONFIG: agentConfig },
    });
    assert.equal(agent.status, 1);
    assert.match(agent.stdout + agent.stderr, /agent is not answering/i);

    // The same question asked of a direct provider is about the credential,
    // which is a different problem with a different fix.
    const direct = { ...JSON.parse(readFileSync(path, 'utf8')) };
    direct.settings = createSettings({ interface: 'direct', provider: 'openai', apiKey: '' });
    writeFileSync(path, `${JSON.stringify(direct, null, 2)}\n`, { mode: 0o600 });
    const provider_ = await phone(['send', 'hello', '--json']);
    assert.equal(provider_.status, 1);
    assert.match(provider_.stdout + provider_.stderr, /key/i);

    // In both cases nothing was asked of the network: the reason is given
    // before a message is even sent.
    assert.equal(provider.received.length, 0, 'no request was attempted');
  });
});

test('models is read from the endpoint when it answers', async () => {
  await withConfig(async ({ phone, provider }) => {
    const result = await phone(['models', '--width', '76']);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /stub-small/);
    assert.match(result.stdout, /stub-large/);
    assert.ok(provider.received.some((entry) => entry.url?.endsWith('/models')));
  });
});

test('models falls back to the offline catalogue when the endpoint is down', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'phone-cli-offline-'));
  const path = join(directory, 'config.json');
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = path;
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    settings: createSettings({ interface: 'direct', provider: 'ollama', model: 'llama3.2', baseUrl: 'http://127.0.0.1:9/v1' }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });
  try {
    const result = await runPhone(['models', '--json'], {});
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    assert.equal(payload.source, 'offline');
    assert.equal(payload.models.includes('llama3.2'), true, 'the offline list is still offered');
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------- config

test('config reads, writes and resets individual values', async () => {
  await withConfig(async ({ phone, path }) => {
    assert.equal((await phone(['config', 'get', 'provider'])).stdout.trim(), 'ollama');

    assert.equal((await phone(['config', 'set', 'theme', 'light'])).status, 0);
    assert.equal((await phone(['config', 'get', 'theme'])).stdout.trim(), 'light');

    await phone(['config', 'set', 'provider', 'xai']);
    const state = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(state.settings.provider, 'xai');
    assert.equal(state.settings.baseUrl, 'https://api.x.ai/v1');
    assert.equal(state.settings.model, 'grok-2-latest');

    await phone(['config', 'unset', 'accent']);
    assert.equal((await phone(['config', 'get', 'accent'])).stdout.trim(), '#F97316');
  });
});

test('every field is listed and readable, and nothing else is', async () => {
  await withConfig(async ({ phone }) => {
    const listed = await phone(['config', 'list']);
    for (const field of ['provider', 'model', 'baseUrl', 'apiKey', 'language', 'theme', 'accent']) {
      assert.match(listed.stdout, new RegExp(field), field);
    }
    assert.doesNotMatch(listed.stdout, /autoSignal/);
    assert.equal((await phone(['config', 'get', 'autoSignal'])).status, 1);
  });
});

test('invalid configuration values are refused with a readable message', async () => {
  await withConfig(async ({ phone }) => {
    const theme = await phone(['config', 'set', 'theme', 'neon']);
    assert.equal(theme.status, 1);
    assert.match(theme.stderr, /Invalid value for theme/);

    const accent = await phone(['config', 'set', 'accent', 'orange']);
    assert.equal(accent.status, 1);

    const provider = await phone(['config', 'set', 'provider', 'skynet']);
    assert.equal(provider.status, 1);
    assert.match(provider.stderr, /Unknown provider/);

    const endpoint = await phone(['config', 'set', 'baseUrl', 'http://evil.example']);
    assert.equal(endpoint.status, 1);
  });
});

test('a language override applies to the whole run', async () => {
  await withConfig(async ({ phone }) => {
    for (const [code, word] of [['ja', 'プロバイダー'], ['zh', '提供商'], ['en', 'Provider']]) {
      const result = await phone(['status', '--lang', code]);
      assert.equal(result.status, 0, result.stderr);
      assert.ok(result.stdout.includes(word), `${code} missing ${word}`);
    }
  });
});

test('an unknown flag fails with a clear message', async () => {
  await withConfig(async ({ phone }) => {
    const result = await phone(['--not-a-flag']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Unknown command/);
  });
});

// ------------------------------------------------------------------- resilience

test('the terminal adopts a change written by another surface', async () => {
  await withConfig(async ({ path, phone }) => {
    // Simulate the browser writing the shared file with a newer revision.
    writeConfig(readConfig());
    const current = JSON.parse(readFileSync(path, 'utf8'));
    writeFileSync(path, `${JSON.stringify({
      ...current,
      revision: current.revision + 5,
      origin: 'web',
      settings: { ...current.settings, theme: 'light', accent: '#0EA5E9' },
    }, null, 2)}\n`, { mode: 0o600 });

    const result = await phone(['status', '--width', '76']);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /#0EA5E9/);
    assert.match(result.stdout, /light/);
  });
});

test('a config written by the CLI is readable by the shared store helpers', async () => {
  await withConfig(async ({ phone }) => {
    await phone(['send', 'shared state']);
    const state = readConfig();
    assert.equal(state.transport, 'file');
    assert.equal(state.origin, 'cli');
    assert.equal(state.messages.length, 2);
    assert.equal(state.revision >= 1, true);
    assert.equal(state.settings.model, 'stub-small');
    assert.equal(state.credential.present, false);
  });
});

test('a pre-existing file is upgraded in place on first write', async () => {
  await withConfig(async ({ phone, path }) => {
    writeFileSync(path, `${JSON.stringify({
      version: 1,
      settings: { provider: 'groq', model: 'llama-3.3-70b-versatile' },
      messages: [],
      thoughts: [],
    })}\n`, { mode: 0o600 });

    assert.equal((await phone(['config', 'get', 'provider'])).stdout.trim(), 'groq');
    await phone(['config', 'set', 'theme', 'light']);

    const raw = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(raw.revision, 1);
    assert.equal(raw.origin, 'cli');
    assert.equal(raw.settings.provider, 'groq');
  });
});

test('an insecure config file is repaired rather than trusted', async () => {
  await withConfig(async ({ phone, path }) => {
    await phone(['config', 'set', 'theme', 'light']);
    chmodSync(path, 0o644);
    const result = await phone(['status']);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(statSync(path).mode & 0o777, 0o600);
  });
});

test('a corrupt config file never crashes a command', async () => {
  await withConfig(async ({ phone, path, directory }) => {
    writeFileSync(path, 'not json at all', { mode: 0o600 });
    const result = await phone(['status', '--width', '76']);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Provider/);
    // The unreadable bytes are set aside rather than silently overwritten, so a
    // truncated write can be recovered by hand instead of taking the settings
    // that shared the file with it.
    const quarantined = readdirSync(directory).filter((name) => name.startsWith('config.json.corrupt-'));
    assert.equal(quarantined.length, 1);
    assert.equal(readFileSync(join(directory, quarantined[0]), 'utf8'), 'not json at all');
  });
});

test('empty input is refused', async () => {
  await withConfig(async ({ phone }) => {
    const result = await phone(['send', '   ', '--json']);
    assert.equal(result.status, 1);
  });
});

test('the settings file the terminal writes matches the documented shape', async () => {
  await withConfig(async ({ phone, path }) => {
    await phone(['send', 'shape check']);
    const raw = JSON.parse(readFileSync(path, 'utf8'));
    assert.deepEqual(Object.keys(raw).sort(), [
      'credential', 'messages', 'origin', 'revision', 'settings', 'transport', 'updatedAt', 'version',
    ]);
    assert.equal(raw.version, 2);
    assert.equal(typeof raw.updatedAt, 'number');
    assert.deepEqual(Object.keys(raw.messages[0]).sort(), ['createdAt', 'id', 'role', 'text']);
  });
});

test('createSettings remains the single normaliser for stored settings', () => {
  const settings = createSettings({ provider: 'xai', model: 'grok-2-latest' });
  assert.equal(settings.provider, 'xai');
  assert.equal(settings.baseUrl, 'https://api.x.ai/v1');
});
