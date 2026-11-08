import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { shouldShowProviderSetupOnLaunch } from '../dist/index.js';
import { configFileExists } from '../dist/store.js';

/**
 * The first-launch rule: a fresh install opens on the provider and LLM
 * settings, and an existing install opens on the chat.
 *
 * The signal is whether the configuration file exists, and only that: a
 * file whose revision or timestamp never moved is still an existing
 * install, and the tests and the fixtures write exactly those files. Once
 * the file is there the person has launched before, so a saved but
 * unfinished provider or key is a state the chat and the settings screen
 * name and refuse rather than a reason to intercept the launch — a setup
 * reached from the chat is always one Ctrl-S away.
 */

test('the config file existing is what is first about a first launch', () => {
  const directory = mkdtempSync(join(tmpdir(), 'project-phone-first-'));
  const path = join(directory, 'config.json');
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = path;
  try {
    assert.equal(configFileExists(), false);
    writeFileSync(path, '{}\n', { mode: 0o600 });
    assert.equal(configFileExists(), true);
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

test('no file opens on the provider and LLM settings on launch', () => {
  assert.equal(shouldShowProviderSetupOnLaunch({ firstLaunch: true }), true);
  assert.equal(shouldShowProviderSetupOnLaunch({ firstLaunch: false }), false);
});
