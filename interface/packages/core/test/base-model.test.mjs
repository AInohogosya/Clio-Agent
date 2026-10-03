import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  agentDraftProblem,
  AgentClient,
  AgentRequestError,
  BASE_MODEL_REFUSAL_KEYS,
  baseModelFailureKey,
  baseModelProblem,
  createSettings,
  discoveryProblem,
  emptyAgentBaseModel,
isBaseModelRefusal,
  probeSignature,
  readAgentBaseModel,
  readAgentView,
  readEnvApiKeys,
  sanitizeDraftSettings,
  setupProblem,
  shouldProbeCatalog,
  translate,
} from '../dist/index.js';

/**
 * The agent's base model.
 *
 * Two halves, and the split matters: the *link* is about what a surface may
 * write without ever holding a credential, and the *rule* is about what has to be
 * true before it writes anything. A form that demanded the key back on every save
 * would make the whole thing unusable, so the tests below are as much about the
 * key never travelling as about the model arriving.
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

const CONFIGURED = {
  configured: true,
  provider: 'openrouter',
  protocol: 'openai_compat',
  model: 'stealth/space-bunny-alpha',
  base_url: 'https://openrouter.ai/api/v1',
  key_present: true,
  key_hint: '••••6a94',
};

// ------------------------------------------------------------------- reading


test('nothing configured reads as nothing, not as a failure', async () => {
  const { agent } = client(() => jsonResponse({ ...emptyAgentBaseModel() }));
  const found = await agent.readBaseModel();
  assert.equal(found.configured, false);
  assert.equal(found.keyPresent, false);
});

test('the base model reads back with the vendor beside the protocol', async () => {
  const { agent } = client(() => jsonResponse(CONFIGURED));
  const found = await agent.readBaseModel();
  assert.equal(found.provider, 'openrouter');
  assert.equal(found.protocol, 'openai_compat');
  assert.equal(found.model, 'stealth/space-bunny-alpha');
  assert.equal(found.keyPresent, true);
});

test('a base model reading a variable says which one', async () => {
  // A key that lives in the environment is recorded by name, so the form can
  // name it under an empty field instead of showing an empty field and silence.
  const { agent } = client(() => jsonResponse({
    ...CONFIGURED,
    key_present: false,
    key_hint: '',
    key_env: 'OPENROUTER_API_KEY',
    key_env_present: true,
  }));
  const found = await agent.readBaseModel();
  assert.equal(found.keyEnv, 'OPENROUTER_API_KEY');
  assert.equal(found.keyEnvPresent, true);
  assert.equal(found.keyPresent, false);
});

test('a variable the process cannot see is reported as absent, not as a key', () => {
  // The file and the process disagree often enough, and a form that believed the
  // file alone would promise a credential that is not there.
  const found = readAgentBaseModel({ ...CONFIGURED, key_env: 'NOT_EXPORTED', key_env_present: false });
  assert.equal(found.keyEnv, 'NOT_EXPORTED');
  assert.equal(found.keyEnvPresent, false);
});

test('the environment report is read as facts, and a hostile one as nothing', async () => {
  const { agent, calls } = client(() => jsonResponse({
    keys: [
      { provider: 'openai', variables: ['OPENAI_API_KEY'], present: true, variable: 'OPENAI_API_KEY', hint: '••••9999' },
      { provider: 'groq', variables: 'GROQ_API_KEY', present: 'yes' },
      'not an entry',
      { variables: ['X'] },
    ],
  }));
  const keys = await agent.readEnvApiKeys();
  assert.equal(calls.at(-1).url, `${URL_BASE}/api/env-keys`);
  assert.equal(keys.length, 2, 'an entry with no provider is not a provider');
  assert.deepEqual(keys[0], {
    provider: 'openai',
    variables: ['OPENAI_API_KEY'],
    present: true,
    variable: 'OPENAI_API_KEY',
    hint: '••••9999',
  });
  // Coerced, never believed: a string where a list belongs is no list, and only
  // a real `true` is a credential this process can see.
  assert.deepEqual(keys[1], { provider: 'groq', variables: [], present: false, variable: '', hint: '' });
  for (const body of [null, 'html', [], { keys: 'no' }]) {
    assert.deepEqual(readEnvApiKeys(body), [], JSON.stringify(body));
  }
});

test('a service that cannot report an environment is not a failure', async () => {
  // An empty list means "no button to offer", and a settings screen with no
  // button is a screen that still works. A rejection here would be worse than
  // the thing it reports.
  for (const handler of [() => jsonResponse({}, 404), () => jsonResponse({}, 503), () => { throw new Error('down'); }]) {
    const { agent } = client(handler);
    assert.deepEqual(await agent.readEnvApiKeys(), []);
  }
  const orphan = new AgentClient({ url: 'http://example.com:8720', channel: null });
  assert.deepEqual(await orphan.readEnvApiKeys(), []);
});

test('a key never travels back over the link', async () => {
  // The agent answers with a hint and nothing else; a surface that could read
  // the key could show it, and could also have been sent the one it just typed.
  const { agent } = client(() => jsonResponse({ ...CONFIGURED, api_key: 'sk-should-not-be-here' }));
  const found = await agent.readBaseModel();
  assert.equal(Object.prototype.hasOwnProperty.call(found, 'api_key'), false);
  assert.equal(found.keyHint, '••••6a94');
});

test('an agent that cannot be asked is null, not an empty base model', async () => {
  // "Nothing is configured" and "nobody answered" are different states, and a
  // settings screen that conflated them would report an agent as model-less.
  const { agent } = client(() => jsonResponse({}, 503));
  assert.equal(await agent.readBaseModel(), null);
});

test('an agent link with no endpoint cannot be asked at all', async () => {
  const agent = new AgentClient({ url: 'http://example.com:8720', channel: null });
  assert.equal(await agent.readBaseModel(), null);
});

// ------------------------------------------------------------------- writing


test('an empty key keeps the one on file rather than clearing it', async () => {
  const { agent, calls } = client(() => jsonResponse(CONFIGURED));
  await agent.saveBaseModel({
    provider: 'openrouter',
    model: 'openai/gpt-4o-mini',
    baseUrl: 'https://openrouter.ai/api/v1',
  });
  const body = calls.at(-1).body;
  assert.equal('api_key' in body, false, 'an untouched field must not be sent as a clear');
  assert.equal(body.model, 'openai/gpt-4o-mini');
});

test('a typed key is sent, and a deliberate clear says so', async () => {
  const { agent, calls } = client(() => jsonResponse(CONFIGURED));
  await agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'u', apiKey: '  sk-typed  ' });
  assert.equal(calls.at(-1).body.api_key, 'sk-typed');
  await agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'u', clearKey: true });
  assert.equal(calls.at(-1).body.clear_key, true);
  assert.equal('api_key' in calls.at(-1).body, false);
});

test('a save can name the variable to read the key from', async () => {
  // The button on the settings screen. The agent records the name and reads the
  // value out of the environment on every request, so a key rotated in a shell
  // is picked up without anybody reopening this form.
  const { agent, calls } = client(() => jsonResponse({ ...CONFIGURED, key_present: false, key_env: 'OPENAI_API_KEY' }));
  await agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'u', useEnv: true });
  const body = calls.at(-1).body;
  assert.equal(body.use_env, true);
  assert.equal('api_key' in body, false, 'the variable is a name, not a key');
});

test('a key typed wins over the variable it was typed over', async () => {
  // Sending both would leave the agent preferring the variable — it reads the
  // environment first — and quietly ignoring what was just pasted.
  const { agent, calls } = client(() => jsonResponse(CONFIGURED));
  await agent.saveBaseModel({
    provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'u', apiKey: 'sk-typed', useEnv: true,
  });
  assert.equal(calls.at(-1).body.api_key, 'sk-typed');
  assert.equal('use_env' in calls.at(-1).body, false);
});

test('going back to the catalogue is its own verb', async () => {
  const { agent, calls } = client(() => jsonResponse({ ...emptyAgentBaseModel() }));
  await agent.clearBaseModel();
  assert.equal(calls.at(-1).url, `${URL_BASE}/api/model`);
  assert.equal(calls.at(-1).body.enabled, false);
});

test('a refusal arrives as the code the agent gave, not as a bare failure', async () => {
  for (const code of ['unknown_provider', 'invalid_model', 'invalid_endpoint', 'key_required']) {
    const { agent } = client(() => jsonResponse({ error: code }, 400));
    await assert.rejects(
      () => agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'https://api.openai.com/v1' }),
      (error) => error instanceof AgentRequestError && error.detail === code,
      code,
    );
  }
});

test('every refusal the agent can give has a sentence of its own', () => {
  const codes = [
    'unknown_provider', 'invalid_model', 'invalid_endpoint', 'key_required',
    'env_key_missing', 'home_unwritable', 'cross_origin_refused',
  ];
  const keys = codes.map((code) => BASE_MODEL_REFUSAL_KEYS[code]);
  assert.equal(new Set(keys).size, keys.length, 'two codes sharing a sentence would be one problem again');
  for (const code of codes) assert.ok(isBaseModelRefusal(code));
  assert.equal(isBaseModelRefusal('something_else'), false);
});

test('a home the agent cannot write is its own refusal, not an outage', () => {
  // Thrown by the interface service, this arrives as a 503 and reads as "the
  // agent is not running" — advice to start something that is already running,
  // for a problem that is a directory somebody can go and look at.
  const { agent } = client(() => jsonResponse({ error: 'home_unwritable' }, 400));
  return assert.rejects(
    () => agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'https://api.openai.com/v1', apiKey: 'sk-x' }),
    (error) => error instanceof AgentRequestError
      && error.detail === 'home_unwritable'
      && baseModelFailureKey(error) === 'baseModelHomeUnwritable',
  );
});

test('a save that went unanswered is not reported as a read that could not happen', () => {
  // The two are different problems with opposite fixes — start the service, or
  // find out why it is silent — and one sentence for both sends somebody to the
  // wrong one. It bit: a save that had been attempted and failed was reported as
  // "could not reach the agent to read its base model".
  assert.equal(baseModelFailureKey(new AgentRequestError('agent_unreachable', 'http_503')), 'agentBaseModelWriteUnanswered');
  assert.equal(baseModelFailureKey(new AgentRequestError('agent_offline', 'not_configured')), 'agentBaseModelWriteUnanswered');
  // A refusal is still the refusal, whatever else is true.
  assert.equal(baseModelFailureKey(new AgentRequestError('agent_refused', 'invalid_model')), 'baseModelInvalidModel');
  // And the read keeps its own sentence, because nothing was asked of anybody.
  assert.equal(translate('en', 'agentBaseModelUnreachable').includes('read'), true);
  assert.equal(
    translate('en', 'agentBaseModelSavingFailed'),
    'Nothing was changed.',
    'the outcome line adds nothing the reason has not already said',
  );
});

test('an address with no base-model route is its own sentence', () => {
  // It answered, and what it answered was that it is not the agent's interface.
  // "The service is not answering" would send somebody to start something that is
  // already running, which is the advice this whole mapping exists to avoid.
  for (const code of ['http_404', 'http_405', 'http_501']) {
    assert.equal(baseModelFailureKey(new AgentRequestError('agent_refused', code)), 'agentBaseModelNoRoute', code);
  }
  assert.equal(
    translate('en', 'agentBaseModelNoRoute') !== translate('en', 'agentBaseModelWriteUnanswered'),
    true,
    'a missing route and an unanswered save are different problems',
  );
});

test('a variable that is not set is its own refusal, not a missing key', () => {
  // "That provider needs an API key" and "the variable you asked for is not
  // exported here" have opposite fixes — paste one, or export one — and telling
  // somebody the first when the truth is the second sends them to the wrong field.
  const { agent } = client(() => jsonResponse({ error: 'env_key_missing' }, 400));
  return assert.rejects(
    () => agent.saveBaseModel({ provider: 'openai', model: 'gpt-4o-mini', baseUrl: 'https://api.openai.com/v1', useEnv: true }),
    (error) => error instanceof AgentRequestError
      && error.detail === 'env_key_missing'
      && baseModelFailureKey(error) === 'baseModelEnvKeyMissing',
  );
});

// ---------------------------------------------------------------- the reader


test('the reader is total: a hostile or absent body reads as no base model', () => {
  for (const body of [null, undefined, 'text', 42, [], { configured: 'yes', model: 7 }]) {
    const found = readAgentBaseModel(body);
    assert.equal(typeof found.configured, 'boolean');
    assert.equal(typeof found.model, 'string');
  }
});

test('an absent model section does not break the view around it', () => {
  const view = readAgentView({ presence: { state: 'IDLE' } });
  assert.equal(view.model.configured, false);
  assert.equal(view.catalogue, 0);
  assert.equal(view.presence.state, 'IDLE');
});

test('the view reports what the agent thinks with', () => {
  const view = readAgentView({ model: CONFIGURED, catalogue: 10 });
  assert.equal(view.model.model, 'stealth/space-bunny-alpha');
  assert.equal(view.catalogue, 10);
});

test('an absurd catalogue count is bounded rather than believed', () => {
  assert.equal(readAgentView({ catalogue: -5 }).catalogue, 0);
  assert.equal(readAgentView({ catalogue: 1e12 }).catalogue, 10_000);
  assert.equal(readAgentView({ catalogue: 'ten' }).catalogue, 0);
});

// ------------------------------------------------------------- the old build

test("a service without the route is not read as a base model", async () => {
  // Three shapes, one answer. Believing any of them is a false all-clear on
  // exactly the question somebody opened the screen to ask.
  const stale = [
    () => jsonResponse({ ok: true, uptime: 5, db: 'up' }, 200),   // 200 + a body that is not one
    () => jsonResponse({}, 404),                                   // no route
    () => jsonResponse({}, 503),                                   // not answering at all
  ];
  for (const handler of stale) {
    const { agent } = client(handler);
    assert.equal(await agent.readBaseModel(), null, 'reported a base model that was never sent');
  }
});

test('the snapshot reader still tolerates a missing section', () => {
  // The opposite case, and it must not change: a snapshot from a build without
  // the section is a normal snapshot, so the view reports none rather than
  // refusing to be drawn.
  const view = readAgentView({ presence: { state: 'IDLE' } });
  assert.equal(view.model.configured, false);
  assert.equal(view.loaded, true);
});

// -------------------------------------------------------------- discovering

test('the catalogue is asked of the agent, not fetched by the page', async () => {
  // The whole reason a page can show a real catalogue for a keyed provider: the
  // request goes to the party that holds the key, and no key is sent with it
  // unless the person just typed one.
  const { agent, calls } = client(() => jsonResponse({ models: ['a/1', 'b/2'], source: 'remote' }));
  const result = await agent.discoverBaseModels({ provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1' });
  assert.deepEqual(result.models, ['a/1', 'b/2']);
  assert.equal(result.source, 'remote');
  const sent = calls.at(-1);
  assert.equal(sent.url, `${URL_BASE}/api/models`);
  assert.equal(sent.method, 'POST');
  assert.equal('api_key' in sent.body, false);
  assert.equal(sent.body.provider, 'openrouter');
});

test('a key just typed is sent, and an untouched one is not', async () => {
  const { agent, calls } = client(() => jsonResponse({ models: [], source: 'offline' }));
  await agent.discoverBaseModels({ provider: 'groq', baseUrl: 'https://api.groq.com/openai/v1', apiKey: '  sk-typed  ' });
  assert.equal(calls.at(-1).body.api_key, 'sk-typed');
  await agent.discoverBaseModels({ provider: 'groq', baseUrl: 'https://api.groq.com/openai/v1', apiKey: '   ' });
  assert.equal('api_key' in calls.at(-1).body, false);
});

test('a catalogue can be asked for with the variable instead of a key', async () => {
  // The person who keeps their key in their shell still gets a real catalogue,
  // and the page still never holds it: the request carries a flag, not a value.
  const { agent, calls } = client(() => jsonResponse({ models: ['a/1'], source: 'remote', key_source: 'environment' }));
  const result = await agent.discoverBaseModels({ provider: 'openai', useEnv: true });
  assert.equal(calls.at(-1).body.use_env, true);
  assert.equal('api_key' in calls.at(-1).body, false);
  // Which credential was spent travels back, so the form can say so.
  assert.equal(result.keySource, 'environment');
});

test('the source of the key is only believed when it is one', async () => {
  const { agent } = client(() => jsonResponse({ models: ['a/1'], source: 'remote' }));
  assert.equal((await agent.discoverBaseModels({ provider: 'openai' })).keySource, undefined);
});

test('every way of failing comes back as a list and a reason, never a throw', async () => {
  const cases = [
    [() => jsonResponse({ models: ['a/1'], source: 'offline', error: 'http_401' }), 'http_401'],
    [() => jsonResponse({}, 503), 'http_503'],
    // A fetch that throws is *our* request failing, so the code says the service
    // is not answering. Reporting it as an unreachable provider would send
    // somebody to look at their vendor instead of at the thing that is down.
    [() => { throw new Error('network down'); }, 'agent_unreachable'],
  ];
  for (const [handler, reason] of cases) {
    const { agent } = client(handler);
    const result = await agent.discoverBaseModels({ provider: 'openai' });
    assert.equal(result.source, 'offline', reason);
    assert.equal(result.error, reason);
    assert.ok(Array.isArray(result.models), reason);
  }
});

test('a reason about the provider is never confused with one about the service', () => {
  // The two are read by different people: `network_unavailable` is the vendor
  // being down, and it comes from whichever party did the asking.
  assert.equal(
    discoveryProblem({ models: ['a/1'], source: 'offline', error: 'network_unavailable' }, 'OpenAI').key,
    'providerUnavailable',
  );
  assert.equal(
    discoveryProblem({ models: ['a/1'], source: 'offline', error: 'agent_unreachable' }, 'OpenAI').key,
    'discoverAgentOffline',
  );
});

test('a link with no endpoint cannot ask anybody', async () => {
  const agent = new AgentClient({ url: 'http://example.com:8720', channel: null });
  const result = await agent.discoverBaseModels({ provider: 'openai' });
  assert.deepEqual(result.models, []);
  assert.equal(result.source, 'offline');
});

test("only the vendor's own answer counts as remote", async () => {
  // A body claiming `remote` with a guess in it must not be believed, or the
  // form would present a fallback as though it were a catalogue.
  const { agent } = client(() => jsonResponse({ models: ['a/1'], source: 'remote?' }));
  assert.equal((await agent.discoverBaseModels({ provider: 'openai' })).source, 'offline');
});

test('a body that is not a list of strings is not treated as one', async () => {
  const { agent } = client(() => jsonResponse({ models: ['a/1', 7, null, { id: 'b' }], source: 'remote' }));
  const result = await agent.discoverBaseModels({ provider: 'openai' });
  assert.deepEqual(result.models, ['a/1']);
});

// --------------------------------------------------------- saying what failed

test('a fallback says which failure it was, in a sentence of its own', () => {
  // The reason exists in the result precisely so a form can use it. Each of these
  // has a different fix, and one sentence for all of them is the reason
  // "Discover models does nothing" was so hard to act on.
  const reasons = {
    missing_credentials: 'discoverNoCredentials',
    invalid_endpoint: 'discoverBadEndpoint',
    unknown_provider: 'discoverUnknownProvider',
    empty_catalog: 'discoverEmptyCatalog',
    redirect_refused: 'discoverRedirect',
    timeout: 'discoverTimeout',
    network_unavailable: 'providerUnavailable',
    bridge_unavailable: 'discoverUnreachable',
    agent_unreachable: 'discoverAgentOffline',
  };
  const keys = Object.values(reasons);
  assert.equal(new Set(keys).size, keys.length, 'two different problems must not share a sentence');
  for (const [reason, key] of Object.entries(reasons)) {
    const problem = discoveryProblem({ models: ['a/1'], source: 'offline', error: reason }, 'OpenRouter');
    assert.deepEqual(problem, { key, values: { provider: 'OpenRouter' } }, reason);
  }
  assert.equal(discoveryProblem({ models: [], source: 'offline', error: 'agent_offline' }, 'OpenAI').key, 'discoverAgentOffline');
});

test('a status is carried into the sentence that has a hole in it', () => {
  assert.deepEqual(
    discoveryProblem({ models: [], source: 'offline', error: 'http_401' }, 'Groq'),
    { key: 'providerHttp', values: { status: '401' } },
  );
  // Not a status, whatever else it is: a code with a hole in it must not be shown
  // as though it were a number somebody can look up.
  assert.equal(discoveryProblem({ models: [], source: 'offline', error: 'http_4xx' }, 'Groq').key, 'discoverNoModels');
});

test('a real catalogue is never complained about', () => {
  assert.equal(discoveryProblem({ models: ['a/1'], source: 'remote' }, 'OpenAI'), null);
  // A fallback list with a reason nobody has a sentence for is still a list, and
  // the form says so on the label rather than inventing a complaint.
  assert.equal(discoveryProblem({ models: ['a/1'], source: 'offline', error: 'something_new' }, 'OpenAI'), null);
  // An empty list is worth a sentence whatever the reason was.
  assert.equal(discoveryProblem({ models: [], source: 'offline' }, 'OpenAI').key, 'discoverNoModels');
});

// ------------------------------------------------------- whether a base model
// is required at all

test('a base model is optional until there is one', () => {
  // The regression this whole function exists for: an install that was never
  // asked about a base model, whose agent is perfectly able to think on its own
  // catalogue, must still be able to save its language.
  const fresh = base();
  const nothing = emptyAgentBaseModel();
  assert.equal(agentDraftProblem(fresh, nothing, false), null);
  assert.equal(agentDraftProblem(base({ language: 'ja' }), nothing, false), null);
});

test('an agent that cannot be asked holds nothing up', () => {
  // There is nothing to validate a base model against, and blocking a save
  // because a service is down would make the whole page useless for everything
  // else on it — including the address field that might be the reason.
  const nothing = null;
  assert.equal(agentDraftProblem(base(), nothing, false), null);
  assert.equal(agentDraftProblem(base({ language: 'ja' }), nothing, false), null);
  // Even a half-finished base model: there is no agent to be half-finished for.
  assert.equal(agentDraftProblem(base(), nothing, true), null);
});

test('a bad address is still refused, even when the agent is down', () => {
  // Address first, deliberately. It is the field the reader is looking at, it is
  // fixable from this page, and "your address is wrong" is a better thing to be
  // told than "the agent is unreachable" when both are true.
  const held = sanitizeDraftSettings({ ...createSettings(), agentUrl: 'http://evil.example' });
  assert.equal(agentDraftProblem(held, null, false), 'invalidEndpoint');
});

test('once a base model is in play it is held to the rule', () => {
  const nothing = emptyAgentBaseModel();
  assert.equal(agentDraftProblem(base(), nothing, true), 'baseModelKeyRequired');
  assert.equal(agentDraftProblem(base({ apiKey: 'sk-x' }), nothing, true), null);
});

test('a base model the agent already has is held to the rule even untouched', () => {
  const configured = { configured: true, keyPresent: true, model: 'a/b', provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1', keyHint: '', protocol: 'openai_compat' };
  const draft = base({ provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1', model: 'a/b' });
  assert.equal(agentDraftProblem(draft, configured, false), null);
  // A stored key the form never held is not a missing one.
  assert.equal(agentDraftProblem(base({ provider: 'openrouter', baseUrl: 'https://openrouter.ai/v1', model: 'a/b' }), configured, false), null);
  // But a configured base model with no key is the state that looks fine and is
  // not, and it must not be saveable.
  assert.equal(agentDraftProblem(draft, { ...configured, keyPresent: false }, false), 'baseModelKeyRequired');
});

test("the agent's own address is still checked first", () => {
  // A draft the form is still holding, not one `createSettings` has quietly
  // repaired — normalising first is how this test passed for the wrong reason
  // once already.
  const held = (overrides) => sanitizeDraftSettings({ ...createSettings(), ...overrides });
  const nothing = emptyAgentBaseModel();
  assert.equal(agentDraftProblem(held({ agentUrl: 'http://evil.example' }), nothing, false), 'invalidEndpoint');
  assert.equal(agentDraftProblem(held({ agentUrl: 'https://agent.example' }), nothing, false), 'invalidEndpoint');
  assert.equal(agentDraftProblem(held({ agentUrl: 'http://127.0.0.1:8720/api' }), nothing, false), 'invalidEndpoint');
  assert.equal(agentDraftProblem(held({ agentPerson: '' }), nothing, false), 'agentPersonRequired');
  assert.equal(agentDraftProblem(held({}), nothing, false), null);
});

// -------------------------------------------------------------- the rule


/**
 * A draft the way the settings screen builds one.
 *
 * `sanitizeDraftSettings`, and the override applied *after* `createSettings` —
 * the form's draft goes through the first, which keeps a half-typed address and a
 * model name as typed, while the second normalises a bad one away. Testing the
 * rule against a normalised draft would test nothing: the rule only ever sees
 * what the form is still holding.
 */
function base(overrides = {}) {
  const start = createSettings({
    provider: 'openrouter',
    baseUrl: 'https://openrouter.ai/api/v1',
    model: 'a/b',
  });
  return sanitizeDraftSettings({ ...start, ...overrides });
}

test('a complete base model has nothing wrong with it', () => {
  assert.equal(baseModelProblem(base({ apiKey: 'sk-x' })), null);
  assert.equal(baseModelProblem(base(), true), null, 'a key on file means the empty field is fine');
});

test('a base model with no model name is refused', () => {
  assert.equal(baseModelProblem(base({ model: '', apiKey: 'sk-x' })), 'baseModelInvalidModel');
  assert.equal(baseModelProblem(base({ model: 'has space', apiKey: 'sk-x' })), 'baseModelInvalidModel');
});

test('an address the agent must not be given is refused here too', () => {
  // The agent refuses it again; checking here is what turns a round trip into an
  // underline, and the two rules have to say the same thing.
  for (const baseUrl of [
    'http://api.openai.com/v1',
    'https://169.254.169.254/latest',
    'https://192.168.1.10/v1',
    'https://user:pw@api.openai.com/v1',
  ]) {
    assert.equal(baseModelProblem(base({ baseUrl, apiKey: 'sk-x' })), 'baseModelInvalidEndpoint', baseUrl);
  }
});

test('a keyed provider needs a key, unless one is already on file', () => {
  assert.equal(baseModelProblem(base({ apiKey: '' })), 'baseModelKeyRequired');
  assert.equal(baseModelProblem(base({ apiKey: '' }), true), null);
});

test('a keyless provider needs no key at all', () => {
  const local = base({ provider: 'ollama', baseUrl: 'http://127.0.0.1:11434/v1', model: 'llama3.2', apiKey: '' });
  assert.equal(baseModelProblem(local), null);
});

test('an empty key field is fine when the key is in the environment', () => {
  // The same rule as a key on file, for the same reason: the field is empty on
  // purpose, and a form that cannot be saved is a form that has told the person
  // to type a secret they have already exported. A surface folds the environment
  // into the state it reports — "a credential exists that this field does not
  // hold" is the one fact the rule actually asks about.
  const draft = base({ apiKey: '' });
  assert.equal(setupProblem(4, draft), 'keyRequired');
  assert.equal(setupProblem(4, draft, true), null);
  const withEnv = (present) => ({ configured: true, keyPresent: present || true });
  assert.equal(agentDraftProblem(draft, withEnv(false), true), null);
  // And with neither, it is still the same missing key it always was.
  assert.equal(agentDraftProblem(draft, { configured: true, keyPresent: false }, true), 'baseModelKeyRequired');
});

// ------------------------------------------- when a page may ask on its own

test('a key that has been typed is a key that may be spent', () => {
  // Leaving the field is the end of the edit, because a secret has no half of
  // itself worth anything. A page that waits for a button after that makes the
  // person who just pasted a key walk down to the models list to find out
  // whether it was any good.
  assert.equal(shouldProbeCatalog(base({ apiKey: 'sk-typed' }), { useEnv: false, keyOnFile: false }), true);
});

test('the other two answers about a credential are keys too', () => {
  // The environment's key spent on purpose, and a key already saved on whichever
  // machine is answering. Neither is in the field, and both buy a catalogue.
  const empty = base({ apiKey: '' });
  assert.equal(shouldProbeCatalog(empty, { useEnv: true, keyOnFile: false }), true);
  assert.equal(shouldProbeCatalog(empty, { useEnv: false, keyOnFile: true }), true);
});

test('an install with no key anywhere is not asked on blur', () => {
  // There is no catalogue to be had here, and the only answer possible is a
  // failure about credentials that nobody caused, put on a form nobody has typed
  // anything into. The button is still there for that, on purpose.
  assert.equal(shouldProbeCatalog(base({ apiKey: '' }), { useEnv: false, keyOnFile: false }), false);
  // Whitespace is not a key: a field of spaces is what a paste misses by, and
  // what the vendor would refuse.
  assert.equal(shouldProbeCatalog(base({ apiKey: '   ' }), { useEnv: false, keyOnFile: false }), false);
});

test('a provider that asks for no key is asked whatever the field says', () => {
  // The endpoint is the whole request. Pressing into an empty field is not a
  // mistake, and somebody who has just pointed the form at a local server should
  // not have to go looking for the button to hear whether it answered.
  const local = base({ provider: 'ollama', baseUrl: 'http://127.0.0.1:11434/v1', model: 'llama3.2', apiKey: '' });
  assert.equal(shouldProbeCatalog(local, { useEnv: false, keyOnFile: false }), true);
});

test('the same credential twice is one question and not two', () => {
  // What lets a page fetch without being told to is a comparison rather than a
  // timer. Tabbing past the field, or opening the page and pressing into it out
  // of curiosity, would otherwise spend a request per visit — and could put a
  // fresh failure where a good list already was.
  assert.equal(
    probeSignature(base({ apiKey: 'sk-x' }), false),
    probeSignature(base({ apiKey: 'sk-x' }), false),
  );
  assert.notEqual(probeSignature(base({ apiKey: 'sk-x' }), false), probeSignature(base({ apiKey: 'sk-x' }), true));
});

test('anything that changes the answer changes the question', () => {
  // The provider, the endpoint, the key itself, and which of the two answers
  // about the key is being spent. A key corrected and left again is a different
  // credential, so a list from the first one is about a key that is gone.
  const mine = probeSignature(base({ apiKey: 'sk-x' }), false);
  const theirs = (overrides, useEnv = false) => probeSignature(base({ apiKey: 'sk-x', ...overrides }), useEnv);
  assert.notEqual(theirs({ provider: 'openai', baseUrl: 'https://api.openai.com/v1' }), mine);
  assert.notEqual(theirs({ baseUrl: 'https://proxy.example/v1' }), mine);
  assert.notEqual(theirs({ apiKey: 'sk-y' }), mine);
  assert.notEqual(theirs({}, true), mine);
  // And nothing that does not. The model is what is being chosen *out* of the
  // answer, so a signature that moved with it would re-ask on every click.
  assert.equal(theirs({ model: 'b/c', language: 'ja' }), mine);
});
