import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { DuplexClient } from '../dist/index.js';

/**
 * What a provider credential can and cannot reach.
 *
 * The browser is never handed one: the file is owner-only, the bridge answers
 * with a redacted snapshot, and an environment credential belongs to the
 * session. Where state is kept is `persistence.test.mjs`'s subject.
 */

test('provider changes do not retain the previous credential tuple', () => {
  const client = new DuplexClient({
    settings: {
      provider: 'openai',
      apiKey: 'openai-secret',
      baseUrl: 'https://api.openai.com/v1',
      model: 'gpt-6.1-sol',
    },
  });

  client.updateSettings({ provider: 'anthropic' });
  const settings = client.getSnapshot().settings;
  assert.equal(settings.provider, 'anthropic');
  assert.equal(settings.apiKey, '');
  assert.equal(settings.baseUrl, 'https://api.anthropic.com/v1');
  // Only OpenAI has a default model: switching clears the model rather than
  // substituting one nobody chose.
  assert.equal(settings.model, '');
});
