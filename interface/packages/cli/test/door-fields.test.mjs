import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { readAgentChannelSetup } from '@project-phone/core';
import { CliError } from '../dist/args.js';
import { doorClear, doorFields, doorValue, doorWrite } from '../dist/door-fields.js';

/**
 * `phone channels set`, checked without a terminal.
 *
 * The terminal used to be the one surface with no way to open a door: a bot token
 * was available from a settings screen and a file edit, and from nowhere a person
 * at a terminal could reach. These tests are about the four things that could go
 * wrong with the fix:
 *
 *   * a save that mentions one field clearing the three beside it;
 *   * a credential being accepted on a command line, which `phone config` has
 *     always refused for a key and which this has to refuse for the same reason;
 *   * a name that is not a door, or a field that door does not have, being
 *     refused with the names that are; and
 *   * `get` answering about a credential with *where it is* and never with what
 *     it says.
 */

/** The doors a bridge reports for a machine with nothing configured. */
function setup(overrides = {}) {
  return readAgentChannelSetup({
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
          id: 'slack',
          enabled: false,
          credentials: [{ key: 'bot_token', label: 'Bot token', env: 'SLACK_BOT_TOKEN', present: false, hint: '', env_present: false }],
          allowlist: 'allowed_ids',
          allowed: [],
          admits: false,
          prefix: 'slack:',
          address: '',
          needs_webhook: true,
          webhook_on: false,
        },
        { id: 'webhook', enabled: false, credentials: [], allowlist: null, allowed: [], admits: true, prefix: '', address: '', needs_webhook: false, webhook_on: true },
        ...(overrides.doors ?? []),
      ],
    },
  });
}

// ------------------------------------------------------------------ the fields

test('a door has the fields every door has, and the credentials it needs', () => {
  // The receiver has no address and admits nobody: it posts to nothing and answers
  // to nobody, so a field for either would be a command that reported a save which
  // changed nothing.
  assert.deepEqual(doorFields(setup().doors[0]), ['enabled', 'acceptFromAnyone', 'allowed', 'address', 'token']);
  assert.deepEqual(doorFields(setup().doors[2]), ['enabled', 'allowed']);
});

test('the receiver cannot be opened to everybody', () => {
  // `acceptFromAnyone` is a decision about a door, and the receiver is not one —
  // naming it here has to be refused rather than reported as a save, because it
  // would otherwise look like the public Telegram door had just been turned on.
  assert.throws(() => doorWrite(setup(), 'webhook.acceptFromAnyone', 'true'), (error) => (
    error instanceof CliError && error.code === 'unknown_field'
  ));
});

test('a door is opened to everybody on purpose, or not at all', () => {
  // The whole point of the field being separate from an empty allowlist: an empty
  // list is what a fresh install looks like, and an install that has not been given
  // an allowlist yet must not answer the public.
  assert.deepEqual(doorWrite(setup(), 'telegram.acceptFromAnyone', 'true'), {
    id: 'telegram',
    person: 'owner',
    acceptFromAnyone: true,
  });
  for (const off of ['false', 'no', 'off', '0']) {
    assert.deepEqual(doorWrite(setup(), 'telegram.acceptFromAnyone', off), {
      id: 'telegram',
      person: 'owner',
      acceptFromAnyone: false,
    });
  }
  for (const bad of ['maybe', '']) {
    assert.throws(() => doorWrite(setup(), 'telegram.acceptFromAnyone', bad), CliError, bad);
  }
});

test('it reads back as the setting it is, next to the list it overrides', () => {
  // A door of its own rather than a second `telegram`: `readDoorTarget` takes the
  // first match, and the point of the test is what the reader does with *this*
  // door's fields.
  const open = setup({
    doors: [{
      id: 'discord',
      enabled: true,
      credentials: [{ key: 'token', label: 'Bot token', env: 'DISCORD_BOT_TOKEN', present: false, hint: '', env_present: false }],
      allowlist: 'allowed_ids',
      allowed: [],
      admits: true,
      accept_from_anyone: true,
      prefix: 'dc:',
      address: 'dc:9999',
      needs_webhook: false,
      webhook_on: false,
    }],
  });
  // Both facts, because both are true and only one of them explains the other: the
  // list is empty and the door answers everybody, and a reader shown only the list
  // would conclude the bot is mute.
  assert.equal(doorValue(open, 'discord.acceptFromAnyone'), 'true');
  assert.equal(doorValue(open, 'discord.allowed'), '');
});

test('a name with no door in it is not a field', () => {
  assert.throws(() => doorWrite(setup(), 'telegram', 'true'), (error) => (
    error instanceof CliError && error.code === 'unknown_field'
  ));
});

test('a door that does not exist is refused with the ones that do', () => {
  // "unknown channel" on its own leaves the only question a reader has — *which*
  // — with no answer.
  assert.throws(() => doorWrite(setup(), 'carrier-pigeon.enabled', 'true'), (error) => (
    error instanceof CliError
    && error.code === 'channel_unknown'
    // The placeholders the sentence has, so the refusal cannot render as
    // "No channel named {name} is open".
    && error.values.name === 'carrier-pigeon'
    && String(error.values.offered).includes('telegram')
  ));
});

test('a field that door does not have is refused rather than ignored', () => {
  // The webhook receiver has no credentials, so naming one on it is a mistake —
  // and a form that ignored it would report a save that changed nothing.
  assert.throws(() => doorWrite(setup(), 'webhook.token', 'x'), (error) => (
    error instanceof CliError && error.code === 'unknown_field'
  ));
});

// -------------------------------------------------------------------- writing

test('a set carries only the field it names', () => {
  // The failure this prevents: `phone channels set telegram.allowed …` clearing
  // the token somebody pasted an hour ago, because one command mentioned the
  // whole door.
  assert.deepEqual(doorWrite(setup(), 'telegram.allowed', '819012345678'), {
    id: 'telegram',
    person: 'owner',
    allowed: ['819012345678'],
  });
  assert.deepEqual(doorWrite(setup(), 'telegram.enabled', 'true'), {
    id: 'telegram',
    person: 'owner',
    enabled: true,
  });
});

test('an allowlist typed with commas or spaces is a list of ids', () => {
  // A list pasted out of a chat is separated by whichever of those whatever it was
  // copied from used, and one entry of "819012345678, 819012345679" is an id no
  // door recognises — a door that is open and admits nobody.
  assert.deepEqual(doorWrite(setup(), 'telegram.allowed', '819012345678, 819012345679\n819012345680').allowed, [
    '819012345678', '819012345679', '819012345680',
  ]);
});

test('an address without the door own prefix is refused before the send', () => {
  // `TelegramAdapter.send` refuses anything that is not `tg:<chat id>`, so an
  // address without it is a conversation the agent deliberates on and then
  // cannot answer — a question, a long pause, and silence.
  //
  // The refusal carries the prefix, and it is the *same* code the bridge refuses
  // with: one problem gets one sentence whichever surface caught it first, and
  // "invalid value" alone would leave the reader to work out what an address
  // looks like on the door they are holding.
  assert.throws(() => doorWrite(setup(), 'telegram.address', '819012345678'), (error) => (
    error instanceof CliError
    && error.code === 'door_invalid_address'
    && error.values.prefix === 'tg:'
  ));
  assert.deepEqual(doorWrite(setup(), 'telegram.address', 'tg:819012345678').address, 'tg:819012345678');
});

test('a door with no addresses takes no address field', () => {
  assert.throws(() => doorWrite(setup(), 'webhook.address', 'tg:1'), (error) => (
    error instanceof CliError && error.code === 'unknown_field'
  ));
});

test('a boolean that is not one is refused rather than read as false', () => {
  // "yes please" read as `false` would report a save that closed the door.
  for (const good of ['true', 'yes', 'on', '1']) {
    assert.equal(doorWrite(setup(), 'telegram.enabled', good).enabled, true, good);
  }
  for (const bad of ['maybe', '']) {
    assert.throws(() => doorWrite(setup(), 'telegram.enabled', bad), CliError);
  }
});

// -------------------------------------------------------------- a credential

test('a credential is refused as a value, with a sentence that says where it goes', () => {
  // The rule `phone config` has always applied to an API key, for the same
  // reason: a secret on a command line is in the shell history and the process
  // list. Removing one is allowed, because removal says nothing.
  assert.throws(() => doorWrite(setup(), 'telegram.token', '123:abc'), (error) => (
    error instanceof CliError && error.code === 'door_secret_argument'
  ));
  assert.deepEqual(doorClear(setup(), 'telegram.token'), {
    id: 'telegram',
    person: 'owner',
    credentials: { token: null },
  });
});

test('a credential reads back as where it is, and never as what it says', () => {
  assert.equal(doorValue(setup(), 'telegram.token'), '');
  const onFile = setup({
    doors: [{
      id: 'discord',
      enabled: true,
      credentials: [{ key: 'token', label: 'Bot token', env: 'DISCORD_BOT_TOKEN', present: true, hint: '••••cdef', env_present: false }],
      allowlist: 'allowed_ids',
      allowed: ['9999'],
      admits: true,
      prefix: 'dc:',
      address: 'dc:9999',
      needs_webhook: false,
      webhook_on: false,
    }],
  });
  assert.equal(doorValue(onFile, 'discord.token'), '••••cdef');
  const inEnv = setup({
    doors: [{
      id: 'discord',
      enabled: true,
      credentials: [{ key: 'token', label: 'Bot token', env: 'DISCORD_BOT_TOKEN', present: true, hint: '••••cdef', env_present: true }],
      allowlist: 'allowed_ids',
      allowed: ['9999'],
      admits: true,
      prefix: 'dc:',
      address: 'dc:9999',
      needs_webhook: false,
      webhook_on: false,
    }],
  });
  // The variable wins, so `get` names it: a form that reported the stored hint
  // here would be showing a token the agent is not going to read.
  assert.equal(doorValue(inEnv, 'discord.token'), 'environment:DISCORD_BOT_TOKEN');
});

test('an unset puts every field back the way a fresh install has it', () => {
  assert.deepEqual(doorClear(setup(), 'telegram.enabled'), { id: 'telegram', person: 'owner', enabled: false });
  // Narrow, not wide: `unset` on a public door has to close it, the same direction
  // every other field here goes. Unsetting something to *open* it would make
  // "put this back the way a fresh install has it" mean the opposite for one field.
  assert.deepEqual(doorClear(setup(), 'telegram.acceptFromAnyone'), {
    id: 'telegram',
    person: 'owner',
    acceptFromAnyone: false,
  });
  assert.deepEqual(doorClear(setup(), 'telegram.allowed'), { id: 'telegram', person: 'owner', allowed: [] });
  assert.deepEqual(doorClear(setup(), 'telegram.address'), { id: 'telegram', person: 'owner', address: null });
});
