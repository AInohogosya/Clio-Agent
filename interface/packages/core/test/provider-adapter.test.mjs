import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  createSettings,
  discoverProviderModels,
  ProviderRequestError,
  requestProviderCompletion,
} from '../dist/index.js';

/** A response whose body is a readable stream of the given text. */
function streamResponse(chunks, init = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: init.status ?? 200,
    headers: { 'content-type': init.contentType ?? 'text/event-stream', ...(init.headers ?? {}) },
  });
}

function jsonResponse(payload, init = {}) {
  return new Response(JSON.stringify(payload), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json' },
  });
}

/** A fetcher that records the request it was given and replies with `reply`. */
function recorder(reply) {
  const calls = [];
  const fetcher = async (input, init) => {
    const raw = typeof init?.body === 'string' ? init.body : '';
    calls.push({ url: String(input), init, body: raw ? JSON.parse(raw) : null });
    return reply(calls.length);
  };
  return { calls, fetcher };
}

const history = [
  { role: 'system', content: 'You are brief.' },
  { role: 'user', content: 'first question' },
  { role: 'assistant', content: 'first answer' },
];

// ---------------------------------------------------------------- system turns

test('a system message is never sent as a turn to Anthropic', async () => {
  const { calls, fetcher } = recorder(() => jsonResponse({
    content: [{ type: 'text', text: 'ok' }],
  }));
  const settings = createSettings({ provider: 'anthropic', apiKey: 'sk-ant-test' });
  await requestProviderCompletion(settings, 'second question', fetcher, undefined, history);

  const sent = calls[0].body;
  assert.deepEqual(sent.system, 'You are brief.');
  for (const message of sent.messages) {
    // Anthropic rejects a `system` role inside `messages` outright, so this was
    // a guaranteed 400 for any conversation that carried one.
    assert.notEqual(message.role, 'system');
  }
  assert.deepEqual(sent.messages.at(-1), { role: 'user', content: 'second question' });
  assert.equal(calls[0].init.headers['anthropic-version'], '2023-06-01');
  assert.equal(calls[0].init.headers['x-api-key'], 'sk-ant-test');
});

test('a system message is never sent as a turn to Gemini', async () => {
  const { calls, fetcher } = recorder(() => jsonResponse({
    candidates: [{ content: { parts: [{ text: 'ok' }] } }],
  }));
  const settings = createSettings({ provider: 'gemini', apiKey: 'gemini-test' });
  await requestProviderCompletion(settings, 'second question', fetcher, undefined, history);

  const sent = calls[0].body;
  assert.deepEqual(sent.systemInstruction, { parts: [{ text: 'You are brief.' }] });
  for (const turn of sent.contents) {
    // Gemini accepts only `user` and `model`; a `system` role is an error.
    assert.ok(turn.role === 'user' || turn.role === 'model');
  }
  assert.equal(sent.contents[0].role, 'user');
  assert.equal(sent.contents[1].role, 'model', 'an assistant turn becomes `model`');
  assert.match(calls[0].url, /\/models\/gemini-2\.0-flash:generateContent$/);
});

test('a system message is kept as a turn for an OpenAI-compatible provider', async () => {
  const { calls, fetcher } = recorder(() => jsonResponse({
    choices: [{ message: { content: 'ok' } }],
  }));
  await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'second question',
    fetcher,
    undefined,
    history,
  );
  assert.equal(calls[0].body.messages[0].role, 'system');
  assert.match(calls[0].url, /\/chat\/completions$/);
});

test('a conversation that opens on an assistant turn is trimmed, not rejected', async () => {
  const { calls, fetcher } = recorder(() => jsonResponse({ choices: [{ message: { content: 'ok' } }] }));
  await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'and now?',
    fetcher,
    undefined,
    [{ role: 'assistant', content: 'an opening remark' }, { role: 'user', content: 'hello' }],
  );
  assert.equal(calls[0].body.messages[0].role, 'user');
});

// ------------------------------------------------------------------ streaming

test('an OpenAI-compatible stream is delivered increment by increment', async () => {
  const frames = [
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    'data: [DONE]\n\n',
  ];
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async () => streamResponse(frames),
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  assert.equal(text, 'Hello');
  assert.deepEqual(seen, ['Hel', 'lo']);
});

test('an Anthropic stream is delivered increment by increment', async () => {
  const frames = [
    'event: message_start\ndata: {"type":"message_start"}\n\n',
    'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\n',
    'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n',
    'event: message_stop\ndata: {"type":"message_stop"}\n\n',
  ];
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'anthropic', apiKey: 'sk-ant-test' }),
    'hi',
    async () => streamResponse(frames),
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  assert.equal(text, 'Hello');
  assert.deepEqual(seen, ['Hel', 'lo']);
});

test('a Gemini stream is delivered increment by increment', async () => {
  const frames = [
    'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\n\n',
    'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]},"finishReason":"STOP"}]}\n\n',
  ];
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'gemini', apiKey: 'gemini-test' }),
    'hi',
    async () => streamResponse(frames),
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  assert.equal(text, 'Hello');
  assert.deepEqual(seen, ['Hel', 'lo']);
});

test('a frame split across two reads is still parsed as one frame', async () => {
  const { calls, fetcher } = recorder(() => streamResponse([
    'data: {"choices":[{"delta":{"con',
    'tent":"split"}}]}\n\ndata: [DONE]\n\n',
  ]));
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    fetcher,
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  assert.equal(calls[0].body.stream, true);
  assert.equal(text, 'split');
  assert.deepEqual(seen, ['split']);
});

test('a server that cannot stream is read as one ordinary body, not refused', async () => {
  // Refusing to talk to a provider that could not stream was the same mistake
  // as refusing a redirect, with a worse outcome for whoever was waiting.
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async () => jsonResponse({ choices: [{ message: { content: 'a whole answer' } }] }, { status: 200 }),
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  assert.equal(text, 'a whole answer');
  assert.deepEqual(seen, ['a whole answer'], 'the answer still reaches a live surface');
});

test('a mid-stream error frame is reported, not swallowed', async () => {
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: 'sk-test' }),
      'hi',
      async () => streamResponse([
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        'data: {"error":{"message":"rate limit exceeded"}}\n\n',
      ]),
      undefined,
      [],
      { onDelta: () => undefined },
    ),
    (error) => error instanceof ProviderRequestError && /rate limit exceeded/.test(error.detail),
  );
});

// ------------------------------------------------------------------- budgets

/** Lets pending microtasks and stream reads run, without advancing the clock. */
async function flush(times = 12) {
  for (let index = 0; index < times; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

test('a long answer is not cut off, because the budget is a stall budget', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const encoder = new TextEncoder();
  let stream;
  const body = new ReadableStream({ start(controller) { stream = controller; } });
  const seen = [];
  const pending = requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async () => new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    undefined,
    [],
    { onDelta: (piece) => seen.push(piece) },
  );
  const settled = pending.then((value) => ({ value }), (error) => ({ error }));

  // 200 seconds of elapsed time, in gaps short enough to be healthy. A single
  // fixed 30-second cap — or a single unre-armed guard — would have ended this
  // at the first or second tick.
  for (const piece of ['one ', 'two ', 'three']) {
    stream.enqueue(encoder.encode(`data: {"choices":[{"delta":{"content":${JSON.stringify(piece)}}}]}\n\n`));
    await flush();
    t.mock.timers.tick(100_000);
  }
  stream.close();

  const outcome = await settled;
  assert.equal(outcome.error, undefined, outcome.error && outcome.error.toString());
  assert.equal(outcome.value, 'one two three');
  assert.deepEqual(seen, ['one ', 'two ', 'three']);
});

test('a stream that goes silent is reported as a timeout, not an interruption', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const body = new ReadableStream({ start() { /* never enqueues, never closes */ } });
  const pending = requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async () => new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    undefined,
    [],
    { onDelta: () => undefined },
  );
  const settled = pending.then((value) => ({ value }), (error) => ({ error }));
  await flush();
  t.mock.timers.tick(200_000);

  const outcome = await settled;
  assert.ok(outcome.error instanceof ProviderRequestError, 'the stall ended the request');
  assert.equal(outcome.error.code, 'timeout');
});

test('an interrupt during a stream is reported as an interruption', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const body = new ReadableStream({ start() { /* waits */ } });
  const controller = new AbortController();
  const pending = requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async () => new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    controller.signal,
    [],
    { onDelta: () => undefined },
  );
  const settled = pending.then((value) => ({ value }), (error) => ({ error }));
  await flush();
  controller.abort();

  const outcome = await settled;
  assert.ok(outcome.error instanceof ProviderRequestError);
  assert.equal(outcome.error.code, 'aborted');
});

// ----------------------------------------------------------------- redirects

test('a redirect that stays on the same origin is followed', async () => {
  const seen = [];
  const text = await requestProviderCompletion(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    'hi',
    async (input) => {
      seen.push(String(input));
      if (seen.length === 1) {
        return new Response(null, { status: 307, headers: { location: '/v1/other/chat/completions' } });
      }
      return jsonResponse({ choices: [{ message: { content: 'followed' } }] });
    },
  );
  assert.equal(text, 'followed');
  assert.equal(seen.length, 2);
  assert.match(seen[1], /\/v1\/other\/chat\/completions$/);
});

test('a redirect off the origin is refused, so a key is never re-sent elsewhere', async () => {
  let hops = 0;
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'anthropic', apiKey: 'sk-ant-test' }),
      'hi',
      async () => {
        hops += 1;
        // `x-api-key` is a custom header, and unlike `Authorization` a fetch
        // implementation has no reason to strip it on a cross-origin hop.
        return new Response(null, { status: 302, headers: { location: 'https://elsewhere.example/collect' } });
      },
    ),
    (error) => error instanceof ProviderRequestError
      && error.code === 'redirect_refused'
      && /elsewhere\.example/.test(error.detail),
  );
  assert.equal(hops, 1, 'the first hop is the last one attempted');
});

// -------------------------------------------------------------------- errors

test('a refusal keeps the provider own explanation', async () => {
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: 'sk-test' }),
      'hi',
      async () => jsonResponse({ error: { message: 'Incorrect API key provided' } }, { status: 401 }),
    ),
    (error) => error instanceof ProviderRequestError
      && error.status === 401
      && error.detail === 'Incorrect API key provided'
      && error.toString() === 'http_401: Incorrect API key provided',
  );
});

test('a truncated answer is reported as a limit, not as silence', async () => {
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: 'sk-test' }),
      'hi',
      async () => jsonResponse({ choices: [{ message: { content: '' }, finish_reason: 'length' }] }),
    ),
    (error) => error instanceof ProviderRequestError
      && error.detail.includes('token limit'),
  );
});

test('an unparseable body is reported rather than retried forever', async () => {
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: 'sk-test' }),
      'hi',
      async () => new Response('<html>gateway</html>', { headers: { 'content-type': 'text/html' } }),
    ),
    (error) => error instanceof ProviderRequestError && error.code === 'no_text_in_response',
  );
});

test('a missing key is refused before a request is made', async () => {
  let called = false;
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: '' }),
      'hi',
      async () => {
        called = true;
        return jsonResponse({});
      },
    ),
    (error) => error instanceof ProviderRequestError && error.code === 'missing_credentials',
  );
  assert.equal(called, false);
});

test('an abort is reported as an abort, not as a network failure', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    requestProviderCompletion(
      createSettings({ provider: 'openai', apiKey: 'sk-test' }),
      'hi',
      async () => jsonResponse({ choices: [{ message: { content: 'x' } }] }),
      controller.signal,
    ),
    (error) => error instanceof ProviderRequestError && error.code === 'aborted',
  );
});

// ---------------------------------------------------------------- catalogues

test('a keyless provider is asked for its catalogue without a credential', async () => {
  const { calls, fetcher } = recorder(() => jsonResponse({ data: [{ id: 'llama3.2' }, { id: 'qwen2.5' }] }));
  const result = await discoverProviderModels(
    createSettings({ provider: 'ollama', model: 'llama3.2' }),
    fetcher,
  );
  assert.equal(result.source, 'remote');
  assert.deepEqual(result.models, ['llama3.2', 'qwen2.5']);
  assert.equal(calls.length, 1, 'the old code refused to even try');
  assert.equal(calls[0].init.headers.Authorization, undefined);
});

test('a keyed provider without a key falls back to the offline catalogue', async () => {
  let called = false;
  const result = await discoverProviderModels(
    createSettings({ provider: 'openai', apiKey: '' }),
    async () => {
      called = true;
      return jsonResponse({});
    },
  );
  assert.equal(called, false);
  assert.equal(result.source, 'offline');
  assert.equal(result.error, 'missing_credentials');
  assert.ok(result.models.length > 0, 'a usable offline list is still offered');
});

test('a Gemini catalogue is read from its own shape', async () => {
  const { fetcher } = recorder(() => jsonResponse({
    models: [{ name: 'models/gemini-2.0-flash' }, { name: 'models/gemini-1.5-pro' }],
  }));
  const result = await discoverProviderModels(
    createSettings({ provider: 'gemini', apiKey: 'gemini-test' }),
    fetcher,
  );
  assert.deepEqual(result.models, ['gemini-1.5-pro', 'gemini-2.0-flash']);
});

test('an unreachable endpoint still yields the offline catalogue', async () => {
  const result = await discoverProviderModels(
    createSettings({ provider: 'openai', apiKey: 'sk-test' }),
    async () => { throw new Error('ECONNREFUSED'); },
  );
  assert.equal(result.source, 'offline');
  assert.equal(result.error, 'network_unavailable');
  assert.ok(result.models.length > 0);
});
