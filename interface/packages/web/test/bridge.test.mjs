import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createSettings } from '@project-phone/core';
import { BRIDGE_ROUTES, bridgePath, BRIDGE_PREFIX } from '../src/bridge-protocol.ts';
import { isLoopbackAddress, isLoopbackAuthority, isLoopbackRemoteAddress, isTrustedOrigin, mergeIncoming } from '../vite.config.ts';

function state(overrides = {}) {
  return {
    version: 2,
    revision: 4,
    updatedAt: 1_700_000_000_000,
    origin: 'cli',
    transport: 'file',
    settings: createSettings({ provider: 'openai', model: 'gpt-4o-mini' }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
    ...overrides,
  };
}

function message(id, createdAt, text = id) {
  return { id, role: 'user', text, createdAt };
}

function request(headers) {
  return { headers };
}

test('the bridge is mounted on a private path', () => {
  // Both ends of the bridge import this one list, so a route cannot be renamed
  // on the server and left behind in the page.
  assert.equal(BRIDGE_PREFIX, '/__phone');
  for (const [name, route] of Object.entries(BRIDGE_ROUTES)) {
    assert.equal(bridgePath(name), `/__phone/${route}`, name);
  }
});

test('only loopback hosts are accepted', () => {
  for (const host of ['127.0.0.1', '127.0.0.53', 'localhost', 'app.localhost', '::1', '0:0:0:0:0:0:0:1', '[::1]']) {
    assert.equal(isLoopbackAddress(host), true, host);
  }
  for (const host of ['10.0.0.1', '192.168.1.5', '172.16.0.1', '0.0.0.0', '8.8.8.8', 'example.com', '127.0.0.1.evil.com', '']) {
    assert.equal(isLoopbackAddress(host), false, host);
  }
});

test('a peer address is accepted in both IPv4 and IPv4-mapped form', () => {
  for (const address of ['127.0.0.1', '::1', 'localhost', '::ffff:127.0.0.1', '::ffff:7f00:1']) {
    assert.equal(isLoopbackRemoteAddress(address), true, address);
  }
  for (const address of ['10.0.0.1', '::ffff:10.0.0.1', '203.0.113.7', undefined, '']) {
    assert.equal(isLoopbackRemoteAddress(address), false, String(address));
  }
});

test('a rebound host header is refused', () => {
  for (const authority of ['127.0.0.1:3000', 'localhost:5173', '[::1]:3000', '127.0.0.1']) {
    assert.equal(isLoopbackAuthority(authority), true, authority);
  }
  for (const authority of ['attacker.example:3000', 'phone.attacker.example', '10.0.0.5:3000', '', '  ', undefined]) {
    assert.equal(isLoopbackAuthority(authority), false, String(authority));
  }
});

test('a cross-site origin is refused even when the host looks local', () => {
  assert.equal(isTrustedOrigin(request({})), true, 'a request without an origin is same-machine');
  assert.equal(isTrustedOrigin(request({ origin: 'http://127.0.0.1:3000' })), true);
  assert.equal(isTrustedOrigin(request({ origin: 'http://localhost:3000' })), true);
  assert.equal(isTrustedOrigin(request({ origin: 'https://attacker.example' })), false);
  assert.equal(isTrustedOrigin(request({ origin: 'http://127.0.0.1.attacker.example' })), false);
  assert.equal(isTrustedOrigin(request({ origin: 'http://10.0.0.1:3000' })), false);
  assert.equal(isTrustedOrigin(request({ origin: 'file://' })), false);
  assert.equal(isTrustedOrigin(request({ origin: 'not a url' })), false);
  assert.equal(isTrustedOrigin(request({ origin: 'http://user:pass@127.0.0.1:3000' })), false);
  assert.equal(isTrustedOrigin(request({ origin: '' })), false);
});

test('a settings change from the browser is accepted', () => {
  const result = mergeIncoming(state(), {
    settings: { provider: 'ollama', apiKey: '', baseUrl: 'http://localhost:11434/v1', model: 'llama3.2', language: 'ja', theme: 'light', accent: '#0EA5E9' },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.provider, 'ollama');
  assert.equal(result.state.settings.language, 'ja');
  assert.equal(result.state.settings.accent, '#0EA5E9');
  assert.equal(result.state.origin, 'web');
  assert.equal(result.changed, true);
});

test('a push that changes nothing is reported as unchanged', () => {
  // Two surfaces each re-sending their view on every unrelated change would
  // otherwise churn the revision forever: every write is a rename, and every
  // rename is a directory event the other surface has to read back.
  const current = state();
  const result = mergeIncoming(current, { settings: current.settings, messages: [] });
  assert.equal(result.ok, true);
  assert.equal(result.changed, false);
});

test('changing provider is allowed, and the old key does not travel with it', () => {
  // The previous rule refused a provider change whenever a key was on file,
  // which locked anyone with a hosted key out of the models on their own
  // machine — a keyless provider can never satisfy "supply the credential that
  // goes with it".
  const current = state({
    settings: createSettings({ provider: 'openai', model: 'gpt-4o-mini', apiKey: 'file-key-1234' }),
    credential: { present: true, source: 'file', hint: '••••1234' },
  });
  const result = mergeIncoming(current, {
    settings: { provider: 'ollama', apiKey: '', baseUrl: 'http://localhost:11434/v1', model: 'llama3.2' },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.provider, 'ollama');
  // The invariant that actually matters: one provider's key is never posted to
  // another provider's endpoint.
  assert.equal(result.state.settings.apiKey, '');
});

test('a provider change with its own key is kept', () => {
  const current = state({
    settings: createSettings({ provider: 'openai', model: 'gpt-4o-mini', apiKey: 'file-key-1234' }),
    credential: { present: true, source: 'file', hint: '••••1234' },
  });
  const result = mergeIncoming(current, {
    settings: {
      provider: 'anthropic',
      apiKey: 'browser-key-5678',
      baseUrl: 'https://api.anthropic.com/v1',
      model: 'claude-3-5-sonnet-latest',
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.apiKey, 'browser-key-5678');
});

test('repinting the endpoint of the same provider is not a key reset', () => {
  // A same-provider endpoint change is allowed to keep the key, because the
  // same key may legitimately be served by a compatible proxy. The endpoint is
  // validated before any request is signed, so a hostile host never receives it.
  const current = state({
    settings: createSettings({ provider: 'openai', model: 'gpt-4o-mini', apiKey: 'file-key-1234' }),
    credential: { present: true, source: 'file', hint: '••••1234' },
  });
  const result = mergeIncoming(current, {
    settings: { provider: 'openai', baseUrl: 'https://gateway.internal/v1', model: 'gpt-4o', apiKey: '' },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.apiKey, 'file-key-1234');
});

test('a redacted snapshot never clears the stored key', () => {
  const current = state({
    settings: createSettings({ provider: 'openai', apiKey: 'file-key-1234' }),
    credential: { present: true, source: 'file', hint: '••••1234' },
  });
  const result = mergeIncoming(current, {
    settings: { provider: 'openai', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o', apiKey: '' },
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.apiKey, 'file-key-1234');
});

test('a supplied credential reaches the store but not the file reader', () => {
  const result = mergeIncoming(state(), {
    settings: { provider: 'openai', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o', apiKey: 'browser-key-5678' },
  });
  assert.equal(result.state.settings.apiKey, 'browser-key-5678');
  // The credential status still describes the file, which the write path owns.
  assert.equal(result.state.credential.present, false);
});

test('the transcript is merged as a union rather than replaced', () => {
  const current = state({ messages: [message('a', 1), message('b', 2)] });
  const result = mergeIncoming(current, {
    settings: current.settings,
    messages: [message('b', 2), message('c', 3)],
  });
  assert.deepEqual(result.state.messages.map((item) => item.id), ['a', 'b', 'c']);
});

test('a hostile payload is neutralised rather than trusted', () => {
  const result = mergeIncoming(state(), {
    settings: { provider: 'skynet', apiKey: '', baseUrl: 'http://169.254.169.254/v1', model: '../../etc/passwd', accent: 'red' },
    messages: [{ id: '', role: 'wizard', text: 'x', createdAt: -5 }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.state.settings.provider, 'openai');
  assert.equal(result.state.settings.accent, '#F97316');
  assert.deepEqual(result.state.messages, []);
});

test('an empty or absent body leaves the state alone', () => {
  for (const payload of [undefined, null, {}, 'nonsense', 42]) {
    const result = mergeIncoming(state(), payload);
    assert.equal(result.ok, true, JSON.stringify(payload));
    assert.equal(result.state.settings.provider, 'openai');
    assert.equal(result.state.revision, 4);
    assert.equal(result.changed, false);
  }
});

test('the merge never widens the revision it was given', () => {
  const result = mergeIncoming(state({ revision: 9 }), { settings: createSettings({ theme: 'light' }) });
  assert.equal(result.state.revision, 9);
});
