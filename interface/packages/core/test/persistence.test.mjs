import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import * as core from '../dist/index.js';
import { createSettings, normalizeMessages, normalizeSettings } from '../dist/index.js';

/**
 * Where state is kept, and where it is not.
 *
 * The file under the configuration directory is the only store, so the page and
 * the terminal cannot disagree and a conversation is the same conversation in
 * any browser. These tests hold that line: the public surface offers no way to
 * keep anything in the browser, and the normalisers that read a payload from
 * anywhere still answer with the one valid shape.
 */

test('the core package offers no browser-side store', () => {
  // A removed API is a promise, not an omission. Anyone — or any future change —
  // reaching for local persistence has to be refused by the surface, because
  // what it costs is invisible from here: settings that reappear under one
  // hostname, and vanish in the next browser.
  for (const name of [
    'getBrowserStorage',
    'createMemoryStorage',
    'loadPersistedState',
    'saveSettings',
    'saveMessages',
    'STORAGE_KEYS',
    'StorageLike',
    'PersistedState',
  ]) {
    assert.equal(name in core, false, `${name} is still exported`);
  }
});

test('settings read from anything but this code are the one valid shape', () => {
  const settings = normalizeSettings({
    provider: 'anthropic',
    model: 'claude-sonnet-5',
    language: 'zh',
    theme: 'light',
    accent: '#00FFAA',
    // Fields this version does not have are dropped rather than carried.
    simulation: true,
  });
  assert.equal(settings.provider, 'anthropic');
  assert.equal(settings.model, 'claude-sonnet-5');
  assert.equal(settings.language, 'zh');
  assert.equal(settings.theme, 'light');
  assert.equal(settings.accent, '#00FFAA');
  assert.equal('simulation' in settings, false);
  assert.deepEqual(Object.keys(settings).sort(), [
    'accent', 'agentPerson', 'agentUrl', 'apiKey', 'baseUrl', 'interface',
    'language', 'model', 'provider', 'theme',
  ]);
});

test('a hostile settings payload is normalised rather than believed', () => {
  // Not an object at all, and an object of the wrong shape, both have to come
  // back as defaults: a store that trusted its input would let a corrupt file
  // decide the endpoint the next reply is signed for.
  for (const payload of [null, undefined, 'nope', 42, [{ apiKey: 'container-secret' }]]) {
    assert.deepEqual(normalizeSettings(payload), createSettings());
  }
  assert.equal(normalizeSettings({ provider: 'openai', accent: 'red' }).accent, createSettings().accent);
  assert.equal(normalizeSettings({ provider: 'openai', model: '' }).model, createSettings({ provider: 'openai' }).model);
});

test('a transcript is trimmed to real, bounded messages', () => {
  const messages = normalizeMessages([
    { id: 'a', role: 'user', text: 'hello', createdAt: 2 },
    { id: 'b', role: 'narrator', text: 'not a role', createdAt: 3 },
    { id: '', role: 'user', text: 'no id', createdAt: 4 },
    { id: 'c', text: 'no role', createdAt: 5 },
    'not an object',
    null,
    { id: 'd', role: 'assistant', text: 'hi', createdAt: 1 },
  ]);
  // Order is the file's to decide, not this function's: it only refuses what it
  // cannot use. Anything that has to be ordered asks `mergeMessages`, which owns
  // that decision and states it in the signature.
  assert.deepEqual(messages.map((message) => message.id), ['a', 'd']);
  assert.equal(messages[0].text, 'hello');
  assert.deepEqual(normalizeMessages(undefined), []);
  assert.deepEqual(normalizeMessages({ a: 1 }), []);
});

test('a message carrying control characters is refused, not repaired', () => {
  // Admitted-then-scrubbed would be the friendlier reading, but a repair cannot
  // tell an escape sequence that was meant from one that was injected, so the
  // entry is dropped whole and the rest of the conversation survives.
  const esc = String.fromCharCode(0x1b);
  const messages = normalizeMessages([
    { id: 'painted', role: 'assistant', text: `before${esc}[31mafter`, createdAt: 1 },
    { id: 'plain', role: 'assistant', text: 'before after', createdAt: 2 },
    { id: 'huge', role: 'user', text: 'x'.repeat(100_001), createdAt: 3 },
  ]);
  assert.deepEqual(messages.map((message) => message.id), ['plain']);
});

test('stored text is normalised on its way in', () => {
  // Line endings are the one thing sanitising can repair without guessing:
  // every other control character is refused, but CRLF is unambiguously a
  // newline wherever it came from.
  const [message] = normalizeMessages([
    { id: 'a', role: 'assistant', text: 'first line\r\nsecond line', createdAt: 1 },
  ]);
  assert.equal(message.text, 'first line\nsecond line');
});

