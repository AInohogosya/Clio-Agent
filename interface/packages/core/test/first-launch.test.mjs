import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  createSettings,
  isFirstRun,
  isProviderConfigured,
  shouldShowProviderSetupOnLaunch,
} from '../dist/index.js';

/**
 * The first-launch rule: a fresh install opens on the provider and LLM
 * settings, and a configured one opens on the chat.
 *
 * Two signals decide it and neither carries a second spelling — "nothing
 * was ever written" is the revision and the write time, and "no usable
 * provider" is the same endpoint, key and model checks the settings form
 * already keeps. The tests below pin both halves, including the agent
 * case whose key lives on the agent side rather than in the shared file.
 */

// ----------------------------------------------------------------- first run

test('a never-written state is a first launch', () => {
  assert.equal(isFirstRun({ revision: 0, updatedAt: 0 }), true);
  assert.equal(isFirstRun({ revision: 1, updatedAt: 0 }), false);
  assert.equal(isFirstRun({ revision: 0, updatedAt: 1700000000000 }), false);
});

test('never-written state opens on setup even though the defaults name a model', () => {
  const settings = createSettings();
  assert.equal(settings.model, 'gpt-6.1-sol');
  assert.equal(shouldShowProviderSetupOnLaunch({ revision: 0, updatedAt: 0, settings }), true);
});

// ------------------------------------------------------------- provider check

test('direct provider without a key is not configured', () => {
  const settings = createSettings({ interface: 'direct', provider: 'openai', model: 'gpt-6.1-sol' });
  assert.equal(isProviderConfigured(settings), false);
  assert.equal(isProviderConfigured(settings, true), true);
});

test('a typed key counts as configured in direct mode', () => {
  const settings = createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' });
  assert.equal(isProviderConfigured(settings), true);
});

test('keyless providers need no credential', () => {
  const settings = createSettings({ interface: 'direct', provider: 'ollama', model: 'llama3.2' });
  assert.equal(isProviderConfigured(settings), true);
});

test('no model or an invalid endpoint is not configured', () => {
  assert.equal(
    isProviderConfigured(createSettings({ interface: 'direct', provider: 'anthropic', model: '' })),
    false,
  );
  assert.equal(
    isProviderConfigured(createSettings({ interface: 'direct', provider: 'anthropic', model: 'claude-x', baseUrl: 'https://not-an-endpoint' })),
    false,
  );
});

test('agent mode does not require a shared-file key', () => {
  const settings = createSettings({ interface: 'agent', provider: 'openai', model: 'gpt-6.1-sol' });
  assert.equal(isProviderConfigured(settings), true);
  assert.equal(isProviderConfigured(settings, false), true);
});

// ------------------------------------------------------------- the launch rule

test('a saved, configured install opens on the chat', () => {
  const settings = createSettings({ interface: 'direct', provider: 'openai', apiKey: 'sk-test' });
  assert.equal(shouldShowProviderSetupOnLaunch({ revision: 3, updatedAt: 1700000000000, settings }), false);
});

test('a saved install that never finished setup opens on settings again', () => {
  const settings = createSettings({ interface: 'direct', provider: 'openai' });
  assert.equal(shouldShowProviderSetupOnLaunch({ revision: 2, updatedAt: 1700000000000, settings }), true);
});

test('a saved agent install with a model opens on the chat', () => {
  const settings = createSettings({ interface: 'agent', provider: 'openai', model: 'gpt-6.1-sol' });
  assert.equal(shouldShowProviderSetupOnLaunch({ revision: 2, updatedAt: 1700000000000, settings }), false);
});
