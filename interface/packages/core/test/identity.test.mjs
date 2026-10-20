import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  AgentClient,
  AgentRequestError,
  emptyAgentIdentity,
  emptyAgentView,
  identityFailureKey,
  isIdentityRefusal,
  readAgentIdentity,
  readAgentView,
  readIdentityReply,
  translate,
} from '../dist/index.js';

/**
 * What the agent is called, as this kit reads it.
 *
 * The name is the one piece of an identity a person sets, and it reaches the prompt
 * from a settings screen or a terminal rather than from the agent's own judgement.
 * That makes this layer responsible for three things nobody else is:
 *
 *   * telling "nobody has named it yet" apart from "the service could not be asked",
 *     because one is a form offering to fill a field in and the other is advice to
 *     start something that is already running;
 *   * not believing a service that answers a question it does not have — a
 *     single-page app answers any unmatched path with its own HTML and a 200; and
 *   * reading the *reason* out of a refusal, which lives in `detail` rather than in
 *     the message and is therefore invisible to anything that reads the message.
 */

const URL_BASE = 'http://127.0.0.1:8720';

function client(handler) {
  const calls = [];
  const fetcher = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? 'GET', body: init.body ? JSON.parse(init.body) : null });
    return handler(String(url), init, calls.length);
  };
  const agent = new AgentClient({ url: URL_BASE, fetchImpl: fetcher, channel: null });
  return { agent, calls };
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

// ------------------------------------------------------------------- reading


test('nothing named reads as nothing named, not as a failure', () => {
  const identity = readAgentIdentity({ configured: false, self_name: '' });
  assert.equal(identity.configured, false);
  assert.equal(identity.selfName, '');
});

test('a name a person chose reads back', () => {
  const identity = readAgentIdentity({ configured: true, self_name: 'Aria' });
  assert.equal(identity.configured, true);
  assert.equal(identity.selfName, 'Aria');
});

test('a name in any script reads back unchanged', () => {
  for (const name of ['Жанна', 'アリア', '小雅']) {
    assert.equal(readAgentIdentity({ configured: true, self_name: name }).selfName, name);
  }
});

test('a flag with no name behind it is not a name', () => {
  /**
   * The flag is not trusted over the field.
   *
   * A surface that believed `configured` alone would show an empty field as though it
   * were the one thing about the agent a person had decided, which is the opposite of
   * what an empty field means here: an offer to fill something in.
   */
  assert.equal(readAgentIdentity({ configured: true, self_name: '' }).configured, false);
  assert.equal(readAgentIdentity({ configured: true, self_name: 42 }).configured, false);
  assert.equal(readAgentIdentity({ configured: true, self_name: null }).configured, false);
});

test('a name is bounded and sanitised like any other text off the wire', () => {
  const long = readAgentIdentity({ configured: true, self_name: 'A'.repeat(500) });
  assert.ok(long.selfName.length <= 64, `${long.selfName.length} characters survived`);
  // A control character never reaches a screen that will draw it.
  const marked = readAgentIdentity({ configured: true, self_name: 'Ari\x1b[31ma' });
  assert.equal(marked.selfName.includes('\x1b'), false);
});

test('a missing section reads as no name rather than throwing', () => {
  assert.deepEqual(readAgentIdentity(undefined), emptyAgentIdentity());
  assert.deepEqual(readAgentIdentity(null), emptyAgentIdentity());
  assert.deepEqual(readAgentIdentity([]), emptyAgentIdentity());
  assert.deepEqual(readAgentIdentity('Aria'), emptyAgentIdentity());
});

test('a snapshot carries the name, so it can be drawn without asking again', () => {
  const view = readAgentView({ identity: { configured: true, self_name: 'Aria' } });
  assert.equal(view.identity.selfName, 'Aria');
});

test('a snapshot with no identity section is an older build, and reads as unnamed', () => {
  assert.deepEqual(readAgentView({}).identity, emptyAgentIdentity());
});

// --------------------------------------------------------- a service that has no route


test('a service without the route does not report an unnamed agent', () => {
  /**
   * The whole reason {@link readIdentityReply} is separate from the total reader.
   *
   * A single-page app answers any unmatched path with its own HTML and a 200. Believing
   * that would report a healthy agent as one nobody has named — a false all-clear on
   * exactly the question the screen was opened to ask — and the form would then offer
   * to save a name into a machine it had not reached.
   */
  assert.equal(readIdentityReply({ '<html>': 'not json' }), null);
  assert.equal(readIdentityReply({ config: true }), null);
  assert.equal(readIdentityReply(undefined), null);
  assert.equal(readIdentityReply('ok'), null);
});

test('a real answer is read as one', () => {
  assert.deepEqual(readIdentityReply({ configured: true, self_name: 'Aria' }), {
    configured: true,
    selfName: 'Aria',
  });
});

// ------------------------------------------------------------------- over the link


test('the link asks the route by name', async () => {
  const { agent, calls } = client(() => jsonResponse({ configured: true, self_name: 'Aria' }));
  const found = await agent.readIdentity();
  assert.equal(found.selfName, 'Aria');
  assert.equal(calls[0].url, `${URL_BASE}/api/identity`);
  assert.equal(calls[0].method, 'GET');
});

test('a service that is not answering reads as null, which is not the same as unnamed', async () => {
  const { agent } = client(() => jsonResponse({}, 503));
  assert.equal(await agent.readIdentity(), null);
});

test('a service with no route at all reads as null', async () => {
  const { agent } = client(() => jsonResponse({ '<html>': 'nope' }, 200));
  assert.equal(await agent.readIdentity(), null);
});

// --------------------------------------------------------------------- writing


test('a name is written as a name', async () => {
  const { agent, calls } = client(() => jsonResponse({ configured: true, self_name: 'Aria' }));
  const written = await agent.saveIdentity('  Aria  ');
  assert.equal(written.selfName, 'Aria');
  // Trimmed before it travels: the agent normalises the same way, and sending the
  // padded form would be asking it to disagree with this kit about what it stored.
  assert.deepEqual(calls[0].body, { self_name: 'Aria' });
  assert.equal(calls[0].url, `${URL_BASE}/api/identity`);
  assert.equal(calls[0].method, 'POST');
});

test('taking the name back is a write, not a delete the agent has to notice', async () => {
  const { agent, calls } = client(() => jsonResponse({ configured: false, self_name: '' }));
  const cleared = await agent.clearIdentity();
  assert.equal(cleared.configured, false);
  assert.deepEqual(calls[0].body, { enabled: false });
});

test('a refused name comes back as the reason, not as a bare failure', async () => {
  const { agent } = client(() => jsonResponse({ error: 'invalid_name' }, 400));
  await assert.rejects(() => agent.saveIdentity('A\nria'), (error) => {
    assert.ok(error instanceof AgentRequestError);
    // `message` reads `agent_refused: invalid_name`, which names the fact that it
    // failed and none of the reason. The reason is in `detail`, and reading the wrong
    // one turns every refusal into "could not reach the agent" — advice to start
    // something that is already running.
    assert.equal(error.detail, 'invalid_name');
    return true;
  });
});

test('a refusal this kit does not know is still a failure, and not a crash', async () => {
  const { agent } = client(() => jsonResponse({ error: 'something_new' }, 400));
  await assert.rejects(() => agent.saveIdentity('Aria'), AgentRequestError);
});

// ------------------------------------------------------------ the reasons in words


test('each refusal has its own sentence, because each has its own fix', () => {
  assert.equal(identityFailureKey(new AgentRequestError('agent_refused', 'invalid_name')), 'agentNameInvalid');
  assert.equal(identityFailureKey(new AgentRequestError('agent_refused', 'home_unwritable')), 'baseModelHomeUnwritable');
  assert.equal(identityFailureKey(new AgentRequestError('agent_refused', 'cross_origin_refused')), 'baseModelCrossOrigin');
  // A missing route is not an outage, and reporting one as the other is what sends
  // somebody to start an agent that is already running.
  assert.equal(identityFailureKey(new AgentRequestError('agent_refused', 'http_404')), 'agentBaseModelNoRoute');
  assert.equal(identityFailureKey(new AgentRequestError('agent_unreachable', 'network')), 'agentBaseModelWriteUnanswered');
  assert.equal(identityFailureKey('not an error at all'), 'agentBaseModelWriteUnanswered');
});

test('the saved line interpolates the name in every language', () => {
  /**
   * The one thing the type system cannot check.
   *
   * `TranslationTable` is `Record<TranslationKey, string>`, so a key missing from
   * Japanese or Chinese is a compile error rather than a blank screen. A *placeholder*
   * missing is not: a translation that says "the agent will answer to ." is a
   * complete string that silently loses the only word in it that mattered, and it
   * renders without any error at all.
   */
  for (const language of ['en', 'ja', 'zh']) {
    const line = translate(language, 'agentNameSaved', { name: 'Aria' });
    assert.match(line, /Aria/, `${language} dropped the name`);
    assert.doesNotMatch(line, /[{}]/, `${language} left a placeholder in`);
  }
});

test('every reason is a sentence in every language', () => {
  for (const language of ['en', 'ja', 'zh']) {
    for (const refusal of ['invalid_name', 'home_unwritable', 'cross_origin_refused']) {
      const key = identityFailureKey(new AgentRequestError('agent_refused', refusal));
      const line = translate(language, key);
      assert.notEqual(line.trim(), '', `${language}: ${key} is blank`);
      assert.doesNotMatch(line, /[{}]/, `${language}: ${key} left a placeholder in`);
    }
  }
});

test('the refusals are the three the agent names', () => {
  assert.deepEqual([...['invalid_name', 'home_unwritable', 'cross_origin_refused']].filter(isIdentityRefusal), [
    'invalid_name',
    'home_unwritable',
    'cross_origin_refused',
  ]);
  for (const notOne of ['unknown_provider', 'key_required', '', null, 42]) {
    assert.equal(isIdentityRefusal(notOne), false, String(notOne));
  }
});

// -------------------------------------------------------------- what a form may offer


test('an empty view offers to name the agent rather than showing nothing', () => {
  assert.deepEqual(emptyAgentView().identity, emptyAgentIdentity());
});
