import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  createSettings,
  createSharedState,
  credentialHint,
  describeCredential,
  diffSettings,
  mergeMessages,
  mergeSharedState,
  nextRevision,
  normalizeSharedState,
  publicSettingsEqual,
  redactSettings,
  SHARED_STATE_VERSION,
  toPublicState,
} from '../dist/index.js';

function message(id, createdAt, text = id) {
  return { id, role: 'user', text, createdAt };
}

test('a file without revision metadata normalises upwards', () => {
  const state = normalizeSharedState({
    version: 1,
    settings: createSettings({ provider: 'groq', model: 'llama-3.3-70b-versatile' }),
    messages: [message('m1', 10)],
  });
  assert.equal(state.version, SHARED_STATE_VERSION);
  assert.equal(state.revision, 0);
  assert.equal(state.updatedAt, 0);
  assert.equal(state.settings.provider, 'groq');
  assert.equal(state.messages.length, 1);
  assert.equal(state.origin, null);
});

test('a malformed payload degrades to defaults instead of throwing', () => {
  for (const value of [null, undefined, 42, 'text', [], true]) {
    const state = normalizeSharedState(value);
    assert.equal(state.revision, 0);
    assert.deepEqual(state.messages, []);
    assert.equal(state.settings.provider, 'openai');
  }
});

test('hostile field values are dropped during normalisation', () => {
  const state = normalizeSharedState({
    revision: -5,
    updatedAt: Number.NaN,
    origin: 'somewhere-else',
    transport: 'carrier-pigeon',
    settings: { provider: 'skynet', accent: 'red', model: { nested: true } },
    messages: [{ id: '', text: 'x', createdAt: -1, role: 'wizard' }],
  });
  assert.equal(state.revision, 0);
  assert.equal(state.updatedAt, 0);
  assert.equal(state.origin, null);
  assert.equal(state.transport, 'none');
  assert.equal(state.settings.provider, 'openai');
  assert.equal(state.settings.accent, '#F97316');
  assert.deepEqual(state.messages, []);
});

test('the API key never survives into a public snapshot', () => {
  const secret = 'sk-super-secret-value-1234';
  const state = createSharedState(
    { settings: createSettings({ provider: 'openai', apiKey: secret }) },
    { origin: 'cli', transport: 'file' },
  );
  assert.equal(state.settings.apiKey, secret);
  assert.equal(state.credential.present, true);
  assert.equal(state.credential.source, 'file');
  assert.equal(state.credential.hint, '••••1234');

  const view = toPublicState(state);
  assert.equal(view.settings.apiKey, '');
  assert.ok(!JSON.stringify(view).includes(secret));
});

test('a credential hint reveals nothing but the last four characters', () => {
  assert.equal(credentialHint('sk-abcdefghijklmnop'), '••••mnop');
  assert.equal(credentialHint('short'), '•••••');
  assert.equal(credentialHint(''), '');
  assert.equal(credentialHint('bad\u0000value'), '');
});

test('an environment credential is reported as session-only', () => {
  const status = describeCredential('', 'env-key-9999');
  assert.equal(status.present, true);
  assert.equal(status.source, 'environment');
  assert.equal(describeCredential('file-key-8888', '').source, 'file');
  assert.equal(describeCredential('', '').source, 'none');
});

test('an environment key overrides the stored one when normalising', () => {
  const state = normalizeSharedState(
    { settings: createSettings({ apiKey: 'stored-key-1111' }) },
    { environmentKey: 'environment-key-2222' },
  );
  assert.equal(state.settings.apiKey, 'environment-key-2222');
  assert.equal(state.credential.source, 'environment');
});

test('redactSettings keeps every non-secret field intact', () => {
  const settings = createSettings({ provider: 'mistral', model: 'mistral-small-latest', theme: 'light', accent: '#0EA5E9', apiKey: 'abc-9999' });
  const redacted = redactSettings(settings);
  assert.equal(redacted.apiKey, '');
  assert.equal(redacted.provider, 'mistral');
  assert.equal(redacted.model, 'mistral-small-latest');
  assert.equal(redacted.theme, 'light');
  assert.equal(redacted.accent, '#0EA5E9');
});

test('history merges as a lossless, ordered union', () => {
  const local = [message('a', 1), message('c', 3)];
  const remote = [message('b', 2), message('c', 3, 'remote wins')];
  const merged = mergeMessages(local, remote);
  assert.deepEqual(merged.map((item) => item.id), ['a', 'b', 'c']);
  assert.equal(merged.at(-1).text, 'remote wins');
});

test('history merging is idempotent', () => {
  const local = [message('a', 1), message('b', 2)];
  const once = mergeMessages(local, [message('b', 2)]);
  const twice = mergeMessages(once, [message('b', 2)]);
  assert.deepEqual(twice.map((item) => item.id), once.map((item) => item.id));
});

test('a higher revision wins for settings, but history is still unioned', () => {
  const older = createSharedState(
    { settings: createSettings({ theme: 'dark' }), messages: [message('a', 1)], revision: 4, updatedAt: 100 },
    { origin: 'cli', transport: 'file' },
  );
  const newer = createSharedState(
    { settings: createSettings({ theme: 'light' }), messages: [message('b', 2)], revision: 9, updatedAt: 200 },
    { origin: 'web', transport: 'bridge' },
  );
  const merged = mergeSharedState(older, newer);
  assert.equal(merged.settings.theme, 'light');
  assert.equal(merged.revision, 9);
  assert.deepEqual(merged.messages.map((item) => item.id), ['a', 'b']);
});

test('a stale revision cannot roll settings back', () => {
  const current = createSharedState(
    { settings: createSettings({ theme: 'light' }), revision: 9, updatedAt: 200 },
    { origin: 'web', transport: 'bridge' },
  );
  const stale = createSharedState(
    { settings: createSettings({ theme: 'dark' }), revision: 3, updatedAt: 100 },
    { origin: 'cli', transport: 'file' },
  );
  const merged = mergeSharedState(current, stale);
  assert.equal(merged.settings.theme, 'light');
  assert.equal(merged.revision, 9);
});

test('the file owner is authoritative about the credential', () => {
  const fromFile = createSharedState(
    { settings: createSettings({ apiKey: 'file-key-7777' }), revision: 2 },
    { origin: 'cli', transport: 'file' },
  );
  const fromBrowser = createSharedState(
    { settings: createSettings(), revision: 5 },
    { origin: 'web', transport: 'bridge' },
  );
  assert.equal(mergeSharedState(fromBrowser, fromFile).credential.present, true);
  assert.equal(mergeSharedState(fromFile, fromBrowser).credential.present, true);
});

test('a redacted snapshot does not look like a deleted key', () => {
  const withKey = createSettings({ provider: 'openai', apiKey: 'file-key-7777' });
  const redacted = redactSettings(withKey);
  assert.equal(publicSettingsEqual(withKey, redacted), true);
});

test('revisions only ever move forward and stay bounded', () => {
  assert.equal(nextRevision({ revision: 0 }), 1);
  assert.equal(nextRevision({ revision: 41 }), 42);
  assert.equal(nextRevision({ revision: 2_147_483_647 }), 2_147_483_647);
  assert.equal(normalizeSharedState({ revision: 1e12 }).revision, 2_147_483_647);
});

test('diffSettings reports exactly the fields that moved', () => {
  const current = createSettings({ provider: 'openai', model: 'gpt-4o-mini' });
  const next = createSettings({ provider: 'openai', model: 'gpt-4o', theme: 'light' });
  const delta = diffSettings(next, current);
  assert.equal(delta.changed, true);
  assert.deepEqual([...delta.fields].sort(), ['model', 'theme']);
  assert.equal(diffSettings(current, current).changed, false);
});
