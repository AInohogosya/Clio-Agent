import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createSettings, DuplexClient } from '../dist/index.js';

function keyedSettings() {
  return createSettings({ provider: 'openai', apiKey: 'test-key', model: 'gpt-4o-mini' });
}

/** A transport that never answers on its own: only an abort ends the request. */
function hangingTransport() {
  return {
    authenticated: true,
    complete: (prompt, history, signal) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('interrupted')), { once: true });
    }),
    discoverModels: async () => ({ models: [], source: 'offline' }),
  };
}

function streamingTransport(script) {
  return {
    authenticated: true,
    complete: async (prompt, history, signal, onDelta) => {
      let answer = '';
      for (const piece of script) {
        if (signal.aborted) throw new Error('aborted');
        onDelta(piece);
        answer += piece;
      }
      return answer;
    },
    discoverModels: async () => ({ models: [], source: 'offline' }),
  };
}

test('a disconnect abandons the reply in flight instead of inventing one', async () => {
  const client = new DuplexClient({ settings: keyedSettings(), transport: hangingTransport() });
  client.connect();
  const pending = client.sendMessage('take your time');
  client.disconnect();

  // Nothing was ever answered, so nothing may be written to the transcript.
  assert.equal(await pending, undefined);
  const snapshot = client.getSnapshot();
  assert.deepEqual(snapshot.messages.map((message) => message.role), ['user']);
  assert.equal(snapshot.status, 'offline');
  assert.equal(snapshot.connected, false);
  client.dispose();
});

test('a disposed client abandons the reply in flight too', async () => {
  const client = new DuplexClient({ settings: keyedSettings(), transport: hangingTransport() });
  client.connect();
  const pending = client.sendMessage('take your time');
  client.dispose();

  assert.equal(await pending, undefined);
  assert.deepEqual(client.getSnapshot().messages.map((message) => message.role), ['user']);
});

test('an abandoned reply does not re-open a closed line', async () => {
  const client = new DuplexClient({ settings: keyedSettings(), transport: hangingTransport() });
  const statuses = [];
  client.subscribe((event) => {
    if (event.type === 'status') statuses.push(event.status);
    if (event.type === 'snapshot') statuses.push(event.snapshot.status);
  });
  client.connect();
  const pending = client.sendMessage('take your time');
  client.disconnect();
  await pending;

  // `disconnect` reports offline; nothing afterwards may claim to be online.
  assert.equal(statuses.at(-1), 'offline');
  assert.equal(statuses.includes('online'), true, 'connecting still reported online once');
  assert.equal(statuses.lastIndexOf('offline'), statuses.length - 1);
  client.dispose();
});

test('a provider failure writes nothing and says what the provider said', async () => {
  const failing = {
    authenticated: true,
    complete: async () => { throw new Error('network_unavailable'); },
    discoverModels: async () => ({ models: [], source: 'offline' }),
  };
  const client = new DuplexClient({ settings: keyedSettings(), transport: failing });
  const events = [];
  client.subscribe((event) => {
    if (event.type === 'error') events.push(event.message);
  });
  client.connect();

  const reply = await client.sendMessage('are you there');

  // The whole point: a failed turn leaves the question standing and invents
  // nothing. The previous build answered from a table of canned sentences, so a
  // wrong key produced confident nonsense in the transcript.
  assert.equal(reply, undefined);
  const snapshot = client.getSnapshot();
  assert.deepEqual(snapshot.messages.map((message) => message.role), ['user']);
  assert.equal(snapshot.streamingText, '');
  assert.equal(snapshot.pending, false);
  assert.equal(snapshot.status, 'online', 'the line is open again, not stuck');
  assert.match(String(snapshot.lastError), /.+/, 'the reason is stated, not swallowed');
  assert.deepEqual(events, ['network_unavailable']);
  client.dispose();
});

test('a refusal that carries a reason keeps the reason', async () => {
  const refusing = {
    authenticated: true,
    complete: async () => {
      const { ProviderRequestError } = await import('../dist/provider-adapter.js');
      throw new ProviderRequestError('http', 'Incorrect API key provided', 401);
    },
    discoverModels: async () => ({ models: [], source: 'offline' }),
  };
  const client = new DuplexClient({ settings: keyedSettings(), transport: refusing });
  client.connect();
  await client.sendMessage('hello?');

  const lastError = String(client.getSnapshot().lastError);
  assert.match(lastError, /401/);
  assert.match(lastError, /Incorrect API key provided/);
  client.dispose();
});

test('the next message clears a recorded failure', async () => {
  let fail = true;
  const client = new DuplexClient({
    settings: keyedSettings(),
    transport: streamingTransport([]),
  });
  client.setTransport({
    authenticated: true,
    complete: async () => {
      if (fail) throw new Error('network_unavailable');
      return 'a real answer';
    },
    discoverModels: async () => ({ models: [], source: 'offline' }),
  });
  client.connect();
  await client.sendMessage('first');
  assert.notEqual(client.getSnapshot().lastError, null);

  fail = false;
  const second = await client.sendMessage('second');
  assert.equal(second?.text, 'a real answer');
  assert.equal(client.getSnapshot().lastError, null);
  client.dispose();
});

test('an unconfigured client refuses rather than answering for itself', async () => {
  const client = new DuplexClient({
    settings: createSettings({ interface: 'direct', provider: 'openai', apiKey: '' }),
  });
  client.connect();
  assert.equal(client.isProviderBacked(), false);
  assert.match(client.unusableReason(), /key/i);

  const reply = await client.sendMessage('anyone there?');
  assert.equal(reply, undefined);
  assert.deepEqual(client.getSnapshot().messages.map((message) => message.role), ['user']);
  assert.match(String(client.getSnapshot().lastError), /key/i);
  client.dispose();
});

test('with no agent link the reason is the link, not a key that is not on screen', async () => {
  // `agent` is the default interface, and there is no API key anywhere on that
  // screen. Reporting "no key" would point the reader at a field they cannot see.
  const client = new DuplexClient({ settings: createSettings() });
  client.connect();
  assert.equal(client.getSnapshot().settings.interface, 'agent');
  assert.equal(client.isProviderBacked(), false);
  assert.match(client.unusableReason(), /not connected/i);

  const reply = await client.sendMessage('anyone there?');
  assert.equal(reply, undefined);
  assert.deepEqual(client.getSnapshot().messages.map((message) => message.role), ['user']);
  assert.match(String(client.getSnapshot().lastError), /not connected/i);
  client.dispose();
});

test('a keyless local provider is usable with no key at all', () => {
  const client = new DuplexClient({
    settings: createSettings({ provider: 'ollama', model: 'llama3.2' }),
  });
  assert.equal(client.isProviderBacked(), true);
  assert.equal(client.unusableReason(), '');
  client.dispose();
});

test('a streamed reply is visible before it is finished', async () => {
  const client = new DuplexClient({
    settings: keyedSettings(),
    transport: streamingTransport(['Hel', 'lo, ', 'world']),
  });
  const seen = [];
  client.subscribe((event) => {
    if (event.type === 'delta') seen.push(event.text);
  });
  client.connect();

  const reply = await client.sendMessage('hi');
  assert.deepEqual(seen, ['Hel', 'lo, ', 'world']);
  assert.equal(reply?.text, 'Hello, world');
  assert.equal(client.getSnapshot().streamingText, '', 'cleared once the turn settles');
  client.dispose();
});

test('an interrupt stops a stream and leaves the line open', async () => {
  const client = new DuplexClient({
    settings: keyedSettings(),
    transport: {
      authenticated: true,
      complete: (prompt, history, signal, onDelta) => new Promise((resolve, reject) => {
        onDelta('the beginning');
        signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      }),
      discoverModels: async () => ({ models: [], source: 'offline' }),
    },
  });
  client.connect();
  const pending = client.sendMessage('go on');
  await new Promise((resolve) => setTimeout(resolve, 10));
  client.interrupt();
  assert.equal(await pending, undefined);

  const snapshot = client.getSnapshot();
  assert.deepEqual(snapshot.messages.map((message) => message.role), ['user']);
  assert.equal(snapshot.streamingText, '');
  assert.equal(snapshot.pending, false);
  // An interruption is something that happened, not a condition the line rests
  // in: the old code left the status pinned at "interrupted" until the next turn.
  assert.equal(snapshot.status, 'online');
  client.dispose();
});

test('a transport attached after construction is used', async () => {
  // The browser finds the bridge a microtask after the first render, so a
  // transport read in the constructor was always null and the page silently
  // answered for itself. These settings carry no key, so the transport is the
  // only thing that can make a reply possible.
  const client = new DuplexClient({ settings: createSettings({ provider: 'openai', model: 'gpt-4o-mini' }) });
  assert.equal(client.isProviderBacked(), false);
  client.setTransport(streamingTransport(['from the bridge']));
  assert.equal(client.isProviderBacked(), true);
  client.connect();
  const reply = await client.sendMessage('hello');
  assert.equal(reply?.text, 'from the bridge');
  client.dispose();
});

test('a second message supersedes the first, and the abandoned reply is dropped', async () => {
  const client = new DuplexClient({
    settings: keyedSettings(),
    transport: {
      authenticated: true,
      complete: (prompt, history, signal) => new Promise((resolve, reject) => {
        if (prompt === 'first') {
          setTimeout(() => resolve('the first answer'), 30);
        } else {
          resolve('the second answer');
        }
        signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      }),
      discoverModels: async () => ({ models: [], source: 'offline' }),
    },
  });
  client.connect();
  const first = client.sendMessage('first');
  const second = client.sendMessage('second');

  assert.equal((await second)?.text, 'the second answer');
  assert.equal(await first, undefined, 'the abandoned reply writes nothing');
  const texts = client.getSnapshot().messages.map((message) => message.text);
  assert.deepEqual(texts, ['first', 'second', 'the second answer']);
  client.dispose();
});

test('adopting a history replaces the transcript and resets the position', () => {
  const client = new DuplexClient({ settings: keyedSettings() });
  client.connect();
  client.adoptHistory([{ id: 'x', role: 'assistant', text: 'from elsewhere', createdAt: 5 }]);
  const snapshot = client.getSnapshot();
  assert.equal(snapshot.messages.length, 1);
  assert.equal(snapshot.messages[0].text, 'from elsewhere');
  assert.equal(snapshot.lastActivityAt, 5);
  client.dispose();
});

test('a snapshot handed to a listener is a copy', () => {
  const client = new DuplexClient({ settings: keyedSettings() });
  let seen = null;
  client.subscribe((event) => {
    if (event.type === 'snapshot') seen = event.snapshot;
  });
  client.connect();
  seen.messages.push({ id: 'forged', role: 'assistant', text: 'not a real reply', createdAt: 1 });
  seen.settings.accent = '#000000';
  assert.equal(client.getSnapshot().messages.length, 0);
  assert.notEqual(client.getSnapshot().settings.accent, '#000000');
  client.dispose();
});

// -------------------------------------------------------------- discovery

test('in agent mode a catalogue is never fetched by the page itself', async () => {
  // This client holds no credential in agent mode, and a page served by the agent
  // is not allowed to reach a provider anyway. So with nothing to ask, it answers
  // with the fallback list and says what is missing — rather than reporting the
  // provider as unreachable, which sends somebody to look in the wrong place.
  const client = new DuplexClient({ settings: createSettings({ interface: 'agent' }) });
  client.connect();
  const result = await client.discoverModels(undefined, createSettings({
    provider: 'openrouter', model: 'a/1', interface: 'agent',
  }));
  assert.equal(result.source, 'offline');
  assert.equal(result.error, 'agent_unreachable');
  assert.ok(result.models.length > 0, 'a list of guesses, clearly labelled as such');
  assert.ok(result.models.includes('openai/gpt-4o-mini'), 'the provider that was asked about');
  client.dispose();
});

test('a direct client still fetches for itself when it is the one holding the key', async () => {
  // The page can do this when there is no service to ask, and it is the only case
  // in which the fallback is a real answer rather than a labelled guess.
  const client = new DuplexClient({ settings: createSettings({ interface: 'direct' }) });
  client.connect();
  const result = await client.discoverModels(undefined, createSettings({
    interface: 'direct', provider: 'ollama', baseUrl: 'http://127.0.0.1:9/v1', model: 'llama3.2',
  }));
  assert.equal(result.source, 'offline');
  assert.equal(result.error, 'network_unavailable', 'the provider is the one that could not be reached');
  client.dispose();
});
