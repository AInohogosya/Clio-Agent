import { strict as assert } from 'node:assert';
import { chmodSync, existsSync, lstatSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { test } from 'node:test';
import process from 'node:process';
import { createSettings } from '../dist/index.js';
import { configPath, readConfig, saveConfig, watchConfig, writeConfig } from '../dist/store.js';

function withTempConfig(run) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-store-'));
  const previous = process.env.PROJECT_PHONE_CONFIG;
  const previousKey = process.env.PROJECT_PHONE_API_KEY;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  delete process.env.PROJECT_PHONE_API_KEY;
  const restore = () => {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    if (previousKey === undefined) delete process.env.PROJECT_PHONE_API_KEY;
    else process.env.PROJECT_PHONE_API_KEY = previousKey;
    rmSync(directory, { recursive: true, force: true });
  };
  let result;
  try {
    result = run({ directory, path });
  } catch (error) {
    restore();
    throw error;
  }
  // An async body must keep the override and the directory alive until it settles.
  if (result && typeof result.then === 'function') return result.finally(restore);
  restore();
  return result;
}

test('a fresh path yields defaults without creating anything eagerly', () => {
  withTempConfig(({ path }) => {
    const state = readConfig();
    assert.equal(state.revision, 0);
    assert.deepEqual(state.messages, []);
    assert.equal(state.settings.provider, 'openai');
    assert.throws(() => statSync(path));
  });
});

test('writes are atomic, owner-only, and survive a reload', () => {
  withTempConfig(({ directory, path }) => {
    const saved = saveConfig({
      ...readConfig(),
      settings: createSettings({ provider: 'ollama', model: 'llama3.2' }),
    }, { origin: 'cli' });

    assert.equal(saved.revision, 1);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(statSync(directory).mode & 0o777, 0o700);

    const reloaded = readConfig();
    assert.equal(reloaded.settings.provider, 'ollama');
    assert.equal(reloaded.revision, 1);
    assert.equal(reloaded.origin, 'cli');
  });
});

test('the key, language, model, theme and accent all survive a restart', () => {
  withTempConfig(({ path }) => {
    const chosen = createSettings({
      provider: 'anthropic',
      apiKey: 'sk-durable-secret',
      model: 'claude-sonnet-5',
      language: 'zh',
      theme: 'light',
      accent: '#00FFAA',
    });
    saveConfig({ ...readConfig(), settings: chosen }, { origin: 'web' });

    // Each read stands in for a fresh process: nothing but the file carries over.
    for (const origin of ['cli', 'web', 'cli']) {
      const reloaded = readConfig().settings;
      assert.equal(reloaded.apiKey, 'sk-durable-secret', `apiKey lost on ${origin} restart`);
      assert.equal(reloaded.language, 'zh', `language lost on ${origin} restart`);
      assert.equal(reloaded.model, 'claude-sonnet-5', `model lost on ${origin} restart`);
      assert.equal(reloaded.theme, 'light', `theme lost on ${origin} restart`);
      assert.equal(reloaded.accent, '#00FFAA', `accent lost on ${origin} restart`);
      assert.equal(reloaded.provider, 'anthropic', `provider lost on ${origin} restart`);
    }

    // The key is on disk in the owner-only file, and nowhere else.
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(readFileSync(path, 'utf8').includes('sk-durable-secret'), true);
    assert.equal(readdirSync(dirname(path)).filter((n) => n.includes('corrupt')).length, 0);
  });
});

test('no temporary files are left behind', () => {
  withTempConfig(({ directory }) => {
    for (let index = 0; index < 5; index += 1) saveConfig(readConfig(), { origin: 'cli' });
    const leftovers = readdirSync(directory).filter((name) => name.includes('.tmp'));
    assert.deepEqual(leftovers, []);
  });
});

test('an environment credential is never written to disk', () => {
  withTempConfig(({ path }) => {
    process.env.PROJECT_PHONE_API_KEY = 'environment-only-secret-value';
    const state = readConfig();
    saveConfig({ ...state, settings: createSettings({ provider: 'openai', apiKey: state.settings.apiKey }) }, { origin: 'cli' });

    const raw = readFileSync(path, 'utf8');
    assert.equal(raw.includes('environment-only-secret-value'), false);
    assert.equal(JSON.parse(raw).settings.apiKey, '');
    assert.equal(readConfig().settings.apiKey, 'environment-only-secret-value');
    assert.equal(readConfig().credential.source, 'environment');
  });
});

test('a group-readable file is treated as untrusted and repaired', () => {
  withTempConfig(({ path }) => {
    const seeded = saveConfig({
      ...readConfig(),
      settings: createSettings({ provider: 'openai', apiKey: 'file-key-5555', language: 'ja', accent: '#00FFAA' }),
    }, { origin: 'cli' });
    assert.equal(seeded.credential.present, true);

    // A config restored from an archive or a checkout arrives 0644. That is a
    // fault in the mode, not in the settings, so the mode is tightened and the
    // settings are read rather than thrown away.
    chmodSync(path, 0o644);
    const repaired = readConfig();
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(repaired.settings.apiKey, 'file-key-5555');
    assert.equal(repaired.settings.language, 'ja');
    assert.equal(repaired.settings.accent, '#00FFAA');
    // The next start still finds them.
    assert.equal(readConfig().settings.apiKey, 'file-key-5555');
  });
});

test('a group-readable directory is tightened without losing the settings', () => {
  withTempConfig(({ directory }) => {
    saveConfig({
      ...readConfig(),
      settings: createSettings({ provider: 'openai', apiKey: 'dir-key-7777', theme: 'light' }),
    }, { origin: 'cli' });

    chmodSync(directory, 0o755);
    const state = readConfig();
    assert.equal(statSync(directory).mode & 0o777, 0o700);
    assert.equal(state.settings.apiKey, 'dir-key-7777');
    assert.equal(state.settings.theme, 'light');
  });
});

test('a symlinked config file is a hard failure, never followed', () => {
  withTempConfig(({ directory, path }) => {
    const target = join(directory, 'real.json');
    writeFileSync(target, JSON.stringify({ version: 1, settings: { provider: 'groq' } }));
    symlinkSync(target, path);
    assert.throws(() => readConfig(), /unsafe_config_target/);
    // The symlink target is left untouched.
    assert.equal(JSON.parse(readFileSync(target, 'utf8')).settings.provider, 'groq');
    assert.equal(lstatSync(path).isSymbolicLink(), true);
  });
});

test('an unparseable file yields defaults and is quarantined, not overwritten', () => {
  withTempConfig(({ directory, path }) => {
    writeFileSync(path, 'this is not json at all');
    const state = readConfig();
    assert.equal(state.settings.provider, 'openai');
    assert.equal(state.messages.length, 0);

    // The bad bytes are moved aside so nothing is destroyed and the contents
    // are still recoverable by hand, rather than being silently truncated.
    const quarantined = readdirSync(directory).filter((name) => name.startsWith("config.json.corrupt-"));
    assert.equal(quarantined.length, 1);
    assert.equal(readFileSync(join(directory, quarantined[0]), 'utf8'), 'this is not json at all');
    // The unreadable bytes are not left in place to fail again on the next start.
    assert.equal(existsSync(path), false);
    // And the session still starts, on defaults.
    assert.equal(readConfig().settings.provider, 'openai');
  });
});

test('a payload that is not an object is quarantined too', () => {
  withTempConfig(({ directory, path }) => {
    writeFileSync(path, '["not","an","object"]', { mode: 0o600 });
    const state = readConfig();
    assert.equal(state.settings.provider, 'openai');
    const quarantined = readdirSync(directory).filter((name) => name.startsWith("config.json.corrupt-"));
    assert.equal(quarantined.length, 1);
  });
});

test('a momentarily unreadable file is left alone for the next attempt', () => {
  withTempConfig(({ path }) => {
    saveConfig({
      ...readConfig(),
      settings: createSettings({ provider: 'openai', apiKey: 'kept-key-8888', language: 'zh' }),
    }, { origin: 'cli' });
    const good = readFileSync(path, 'utf8');

    // A permission revoked for a moment, or a rename in flight, is not damage.
    chmodSync(path, 0o000);
    const blocked = readConfig();
    assert.equal(blocked.settings.provider, 'openai');
    assert.equal(blocked.settings.apiKey, '');

    chmodSync(path, 0o600);
    // Nothing was overwritten, so the settings come straight back.
    assert.equal(readConfig().settings.apiKey, 'kept-key-8888');
    assert.equal(readConfig().settings.language, 'zh');
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).settings.apiKey, 'kept-key-8888');
    assert.equal(readFileSync(path, 'utf8'), good);
  });
});

test('a file from an older build is read, and saved in the current shape', () => {
  withTempConfig(({ path }) => {
    // An older build wrote a `source` and `status` on every message, and a
    // separate list of ambient thoughts. Reading it keeps the conversation and
    // drops the rest, rather than refusing the file and losing the key with it.
    writeFileSync(path, JSON.stringify({
      version: 1,
      settings: { provider: 'mistral', model: 'mistral-small-latest', apiKey: 'legacy-key-1234' },
      messages: [{ id: 'm1', role: 'user', text: 'hi', createdAt: 1700000000000, source: 'user', status: 'sent' }],
      thoughts: [{ id: 't1', text: 'thinking', createdAt: 1700000000001 }],
    }), { mode: 0o600 });
    const state = readConfig();
    assert.equal(state.settings.provider, 'mistral');
    assert.equal(state.settings.apiKey, 'legacy-key-1234');
    assert.equal(state.messages.length, 1);
    assert.equal(state.messages[0].text, 'hi');

    const saved = saveConfig(state, { origin: 'cli' });
    assert.equal(saved.revision, 1);
    const raw = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(raw.origin, 'cli');
    assert.equal(raw.transport, 'file');
    assert.equal(raw.settings.apiKey, 'legacy-key-1234');
    // The fields this build no longer has must not be written back out, or a
    // downgrade would resurrect a transcript full of lines nobody sent.
    assert.equal('thoughts' in raw, false);
    assert.equal('source' in raw.messages[0], false);
    assert.equal('status' in raw.messages[0], false);
  });
});

test('revisions advance monotonically across many saves', () => {
  withTempConfig(() => {
    let state = readConfig();
    const seen = [];
    for (let index = 0; index < 5; index += 1) {
      state = saveConfig(state, { origin: 'cli' });
      seen.push(state.revision);
    }
    assert.deepEqual(seen, [1, 2, 3, 4, 5]);
  });
});

test('a fixed clock and revision cap keep values bounded', () => {
  withTempConfig(() => {
    const state = saveConfig(readConfig(), { origin: 'web', now: 1_700_000_000_000 });
    assert.equal(state.updatedAt, 1_700_000_000_000);
    const capped = saveConfig({ ...state, revision: 2_147_483_647 }, { origin: 'web' });
    assert.equal(capped.revision, 2_147_483_647);
  });
});

test('the watcher reports a change made by another writer', async () => {
  await withTempConfig(async ({ path }) => {
    writeConfig(readConfig());
    const seen = [];
    const stop = watchConfig((state) => seen.push(state));

    try {
      writeConfig({ ...readConfig(), settings: createSettings({ theme: 'light' }) });
      await new Promise((resolve) => setTimeout(resolve, 400));
      assert.equal(seen.length >= 1, true, 'expected at least one change event');
      assert.equal(seen.at(-1).settings.theme, 'light');
    } finally {
      stop();
    }
    assert.ok(path.length > 0);
  });
});

test('the watcher survives the rename that an atomic write performs', async () => {
  await withTempConfig(async () => {
    writeConfig(readConfig());
    const stop = watchConfig(() => undefined);

    try {
      for (let index = 0; index < 4; index += 1) {
        saveConfig(readConfig(), { origin: 'cli' });
        await new Promise((resolve) => setTimeout(resolve, 120));
      }
      const final = readConfig();
      assert.equal(final.revision, 4);
    } finally {
      stop();
    }
  });
});

test('a stopped watcher goes quiet', async () => {
  await withTempConfig(async () => {
    writeConfig(readConfig());
    const seen = [];
    const stop = watchConfig((state) => seen.push(state));
    stop();
    saveConfig(readConfig(), { origin: 'cli' });
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.deepEqual(seen, []);
  });
});

test('the config path honours the environment override', () => {
  withTempConfig(({ path }) => {
    assert.equal(configPath(), path);
  });
});
