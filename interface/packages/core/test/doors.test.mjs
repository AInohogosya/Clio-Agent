import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  AgentClient,
  AgentRequestError,
  channelFailureKey,
  channelLabel,
  isChannelRefusal,
  readAgentChannelSetup,
  translate,
} from '../dist/index.js';

/**
 * The doors, as this kit reads them.
 *
 * A bot token used to exist only as the *name* of an environment variable, which
 * made this layer part of the reason there was no way to hand this agent one from
 * a screen or a terminal: nothing could be written, so nothing could be read
 * back. With a door writable, this layer is responsible for the same three things
 * the name and the base model are:
 *
 *   * telling "the service could not be asked" apart from "this deployment has no
 *     doors", because one is a form with nowhere to write and the other is a
 *     machine to write to;
 *   * never holding a credential — a page that could read a bot token would be a
 *     page that could post it somewhere; and
 *   * reading the *reason* out of a refusal, which lives in `detail` rather than
 *     in the message and is therefore invisible to anything reading the message.
 *
 * Each of the seven refusals is a different thing somebody typed with a different
 * fix, which is why they get seven sentences and not one.
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

/** What the bridge reports for a machine with nothing configured. */
function setupBody(overrides = {}) {
  return {
    channels: [{ id: 'web', enabled: true, admits: true, contacts: [] }],
    setup: {
      person: 'owner',
      file: '/home/ethos/.ethos/channels.yaml',
      doors: [
        {
          id: 'telegram',
          enabled: false,
          credentials: [{ key: 'token', label: 'Bot token', env: 'TELEGRAM_BOT_TOKEN', present: false, hint: '', env_present: false }],
          allowlist: 'allowed_chat_ids',
          allowed: [],
          admits: false,
          prefix: 'tg:',
          address: '',
          needs_webhook: false,
          webhook_on: false,
        },
        {
          id: 'whatsapp',
          enabled: false,
          credentials: [
            { key: 'phone_number_id', label: 'Phone number id', env: 'WHATSAPP_PHONE_NUMBER_ID', present: false, hint: '', env_present: false },
            { key: 'access_token', label: 'Access token', env: 'WHATSAPP_ACCESS_TOKEN', present: false, hint: '', env_present: false },
            { key: 'app_secret', label: 'App secret', env: 'WHATSAPP_APP_SECRET', present: false, hint: '', env_present: false },
            { key: 'verify_token', label: 'Verify token', env: 'WHATSAPP_VERIFY_TOKEN', present: false, hint: '', env_present: false },
          ],
          allowlist: 'allowed_phone_numbers',
          allowed: [],
          admits: false,
          prefix: 'wa:',
          address: '',
          needs_webhook: true,
          webhook_on: false,
          path: '/hooks/whatsapp',
        },
        {
          id: 'webhook',
          enabled: false,
          credentials: [],
          allowlist: null,
          allowed: [],
          admits: true,
          prefix: '',
          address: '',
          needs_webhook: false,
          webhook_on: true,
          host: '127.0.0.1',
          port: 8730,
        },
        ...overrides.doors ?? [],
      ],
    },
  };
}

const doorOf = (setup, id) => setup.doors.find((door) => door.id === id);

// ------------------------------------------------------------------- reading

test('every door reads back, with the fields it needs', () => {
  const setup = readAgentChannelSetup(setupBody());
  assert.equal(setup.person, 'owner');
  assert.equal(setup.file, '/home/ethos/.ethos/channels.yaml');
  assert.deepEqual(setup.doors.map((door) => door.id), ['telegram', 'whatsapp', 'webhook']);
  assert.deepEqual(doorOf(setup, 'telegram').credentials.map((entry) => entry.key), ['token']);
  assert.equal(doorOf(setup, 'telegram').prefix, 'tg:');
  assert.equal(doorOf(setup, 'whatsapp').needsWebhook, true);
  assert.equal(doorOf(setup, 'whatsapp').path, '/hooks/whatsapp');
  assert.equal(doorOf(setup, 'webhook').port, 8730);
});

test('a door that is on, complete and admitting nobody still says it admits nobody', () => {
  // The state that looks most like working: the bot answers `/id` and nothing
  // else. A form that reported only `enabled` would show this as a door a person
  // can talk on.
  const setup = readAgentChannelSetup(setupBody({
    doors: [{
      id: 'discord',
      enabled: true,
      credentials: [{ key: 'token', label: 'Bot token', env: 'DISCORD_BOT_TOKEN', present: true, hint: '••••cdef', env_present: false }],
      allowlist: 'allowed_ids',
      allowed: [],
      admits: false,
      prefix: 'dc:',
      address: '',
      needs_webhook: false,
      webhook_on: false,
    }],
  }));
  const discord = doorOf(setup, 'discord');
  assert.equal(discord.enabled, true);
  assert.equal(discord.credentials[0].present, true);
  assert.equal(discord.credentials[0].hint, '••••cdef');
  assert.equal(discord.admits, false);
});

test('a credential in the environment and one on file are different answers', () => {
  // The environment is what the agent will read, so a form that could not say so
  // would show a pasted token as the live one on a machine that is not using it.
  const setup = readAgentChannelSetup(setupBody({
    doors: [{
      id: 'slack',
      enabled: true,
      credentials: [{ key: 'bot_token', label: 'Bot token', env: 'SLACK_BOT_TOKEN', present: false, hint: '', env_present: true }],
      allowlist: 'allowed_ids',
      allowed: ['C0E2E'],
      admits: true,
      prefix: 'slack:',
      address: 'slack:C0E2E',
      needs_webhook: true,
      webhook_on: true,
    }],
  }));
  const slack = doorOf(setup, 'slack');
  assert.equal(slack.credentials[0].envPresent, true, 'and that is the one the agent reads');
  assert.equal(slack.credentials[0].present, false);
  assert.deepEqual(slack.allowed, ['C0E2E']);
  assert.equal(slack.address, 'slack:C0E2E');
});

test('a door told to answer anyone says so, next to the list it overrides', () => {
  // The other way to be a usable door, and the one a public agent needs: there is
  // no list of everybody who might write. Both facts are true at once — the list
  // is empty *and* the door answers — so a form shown only one of them is wrong
  // either way: read alone, the empty list says the bot is mute.
  const setup = readAgentChannelSetup(setupBody({
    doors: [{
      // A door of its own rather than a second `telegram`: the overrides append to
      // the list, and `doorOf` takes the first match, so this would be reading the
      // door that has nothing set on it.
      id: 'discord',
      enabled: true,
      credentials: [{ key: 'token', label: 'Bot token', env: 'DISCORD_BOT_TOKEN', present: true, hint: '••••cdef', env_present: false }],
      allowlist: 'allowed_ids',
      allowed: [],
      admits: true,
      accept_from_anyone: true,
      prefix: 'dc:',
      address: '',
      needs_webhook: false,
      webhook_on: false,
    }],
  }));
  const discord = doorOf(setup, 'discord');
  assert.equal(discord.acceptFromAnyone, true);
  assert.equal(discord.admits, true);
  assert.deepEqual(discord.allowed, []);
});

test('a door that was never told to answer anyone does not', () => {
  // The half of the pair that matters most, and the default on every bridge that
  // predates the field: `accept_from_anyone` absent means off, so a fresh install
  // with a bot token in it does not answer the public.
  const setup = readAgentChannelSetup(setupBody());
  assert.equal(doorOf(setup, 'telegram').acceptFromAnyone, false);
  assert.equal(doorOf(setup, 'telegram').admits, false);
  // The shared receiver is not a door and admits nobody, so it is reported as
  // closed rather than as open to all.
  assert.equal(doorOf(setup, 'webhook').acceptFromAnyone, false);
});

test('the setting is written only when a caller mentions it', async () => {
  // The failure this prevents: a form saving somebody's token silently closing a
  // door they had opened to the public an hour ago, because one request named the
  // wrong key. `undefined` means leave it alone and `false` means close it, and
  // the two are different instructions.
  const { agent, calls } = client(() => jsonResponse({ setup: setupBody() }));

  await agent.saveDoor({ id: 'telegram', enabled: true, acceptFromAnyone: true });
  assert.equal(calls[0].body.accept_from_anyone, true);

  await agent.saveDoor({ id: 'telegram', credentials: { token: '1:2' } });
  assert.equal('accept_from_anyone' in calls[1].body, false, 'a save that did not mention it');

  await agent.saveDoor({ id: 'telegram', acceptFromAnyone: false });
  assert.equal(calls[2].body.accept_from_anyone, false, 'and a save that did can close it');
});

test('a service that cannot be asked is not a machine with no doors', () => {
  // A route that is missing, a 404, a body of somebody else's HTML and nothing at
  // all all read the same way: an empty setup, which a form can tell apart from
  // an agent it reached. Collapsing them would offer a field for a token over a
  // service that was never asked.
  for (const answer of [null, {}, { setup: null }, { setup: { doors: 'not a list' } }]) {
    const setup = readAgentChannelSetup(answer);
    assert.deepEqual(setup.doors, []);
    assert.equal(setup.person, 'owner');
  }
});

test('a door with no name is not a door', () => {
  const setup = readAgentChannelSetup({ setup: { person: 'owner', doors: [{ enabled: true }, { id: '  ' }] } });
  assert.deepEqual(setup.doors, []);
});

test('the receiver is named like every other door', () => {
  // It is the other half of WhatsApp and Slack, so a person setting either up has
  // to be able to name it rather than be told "webhook" in a raw id.
  assert.equal(channelLabel('webhook'), 'Webhook');
  assert.equal(channelLabel('telegram'), 'Telegram');
});

// ------------------------------------------------------------------- writing

test('a save sends only the field it was given', async () => {
  // The failure this prevents: a settings screen that clears a token somebody
  // pasted five minutes ago because they went to change the allowlist.
  const { agent, calls } = client(() => jsonResponse(setupBody()));
  const written = await agent.saveDoor({
    id: 'telegram',
    enabled: true,
    credentials: { token: '123:abc' },
    allowed: ['819012345678'],
    address: 'tg:819012345678',
  });
  assert.deepEqual(calls[0].body, {
    id: 'telegram',
    person: 'owner',
    enabled: true,
    credentials: { token: '123:abc' },
    allowed: ['819012345678'],
    address: 'tg:819012345678',
  });
  assert.equal(written.doors.length, 3, 'the answer is the setup that was written');
  agent.dispose();
});

test('a save that names no credential sends none, rather than an empty one', async () => {
  // An empty string means "keep the one on file" on the wire, and a form that
  // sent `''` for a field nobody filled in would be a form that clears it.
  const { agent, calls } = client(() => jsonResponse(setupBody()));
  await agent.saveDoor({ id: 'discord', enabled: true, allowed: [] });
  assert.equal('credentials' in calls[0].body, false);
  assert.deepEqual(calls[0].body.allowed, []);
  agent.dispose();
});

test('taking a credential back is a different instruction from keeping it', async () => {
  const { agent, calls } = client(() => jsonResponse(setupBody()));
  await agent.saveDoor({ id: 'telegram', credentials: { token: null } });
  assert.equal(calls[0].body.credentials.token, null);
  agent.dispose();
});

test('a refusal arrives as the code the agent gave, not as a bare failure', async () => {
  // Seven different things somebody typed, seven different fixes. Throwing the
  // code away would leave "the agent refused" over a chat id that was the whole
  // problem.
  const { agent } = client(() => jsonResponse({ error: 'invalid_allowed_id' }, 400));
  await assert.rejects(
    () => agent.saveDoor({ id: 'telegram', allowed: ['abc'] }),
    (error) => error instanceof AgentRequestError && error.detail === 'invalid_allowed_id',
  );
  agent.dispose();
});

test('a service with no such route is not reported as a refusal', async () => {
  // An older bridge answers any unmatched path with its own HTML and a 200, so
  // this is a question about the shape of the answer rather than the status.
  const { agent } = client(() => jsonResponse({ setup: null }));
  const written = await agent.saveDoor({ id: 'telegram', enabled: true });
  assert.deepEqual(written.doors, []);
  agent.dispose();
});

// ------------------------------------------------------------------- refusals

test('the refusals are the seven the agent names', () => {
  const named = [
    'unknown_channel', 'unknown_field', 'invalid_value', 'invalid_allowed_id',
    'invalid_address', 'home_unwritable', 'cross_origin_refused',
  ];
  assert.deepEqual(named.filter(isChannelRefusal), named);
  for (const notOne of ['unknown_provider', 'invalid_name', '', null, 42]) {
    assert.equal(isChannelRefusal(notOne), false, String(notOne));
  }
});

test('every refusal is a sentence in every language', () => {
  for (const language of ['en', 'ja', 'zh']) {
    for (const refusal of [
      'unknown_channel', 'unknown_field', 'invalid_value', 'invalid_allowed_id',
      'invalid_address', 'home_unwritable', 'cross_origin_refused',
    ]) {
      // Every door sentence gets the same values, because one of them names the
      // prefix a door posts to and a blank one would render as `{prefix}` on a
      // screen — which is the failure this loop exists to catch.
      const line = translate(language, channelFailureKey(new AgentRequestError('agent_refused', refusal)), {
        prefix: 'tg:',
        value: '819012345678',
        field: 'telegram.token',
      });
      assert.notEqual(line.trim(), '', `${language}: ${refusal} is blank`);
      assert.doesNotMatch(line, /[{}]/, `${language}: ${refusal} left a placeholder in`);
    }
  }
});

test('an address refusal says what an address on that door looks like', () => {
  const line = translate('en', channelFailureKey(new AgentRequestError('agent_refused', 'invalid_address')), {
    prefix: 'tg:',
  });
  assert.match(line, /tg:/);
});
