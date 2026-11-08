import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { createSettings } from '../../core/dist/index.js';
import { emptyConfig, readConfig, saveConfig } from '../dist/config.js';
import { resolveLaunchMode } from '../dist/index.js';

/**
 * A plain `phone` on a first launch opens on the provider and LLM
 * settings, and on an existing install opens on the chat.
 *
 * The decision reads the same file the surface is about to load: no file
 * at all is a first launch. `phone setup`, asked for on its own, never
 * needs deciding.
 */

function withConfigPath(run) {
  const directory = mkdtempSync(join(tmpdir(), 'project-phone-first-launch-'));
  const path = join(directory, 'config.json');
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = path;
  try {
    return run({ path });
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  }
}

test('a first launch opens on the provider setup', () => {
  withConfigPath(() => {
    assert.equal(resolveLaunchMode(emptyConfig(), 'chat'), 'firstRun');
  });
});

test('an explicit setup stays a setup', () => {
  withConfigPath(() => {
    assert.equal(resolveLaunchMode(emptyConfig(), 'setup'), 'setup');
    assert.equal(resolveLaunchMode(emptyConfig(), 'firstRun'), 'firstRun');
  });
});

test('an existing, finished install opens on the chat', () => {
  withConfigPath(({ path }) => {
    writeFileSync(path, '{}\n', { mode: 0o600 });
    const config = {
      ...emptyConfig(),
      revision: 2,
      updatedAt: 1700000000000,
      settings: createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' }),
    };
    assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
  });
});

test('a file with zero revision and timestamp still opens on the chat', () => {
  withConfigPath(({ path }) => {
    writeFileSync(path, '{}\n', { mode: 0o600 });
    const config = {
      ...emptyConfig(),
      revision: 0,
      updatedAt: 0,
      settings: createSettings({ interface: 'agent', provider: 'openai', model: 'gpt-6.1-sol' }),
    };
    assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
  });
});

test('an existing install without a finished provider still opens on the chat', () => {
  withConfigPath(({ path }) => {
    writeFileSync(path, '{}\n', { mode: 0o600 });
    const config = {
      ...emptyConfig(),
      revision: 2,
      updatedAt: 1700000000000,
      settings: createSettings({ interface: 'direct', provider: 'openai' }),
    };
    assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
  });
});

test('the rule survives a round trip through the config file', () => {
  withConfigPath(() => {
    const config = {
      ...emptyConfig(),
      revision: 2,
      updatedAt: 1700000000000,
      settings: createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' }),
    };
    const written = saveConfig(config, { origin: 'cli' });
    const read = readConfig();
    assert.equal(resolveLaunchMode(written, 'chat'), 'chat');
    assert.equal(resolveLaunchMode(read, 'chat'), 'chat');
  });
});
