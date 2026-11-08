import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { createSettings } from '../../core/dist/index.js';
import { emptyConfig, readConfig, saveConfig } from '../dist/config.js';
import { resolveLaunchMode } from '../dist/index.js';

/**
 * A plain `phone` on a first launch opens on the provider and LLM
 * settings, and on a configured one opens on the chat.
 *
 * The decision reads the same file the surface is about to load, and the
 * checks are the shared ones: a file that was never written is a first
 * launch whatever its defaults say, and a written file that still does
 * not leave a usable provider behind is the same setup owed a second
 * chance. `phone setup`, asked for on its own, never needs deciding.
 */

const SAVED = 1700000000000;

test('a first launch opens on the provider setup', () => {
  const config = emptyConfig();
  assert.equal(resolveLaunchMode(config, 'chat'), 'firstRun');
});

test('an explicit setup stays a setup', () => {
  const config = emptyConfig();
  assert.equal(resolveLaunchMode(config, 'setup'), 'setup');
  assert.equal(resolveLaunchMode(config, 'firstRun'), 'firstRun');
});

test('a saved, configured install opens on the chat', () => {
  const config = {
    ...emptyConfig(),
    revision: 2,
    updatedAt: SAVED,
    settings: createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' }),
  };
  assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
});

test('a key on file counts even when the draft key field is empty', () => {
  const config = {
    ...emptyConfig(),
    revision: 2,
    updatedAt: SAVED,
    settings: createSettings({ interface: 'direct', provider: 'openai' }),
    credential: { present: true, source: 'file', hint: '••••test' },
  };
  assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
});

test('a saved install that never finished setup opens on setup again', () => {
  const config = {
    ...emptyConfig(),
    revision: 2,
    updatedAt: SAVED,
    settings: createSettings({ interface: 'direct', provider: 'openai' }),
  };
  assert.equal(resolveLaunchMode(config, 'chat'), 'firstRun');
});

test('a saved agent install opens on the chat without a shared-file key', () => {
  const config = {
    ...emptyConfig(),
    revision: 2,
    updatedAt: SAVED,
    settings: createSettings({ interface: 'agent', provider: 'openai', model: 'gpt-6.1-sol' }),
  };
  assert.equal(resolveLaunchMode(config, 'chat'), 'chat');
});

test('the rule survives a round trip through the config file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'project-phone-first-launch-'));
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = join(dir, 'config.json');
  try {
    const config = {
      ...emptyConfig(),
      revision: 2,
      updatedAt: SAVED,
      settings: createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' }),
    };
    const written = saveConfig(config, { origin: 'cli' });
    const read = readConfig();
    assert.equal(resolveLaunchMode(written, 'chat'), 'chat');
    assert.equal(resolveLaunchMode(read, 'chat'), 'chat');
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(dir, { recursive: true, force: true });
  }
});
