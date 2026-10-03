import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  createSettings,
  DuplexClient,
  isProviderId,
  LANGUAGES,
  PROVIDER_DEFAULTS,
  PROVIDER_DEFINITIONS,
  PROVIDER_IDS,
  PROVIDER_LABELS,
  providerDescriptionKey,
  requestProviderCompletion,
  translationTables,
} from '../dist/index.js';

const LOCAL_PROVIDERS = ['ollama', 'lmstudio'];
const HOSTED_PROVIDERS = PROVIDER_IDS.filter((id) => !LOCAL_PROVIDERS.includes(id));

test('every provider id is registered with defaults, labels, and translated descriptions', () => {
  for (const id of PROVIDER_IDS) {
    const definition = PROVIDER_DEFINITIONS[id];
    const defaults = PROVIDER_DEFAULTS[id];

    assert.ok(isProviderId(id));
    assert.equal(definition.id, id);
    assert.ok(definition.label.length > 0, `${id} needs a label`);
    assert.ok(definition.description.length > 0, `${id} needs a description`);
    assert.equal(definition.defaultBaseUrl, defaults.baseUrl);
    assert.equal(definition.defaultModel, defaults.model);
    assert.ok(definition.staticModels.includes(defaults.model), `${id} static catalog must include its default model`);
    assert.equal(PROVIDER_LABELS[id], definition.label);

    const descriptionKey = providerDescriptionKey(id);
    for (const language of LANGUAGES) {
      const table = translationTables[language];
      const copy = table[descriptionKey];
      assert.ok(
        Object.prototype.hasOwnProperty.call(table, descriptionKey) && copy !== descriptionKey && copy.length > 0,
        `${id} is missing ${descriptionKey} for ${language}`,
      );
    }

    const settings = createSettings({ provider: id });
    assert.equal(settings.provider, id);
    assert.equal(settings.baseUrl, defaults.baseUrl);
    assert.equal(settings.model, defaults.model);
  }
});

test('provider defaults satisfy the endpoint transport rules', () => {
  for (const id of PROVIDER_IDS) {
    const baseUrl = PROVIDER_DEFAULTS[id].baseUrl;
    const usesHttp = baseUrl.startsWith('http://');
    assert.ok(usesHttp || baseUrl.startsWith('https://'), `${id} needs an http(s) endpoint`);
    assert.equal(usesHttp, LOCAL_PROVIDERS.includes(id), `${id} defaults to the wrong transport scheme`);
  }
});

test('local providers accept loopback HTTP while keyed providers require HTTPS', () => {
  for (const id of LOCAL_PROVIDERS) {
    assert.equal(createSettings({ provider: id, baseUrl: 'http://127.0.0.1:9999/v1' }).baseUrl, 'http://127.0.0.1:9999/v1');
  }
  for (const id of HOSTED_PROVIDERS) {
    const settings = createSettings({ provider: id, baseUrl: 'http://127.0.0.1:9999/v1' });
    assert.notEqual(settings.baseUrl, 'http://127.0.0.1:9999/v1', `${id} must not accept loopback HTTP`);
    assert.equal(settings.baseUrl, PROVIDER_DEFAULTS[id].baseUrl);
  }
});

test('switching providers resets the credential tuple to the new provider defaults', () => {
  for (const id of PROVIDER_IDS.filter((candidate) => candidate !== 'openai')) {
    const client = new DuplexClient({
      settings: {
        provider: 'openai',
        apiKey: 'openai-secret',
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini',
      },
    });

    client.updateSettings({ provider: id });
    const settings = client.getSnapshot().settings;
    assert.equal(settings.provider, id);
    assert.equal(settings.apiKey, '');
    assert.equal(settings.baseUrl, PROVIDER_DEFAULTS[id].baseUrl);
    assert.equal(settings.model, PROVIDER_DEFAULTS[id].model);
  }
});

test('OpenAI-compatible providers post to chat/completions with bearer auth', async () => {
  for (const id of HOSTED_PROVIDERS) {
    const definition = PROVIDER_DEFINITIONS[id];
    if (id === 'anthropic' || id === 'gemini') continue;

    let url = '';
    let request = null;
    const fetcher = async (input, init) => {
      url = String(input);
      request = init;
      return globalThis.Response.json({ choices: [{ message: { content: `reply from ${id}` } }] });
    };

    const reply = await requestProviderCompletion(
      { ...createSettings({ provider: id }), apiKey: `${id}-secret` },
      'ping',
      fetcher,
    );

    assert.equal(url, `${PROVIDER_DEFAULTS[id].baseUrl}/chat/completions`);
    assert.equal(request?.method, 'POST');
    assert.equal(request?.headers?.Authorization, `Bearer ${id}-secret`);
    assert.equal(JSON.parse(String(request?.body)).model, definition.defaultModel);
    assert.equal(reply, `reply from ${id}`);
  }
});

test('keyless local providers complete without an API key', async () => {
  for (const id of ['ollama', 'lmstudio']) {
    const definition = PROVIDER_DEFINITIONS[id];
    assert.equal(definition.requiresApiKey, false);

    let request = null;
    const fetcher = async (_input, init) => {
      request = init;
      return globalThis.Response.json({ choices: [{ message: { content: `local ${id}` } }] });
    };

    const reply = await requestProviderCompletion(
      { ...createSettings({ provider: id }), apiKey: '' },
      'ping',
      fetcher,
    );

    assert.equal(request?.headers?.Authorization, undefined);
    assert.equal(reply, `local ${id}`);
  }
});
