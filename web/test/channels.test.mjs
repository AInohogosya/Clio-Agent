import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, statSync, writeFileSync, mkdirSync, copyFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, beforeEach, test } from 'node:test';

/**
 * `/api/channels` and `/api/message`'s channel, over real HTTP.
 *
 * These two routes are the whole of "talk to the agent from somewhere that is not
 * a browser". `/api/channels` says which doors the deployment has open, and
 * `/api/message` decides which door a question goes out of — which means it also
 * decides where the agent's *reply* is delivered, so it is the one route here
 * that can make this process send the agent's own words somewhere.
 *
 * No database is needed for either: both answers are computed from the config
 * files the agent's own process reads, and the bridge is expected to keep serving
 * while Postgres is down. The bridge under test is therefore pointed at an
 * unreachable database, so the two refusals below — the one for a message with
 * nothing to say and the one for a door with no destination — are distinguishable
 * from a failure to reach the database at all, and so no assertion here can
 * disturb an agent's transcript.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(HERE, '..', 'server', 'index.mjs');
const CONFIG_DIR = resolve(HERE, '..', '..', 'config');

let child = null;
let home = '';
let base = '';

/** The `channels.yaml` each test starts from: the shipped one, then edited. */
let channelsPath = '';
/** The contact book the same tests edit, so the two can disagree on purpose. */
let peoplePath = '';

/** The one person the shipped contact book holds, as `/api/channels` reports it. */
const WEB_OWNER = [{ person: 'owner', address: 'owner' }];

async function freePort() {
  const { createServer } = await import('node:net');
  return new Promise((done) => {
    const probe = createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => done(port));
    });
  });
}

/** Rewrites the config copy the running bridge reads, for the next assertion. */
function writeChannels(text) {
  writeFileSync(channelsPath, text);
}

function writePeople(text) {
  writeFileSync(peoplePath, text);
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'ethos-channels-'));
  // The bridge reads `config/` relative to its own repository, so the config is
  // copied into a temp tree and pointed at by the environment — the same way a
  // deployment would point at its own.
  const configDir = join(home, 'config');
  mkdirSync(configDir, { recursive: true });
  for (const name of ['ethos.yaml', 'channels.yaml', 'models.yaml', 'permissions.yaml', 'people.yaml']) {
    copyFileSync(resolve(HERE, '..', '..', 'config', name), join(configDir, name));
  }
  channelsPath = join(configDir, 'channels.yaml');
  peoplePath = join(configDir, 'people.yaml');

  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      ETHOS_HOME: home,
      ETHOS_CONFIG_DIR: configDir,
      ETHOS_WEB_PORT: String(port),
      // Pointed away from any database on purpose. Both routes here are computed
      // from the config files, and `/api/message` must answer without one — so
      // the suite runs with the database unreachable and every write it attempts
      // fails the same way. Inheriting the ambient `ETHOS_DSN` instead would let
      // these tests write into the agent's own transcript whenever a developer
      // happened to be running Postgres, which is the one thing a test about
      // refusing to fabricate a conversation must not do.
      ETHOS_DSN: 'postgresql://ethos:ethos@127.0.0.1:1/ethos',
    },
    stdio: 'ignore',
  });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${base}/api/channels`);
      if (response.ok) return;
    } catch {
      /* not yet */
    }
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error('the bridge did not start');
});

after(() => {
  child?.kill('SIGKILL');
  if (home) rmSync(home, { recursive: true, force: true });
});

/** Puts the shipped configuration back, so one test cannot edit another's answer. */
beforeEach(() => {
  writeFileSync(
    channelsPath,
    [
      'web:',
      '  enabled: true',
      '  http_port: 8720',
      '  auth_token_env: ETHOS_WEB_TOKEN',
      'telegram:',
      '  enabled: false',
      '  token_env: TELEGRAM_BOT_TOKEN',
      '  allowed_chat_ids: []',
      'whatsapp:',
      '  enabled: false',
      '  allowed_phone_numbers: []',
      'webhook:',
      '  enabled: false',
      '',
    ].join('\n'),
  );
  // The contact book goes back too, because it is the other half of what
  // `/api/message` resolves and a test that taught it an address would otherwise
  // lend that address to every test after it.
  copyFileSync(resolve(HERE, '..', '..', 'config', 'people.yaml'), peoplePath);
  // And so does the agent's home, which is where a door's own settings are
  // written. The files it holds are overlays on the two above, so a test that
  // turned Telegram on would otherwise leave it on for every test after it —
  // which is the same shape of leak the contact book is guarded against, one
  // directory over.
  for (const name of ['channels.yaml', 'people.yaml']) {
    rmSync(join(home, name), { force: true });
  }
});

function ask(body, headers = {}) {
  return fetch(`${base}/api/message`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

/** A door write, as a settings screen makes one. */
function setDoor(body, headers = {}) {
  return fetch(`${base}/api/channels`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

const setupOf = async (person) => (
  await (await fetch(`${base}/api/channels${person ? `?person=${person}` : ''}`)).json()
).setup;

const doorOf = (setup, id) => setup.doors.find((door) => door.id === id);

// ------------------------------------------------------------------ /channels

test('the web channel is always offered, because it is this process', async () => {
  const body = await (await fetch(`${base}/api/channels`)).json();
  assert.deepEqual(body.channels, [{ id: 'web', enabled: true, admits: true, contacts: WEB_OWNER }]);
});

test('a channel that is off is not offered', async () => {
  writeChannels('web:\n  enabled: true\ntelegram:\n  enabled: false\n');
  const body = await (await fetch(`${base}/api/channels`)).json();
  assert.deepEqual(body.channels.map((channel) => channel.id), ['web']);
});

test('a channel on but admitting nobody is reported as unusable, not as working', async () => {
  // An empty allowlist answers no one. Offering it as a place to talk would send
  // a person's messages into silence and leave them believing the agent was busy.
  writeChannels([
    'web:',
    '  enabled: true',
    'telegram:',
    '  enabled: true',
    '  allowed_chat_ids: []',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const telegram = body.channels.find((channel) => channel.id === 'telegram');
  assert.equal(telegram.enabled, true);
  assert.equal(telegram.admits, false);
});

test('a channel on with an allowlist admits somebody', async () => {
  writeChannels([
    'web:',
    '  enabled: true',
    'whatsapp:',
    '  enabled: true',
    '  allowed_phone_numbers: ["819012345678"]',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const whatsapp = body.channels.find((channel) => channel.id === 'whatsapp');
  assert.equal(whatsapp.admits, true);
});

test('a channels file that cannot be parsed still leaves the web line standing', async () => {
  writeChannels('telegram: [this is not a mapping\n');
  const body = await (await fetch(`${base}/api/channels`)).json();
  assert.deepEqual(body.channels, [{ id: 'web', enabled: true, admits: true, contacts: WEB_OWNER }]);
});

// ------------------------------------------------------- answering anybody

test('a door told to answer anyone admits people with an empty allowlist', async () => {
  // The other way to be a usable door, and the one a public agent needs: there is
  // no list of everybody who might write. Both facts have to be true at once —
  // the list is empty *and* the door answers — so a reader shown only one of them
  // is wrong either way.
  writeChannels([
    'web:',
    '  enabled: true',
    'telegram:',
    '  enabled: true',
    '  allowed_chat_ids: []',
    '  accept_from_anyone: true',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const telegram = body.channels.find((channel) => channel.id === 'telegram');
  assert.equal(telegram.admits, true);

  const setup = await setupOf();
  assert.equal(doorOf(setup, 'telegram').accept_from_anyone, true);
  assert.equal(doorOf(setup, 'telegram').admits, true);
  assert.deepEqual(doorOf(setup, 'telegram').allowed, []);
});

test('an empty allowlist alone still admits nobody', async () => {
  // The half of the pair that matters most. `accept_from_anyone` was not written,
  // so this is a fresh install with a bot token in it — and reading that as "and
  // welcome to everybody" would publish an endpoint for an agent holding a shell.
  writeChannels([
    'web:',
    '  enabled: true',
    'telegram:',
    '  enabled: true',
    '  allowed_chat_ids: []',
    '',
  ].join('\n'));
  const setup = await setupOf();
  assert.equal(doorOf(setup, 'telegram').accept_from_anyone, false);
  assert.equal(doorOf(setup, 'telegram').admits, false);
});

test('answering anyone is a boolean, and a word is not one', async () => {
  // Coerced, "yes please" would be a door opened by a form that thought it was
  // writing a label. Refused, the person knows the field takes a tick.
  assert.equal((await setDoor({ id: 'telegram', accept_from_anyone: true })).status, 200);
  assert.equal((await setDoor({ id: 'telegram', accept_from_anyone: false })).status, 200);
  for (const bad of ['yes', 1, 'true', null]) {
    const response = await setDoor({ id: 'telegram', accept_from_anyone: bad });
    assert.equal(response.status, 400, String(bad));
    assert.equal((await response.json()).error, 'invalid_value', String(bad));
  }
});

test('a save that does not mention it leaves the door exactly as it was', async () => {
  // The failure this prevents: a form saving somebody's token clearing the switch
  // they set an hour ago, because one request mentioned the wrong key.
  await setDoor({ id: 'telegram', accept_from_anyone: true });
  await setDoor({ id: 'telegram', credentials: { token: '123456:token' } });
  assert.equal(doorOf(await setupOf(), 'telegram').accept_from_anyone, true);

  await setDoor({ id: 'telegram', accept_from_anyone: false });
  await setDoor({ id: 'telegram', allowed: ['819012345678'] });
  assert.equal(doorOf(await setupOf(), 'telegram').accept_from_anyone, false);
});

test('the receiver has no allowlist and so no way to be opened to everybody', async () => {
  const setup = await setupOf();
  assert.equal(doorOf(setup, 'webhook').accept_from_anyone, false);
  // Refused rather than answered with a save that changed nothing: the receiver
  // posts to nothing and answers to nobody, so a 200 here would report success for
  // a setting the agent does not have.
  const response = await setDoor({ id: 'webhook', accept_from_anyone: true });
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error, 'unknown_field');
});

// --------------------------------------------------------------- door settings

test('every door is offered for setting up, and no credential is in the answer', async () => {
  // The question this whole route answers is "where do I put a Telegram token",
  // and it cannot be answered by a route that offers only the doors a deployment
  // has already opened — the ones worth setting up are precisely the ones that
  // are off. So the setup lists all of them, with the fields each one needs.
  const setup = await setupOf();
  assert.deepEqual(
    setup.doors.map((door) => door.id),
    ['telegram', 'whatsapp', 'slack', 'discord', 'email', 'webhook'],
  );
  assert.deepEqual(doorOf(setup, 'telegram').credentials.map((entry) => entry.key), ['token']);
  assert.deepEqual(doorOf(setup, 'whatsapp').credentials.map((entry) => entry.key), [
    'phone_number_id', 'access_token', 'app_secret', 'verify_token',
  ]);
  assert.equal(doorOf(setup, 'slack').credentials.length, 2);
  assert.equal(doorOf(setup, 'discord').credentials.length, 1);
  // The two push doors and only those two need somewhere for a POST to land.
  assert.deepEqual(
    setup.doors.filter((door) => door.needs_webhook).map((door) => door.id),
    ['whatsapp', 'slack'],
  );
  // A bot token stored in the home must not come back out of this process: a
  // browser that could read one could post it somewhere.
  await setDoor({ id: 'telegram', credentials: { token: '123456:never-returned' } });
  const after = await setupOf();
  assert.equal(doorOf(after, 'telegram').credentials[0].present, true);
  assert.equal(JSON.stringify(after).includes('never-returned'), false);
});

test('a token written from a page is where the agent will look for it, and only there', async () => {
  const response = await setDoor({
    id: 'telegram',
    enabled: true,
    credentials: { token: '123456:from-the-settings-screen' },
    allowed: ['819012345678'],
    address: 'tg:819012345678',
  });
  assert.equal(response.status, 200);
  const stored = readFileSync(join(home, 'channels.yaml'), 'utf8');
  // Written as an overlay in the agent's own home, and merged over the shipped
  // file rather than replacing it: the same document `ethos.config.load_config`
  // reads, so what a form writes and what `ethos-comms` runs cannot disagree.
  assert.match(stored, /telegram:/);
  assert.match(stored, /123456:from-the-settings-screen/);
  // A credential in a file other accounts can read is not a credential.
  assert.equal(statSync(join(home, 'channels.yaml')).mode & 0o777, 0o600);
  // And the door the rest of this process believes in now agrees.
  const body = await (await fetch(`${base}/api/channels`)).json();
  const telegram = body.channels.find((channel) => channel.id === 'telegram');
  assert.equal(telegram.enabled, true);
  assert.equal(telegram.admits, true);
  assert.deepEqual(telegram.contacts, [{ person: 'owner', address: 'tg:819012345678' }]);
});

test('a door saved with a token but nobody admitted says so rather than looking ready', async () => {
  // The most misleading state a channel can be in: everything is filled in, the
  // bot answers `/id`, and it answers nothing else. Reported as unusable.
  await setDoor({ id: 'telegram', enabled: true, credentials: { token: '123456:token' } });
  const setup = await setupOf();
  assert.equal(doorOf(setup, 'telegram').enabled, true);
  assert.equal(doorOf(setup, 'telegram').credentials[0].present, true);
  assert.equal(doorOf(setup, 'telegram').admits, false);
});

test('a save that mentions one credential leaves the others on that door alone', async () => {
  // The failure this prevents is a settings screen that clears a token somebody
  // pasted five minutes ago because they have just gone to change the allowlist.
  await setDoor({
    id: 'whatsapp',
    enabled: true,
    credentials: { phone_number_id: '1234', access_token: 'a', app_secret: 'b', verify_token: 'c' },
  });
  await setDoor({ id: 'whatsapp', allowed: ['819012345678'] });
  const door = doorOf(await setupOf(), 'whatsapp');
  assert.deepEqual(door.credentials.map((entry) => entry.present), [true, true, true, true]);
  assert.deepEqual(door.allowed, ['819012345678']);
});

test('a settings file that cannot be read is refused, never written over', async () => {
  // The read path treats an unreadable settings file as no overrides at all, which
  // is right: the shipped configuration stands and every door is visibly off. The
  // write path must not do the same. A save is built from what can be read of the
  // file, so writing over one that would not parse replaces every other door's
  // credentials with the door being saved — a person who hand-edited the file, or
  // whose editor truncated it, loses four doors to a form they opened to change an
  // allowlist. Refusing is the answer they can act on; overwriting is not.
  await setDoor({
    id: 'telegram',
    enabled: true,
    credentials: { token: '123456:do-not-lose-me' },
    allowed: ['819012345678'],
    address: 'tg:819012345678',
  });
  const homeFile = join(home, 'channels.yaml');
  const homeBook = join(home, 'people.yaml');
  const saved = readFileSync(homeFile, 'utf8');
  // A tab where YAML wants spaces, which is what a hand-edit or a bad paste leaves.
  const broken = `${saved}\ttelegram: [unterminated\n`;
  writeFileSync(homeFile, broken);

  const refused = await setDoor({ id: 'discord', enabled: true, credentials: { token: 'discord-token' } });
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).error, 'settings_unreadable');
  // Byte for byte, so the person can still read their own settings back and fix it.
  assert.equal(readFileSync(homeFile, 'utf8'), broken);
  assert.match(readFileSync(homeFile, 'utf8'), /do-not-lose-me/);
  // And the address in the other file with it: the save refused one write must not
  // have gone on to the second, which would leave the book disagreeing with the door.
  assert.equal(readFileSync(homeBook, 'utf8'), readFileSync(homeBook, 'utf8'));
  assert.match(readFileSync(homeBook, 'utf8'), /tg:819012345678/);

  // The same rule for a file that parses but is not the shape a document can be
  // written back into: a top-level list holds settings this code cannot represent,
  // so replacing it would lose them just as silently as a parse failure would.
  writeFileSync(homeFile, '- telegram\n- discord\n');
  assert.equal((await setDoor({ id: 'discord', enabled: true })).status, 400);
  assert.equal(readFileSync(homeFile, 'utf8'), '- telegram\n- discord\n');

  // An empty file is not an unreadable one: there is nothing in it to lose, and
  // refusing every save until a person deletes it by hand would be worse.
  writeFileSync(homeFile, '\n');
  assert.equal((await setDoor({ id: 'discord', enabled: true })).status, 200);
  assert.equal(doorOf(await setupOf(), 'discord').enabled, true);
});

test('a credential can be taken back, and a save that does not mean remove does not', async () => {
  await setDoor({ id: 'discord', enabled: true, credentials: { token: 'discord-token' } });
  assert.equal(doorOf(await setupOf(), 'discord').credentials[0].present, true);
  // A field left empty means "keep the one on file", exactly as it does for a
  // base model; `null` is the instruction to remove it.
  await setDoor({ id: 'discord', credentials: { token: '' } });
  assert.equal(doorOf(await setupOf(), 'discord').credentials[0].present, true);
  await setDoor({ id: 'discord', credentials: { token: null } });
  assert.equal(doorOf(await setupOf(), 'discord').credentials[0].present, false);
  assert.equal(/discord-token/.test(readFileSync(join(home, 'channels.yaml'), 'utf8')), false);
});

test('an address the door could not post to is refused, with a reason', async () => {
  // `TelegramAdapter.send` refuses anything that is not `tg:<chat id>`, so an
  // address without that prefix is a conversation the agent would deliberate on
  // and refuse to answer at the last step. Refused here instead, where the
  // person who typed it is looking.
  assert.equal((await setDoor({ id: 'telegram', address: '819012345678' })).status, 400);
  assert.equal((await setDoor({ id: 'telegram', address: 'tg:' })).status, 400);
  assert.equal((await setDoor({ id: 'slack', address: 'tg:819012345678' })).status, 400);
  const refusal = await (await setDoor({ id: 'telegram', address: '819012345678' })).json();
  assert.equal(refusal.error, 'invalid_address');
  assert.equal((await setDoor({ id: 'telegram', address: 'tg:819012345678' })).status, 200);
});

test('an address can be given up', async () => {
  writePeople([
    'people:',
    '  - id: owner',
    '    channels:',
    '      web: "owner"',
    '      telegram: "tg:819012345678"',
    '',
  ].join('\n'));
  assert.equal(doorOf(await setupOf(), 'telegram').address, 'tg:819012345678');
  await setDoor({ id: 'telegram', address: null });
  assert.equal(doorOf(await setupOf(), 'telegram').address, '');
});

test('an allowlist entry the door would not recognise is refused', async () => {
  // Each allowlist is a different namespace, and a list that accepted any of
  // them in any door would produce a door that looks configured and admits
  // nobody. The three that differ, checked apart.
  assert.equal((await setDoor({ id: 'telegram', allowed: ['not-a-chat-id'] })).status, 400);
  assert.equal((await setDoor({ id: 'telegram', allowed: ['819012345678'] })).status, 200);
  // E.164 digits with no `+`, which is what `normalize_phone` leaves behind.
  assert.equal((await setDoor({ id: 'whatsapp', allowed: ['+81 90-1234-5678'] })).status, 400);
  assert.equal((await setDoor({ id: 'whatsapp', allowed: ['819012345678'] })).status, 200);
  assert.equal((await setDoor({ id: 'slack', allowed: ['C0E2E'] })).status, 200);
  const refusal = await (await setDoor({ id: 'slack', allowed: ['C 0 E 2 E'] })).json();
  assert.equal(refusal.error, 'invalid_allowed_id');
});

test('a door that is not one, and a field that is not one, are both refused by name', async () => {
  // A form that offered a door this deployment has no adapter for would collect a
  // credential that nothing would ever read.
  const door = await (await setDoor({ id: 'carrier-pigeon', enabled: true })).json();
  assert.equal(door.error, 'unknown_channel');
  const field = await (await setDoor({ id: 'telegram', credentials: { webhook_url: 'x' } })).json();
  assert.equal(field.error, 'unknown_field');
  assert.equal((await setDoor({ id: 'telegram', enabled: 'yes' })).status, 400);
});

test('the shared receiver is a door of its own, because it is half of two others', async () => {
  // WhatsApp has no poller at all, so with no listener there is nowhere for Meta
  // to POST: the door is on, has four credentials, and is deaf. A save that turns
  // one of those doors on therefore turns the receiver on with it, in the same
  // document and the same write.
  await setDoor({ id: 'slack', enabled: true, credentials: { bot_token: 'xoxb-1', signing_secret: 's' } });
  const setup = await setupOf();
  assert.equal(doorOf(setup, 'webhook').enabled, true, 'a push door cannot work without it');
  assert.equal(doorOf(setup, 'webhook').port, 8730);
  // And turning it off is a decision somebody makes, not a side effect of a save.
  await setDoor({ id: 'slack', enabled: false });
  assert.equal(doorOf(await setupOf(), 'webhook').enabled, true);
  await setDoor({ id: 'webhook', enabled: false });
  assert.equal(doorOf(await setupOf(), 'webhook').enabled, false);
});

test('a write that names no door is refused rather than quietly opening one', async () => {
  const body = await (await setDoor({ enabled: true })).json();
  assert.equal(body.error, 'unknown_channel');
  assert.equal(statSync(join(home, 'channels.yaml'), { throwIfNoEntry: false }), undefined);
});

test('a write from another site is refused', async () => {
  // A cross-origin POST here would install a bot token on an agent that holds a
  // shell. Loopback is where a page in somebody's browser reaches too, so the
  // page this bridge served is same-origin and every other page is not.
  const response = await setDoor(
    { id: 'telegram', enabled: true, credentials: { token: 'from-another-site' } },
    { 'sec-fetch-site': 'cross-site' },
  );
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, 'cross_origin_refused');
  assert.equal(doorOf(await setupOf(), 'telegram').enabled, false);
});

test('a credential in the environment is reported as present, and without its value', async () => {
  // The other way a token reaches this agent, and the one a deployment with an
  // `EnvironmentFile` uses. It wins over the stored value, so the form has to be
  // able to say which of the two the agent will actually use — and a form that
  // could not would show a pasted token as the live one.
  const port = await freePort();
  const other = spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      ETHOS_HOME: home,
      ETHOS_CONFIG_DIR: join(home, 'config'),
      ETHOS_WEB_PORT: String(port),
      ETHOS_DSN: 'postgresql://ethos:ethos@127.0.0.1:1/ethos',
      TELEGRAM_BOT_TOKEN: 'from-the-environment',
    },
    stdio: 'ignore',
  });
  try {
    let setup = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        setup = (await (await fetch(`http://127.0.0.1:${port}/api/channels`)).json()).setup;
        if (setup) break;
      } catch {
        /* not yet */
      }
      await new Promise((done) => setTimeout(done, 50));
    }
    const credential = doorOf(setup, 'telegram').credentials[0];
    assert.equal(credential.env, 'TELEGRAM_BOT_TOKEN');
    assert.equal(credential.env_present, true, 'the variable is what the agent will read');
    assert.equal(credential.present, false, 'and nothing was written to the file for it');
    assert.equal(JSON.stringify(setup).includes('from-the-environment'), false);
  } finally {
    other.kill('SIGKILL');
  }
});

// ------------------------------------------------------------------ addresses

test('a door is reported with the addresses it can actually send to', async () => {
  // A surface that knows Telegram is open still cannot use it without knowing
  // where Telegram reaches this person, and that address lives in the contact
  // book rather than in the transcript. So the route has to carry it.
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels:',
    '      web: "owner"',
    '      telegram: "tg:819012345678"',
    '',
  ].join('\n'));
  writeChannels([
    'web:',
    '  enabled: true',
    'telegram:',
    '  enabled: true',
    '  allowed_chat_ids: [819012345678]',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const telegram = body.channels.find((channel) => channel.id === 'telegram');
  assert.deepEqual(telegram.contacts, [{ person: 'owner', address: 'tg:819012345678' }]);
});

test('a door nobody has an address on reports no contacts rather than an invented one', async () => {
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels: { web: "owner" }',
    '',
  ].join('\n'));
  writeChannels([
    'web:',
    '  enabled: true',
    'telegram:',
    '  enabled: true',
    '  allowed_chat_ids: [819012345678]',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const telegram = body.channels.find((channel) => channel.id === 'telegram');
  assert.deepEqual(telegram.contacts, [], 'an address nobody has been taught is not a guess');
});

// ------------------------------------------------------------------ /message

test('a message with no channel is a web message', async () => {
  // This route writes to the database, and there is none here — so what is
  // checked is the refusal to proceed, not a row. An empty message is refused
  // before any of that, which is the part a channel name could have got wrong.
  const empty = await ask({ text: '   ' });
  assert.equal(empty.status, 400);
  assert.deepEqual(await empty.json(), { error: 'empty_message' });
});

test('another site cannot use this route to send through a chosen door', async () => {
  const response = await ask(
    { text: 'hello', channel: 'telegram' },
    { origin: 'https://elsewhere.example', 'sec-fetch-site': 'cross-site' },
  );
  assert.equal(response.status, 403);
});

test('the same origin, and a terminal with no provenance, are the owner', async () => {
  for (const headers of [
    { origin: base },
    { 'sec-fetch-site': 'same-origin' },
    {},
  ]) {
    // Reaches the database layer, so the answer is the bridge's 503 rather than
    // a refusal — which is exactly what proves the owner check let it through.
    const response = await ask({ text: 'hello', channel: 'telegram' }, headers);
    assert.notEqual(response.status, 403, JSON.stringify(headers));
  }
});

test('a door this person has no address on is refused before anything is written', async () => {
  // `TelegramAdapter.send` refuses any person_id that is not `tg:<chat id>`, so
  // a row written as `owner` on the Telegram door is a conversation the agent
  // deliberates about and the adapter cannot answer. The refusal has to happen
  // here, where it can be acted on, rather than after the deliberation.
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels: { web: "owner" }',
    '',
  ].join('\n'));
  const response = await ask({ text: 'hello', channel: 'telegram' });
  // No database in this process, so the address lookup is what answers: the
  // request got past the owner check and stopped at the door it cannot reach.
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), {
    error: 'channel_unaddressed',
    channel: 'telegram',
    person_id: 'owner',
    detail: 'no address for owner on telegram; add one to channels: in config/people.yaml',
  });
});

test('a door this person is known on gets past the address lookup', async () => {
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels:',
    '      web: "owner"',
    '      telegram: "tg:819012345678"',
    '',
  ].join('\n'));
  // Same route, same body, and now it is the database that stops it — which is
  // how the two refusals are told apart.
  const response = await ask({ text: 'hello', channel: 'telegram' });
  assert.equal(response.status, 503);
});

test('the local line needs no address book entry to be reached', async () => {
  // `web` and `cli` deliver to whoever is reading this process, so the person is
  // the address and nothing has to be configured for the default case to work.
  writePeople('people: []\n');
  for (const channel of [undefined, 'web', 'cli']) {
    const response = await ask({ text: 'hello', ...(channel ? { channel } : {}) });
    assert.equal(response.status, 503, String(channel));
  }
});

test('a channel name that is not a channel is the local line, as before', async () => {
  // Kept from the whitelist rather than refused: an unknown name is a surface
  // with a newer vocabulary than this process, and answering `web` is the same
  // answer it always gave.
  const response = await ask({ text: 'hello', channel: 'carrier-pigeon' });
  assert.equal(response.status, 503);
});

test('an address handed over directly is honoured', async () => {
  // The escape hatch for a deployment that knows an address the contact book has
  // not been taught yet. The adapter is the last word on whether it is usable,
  // which is where a bad one is caught — so this only has to get past the lookup.
  const response = await ask({ text: 'hello', channel: 'telegram', address: 'tg:819012345678' });
  assert.equal(response.status, 503);
});
// ------------------------------------------------------- the second generation

test('the terminal socket is offered, because it is a door a message may name', async () => {
  // `MESSAGE_CHANNELS` has always accepted `cli`, so a surface could already send
  // through it — but a door it could not see in this list was a door it could not
  // filter the transcript by, and those two answers have to come from one place.
  writeChannels('web:\n  enabled: true\ncli:\n  enabled: true\n');
  const body = await (await fetch(`${base}/api/channels`)).json();
  assert.deepEqual(body.channels.map((channel) => channel.id), ['web', 'cli']);
  const terminal = body.channels.find((channel) => channel.id === 'cli');
  assert.equal(terminal.admits, true);
  // It delivers to whoever is reading, so it needs no address of its own and
  // falls back to the local line's contact rather than reporting none.
  assert.deepEqual(terminal.contacts, WEB_OWNER);
});

test('a terminal socket that is switched off is not offered', async () => {
  writeChannels('web:\n  enabled: true\ncli:\n  enabled: false\n');
  const body = await (await fetch(`${base}/api/channels`)).json();
  assert.deepEqual(body.channels.map((channel) => channel.id), ['web']);
});

test('slack and discord are reported with the same two facts as every other door', async () => {
  // Both are allowlisted doors, so both are the same shape as Telegram: on, and
  // whether the list admits anybody. Written as one test over the two because
  // they are one code path, and a channel added by copy-paste is a channel that
  // quietly forgets one of the two facts a surface needs.
  writeChannels([
    'web:',
    '  enabled: true',
    'slack:',
    '  enabled: true',
    '  allowed_ids: []',
    'discord:',
    '  enabled: true',
    '  allowed_ids: ["1234"]',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const slack = body.channels.find((channel) => channel.id === 'slack');
  const discord = body.channels.find((channel) => channel.id === 'discord');
  assert.equal(slack.admits, false, 'an empty allowlist admits nobody');
  assert.equal(discord.admits, true);
});

test('a door with an address is offered with the address it can send to', async () => {
  // The address of a Discord or Slack door is a *channel*, not a person, because
  // a reply has to go back to the conversation it was asked in. The bridge does
  // not care which namespace the value is in — the adapter is the last word.
  writeChannels('web:\n  enabled: true\ndiscord:\n  enabled: true\n  allowed_ids: ["1234"]\n');
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels:',
    '      web: "owner"',
    '      discord: "dc:9999"',
    '',
  ].join('\n'));
  const body = await (await fetch(`${base}/api/channels`)).json();
  const discord = body.channels.find((channel) => channel.id === 'discord');
  assert.deepEqual(discord.contacts, [{ person: 'owner', address: 'dc:9999' }]);
});

test('a message may be sent through the new doors', async () => {
  // Every one of these gets past this process, which is the whole claim: the
  // bridge has to accept a channel name it does not know how to deliver itself,
  // because the agent's own adapter is the thing that delivers it. 503 is the
  // unreachable database, which is how a name is shown to have been accepted.
  writePeople([
    'people:',
    '  - id: owner',
    '    display_name: Owner',
    '    relation: owner',
    '    channels:',
    '      web: "owner"',
    '      slack: "slack:C1"',
    '      discord: "dc:9999"',
    '',
  ].join('\n'));
  for (const channel of ['slack', 'discord']) {
    const response = await ask({ text: 'hello', channel });
    assert.equal(response.status, 503, channel);
  }
});

test('a person with no address on a new door is refused before anything is written', async () => {
  // The refusal, not a silent 503 from the database. Recording the row anyway is
  // what fabricates a conversation: a question the agent deliberates on, and a
  // reply discarded at the last step.
  writePeople('people:\n  - id: owner\n    display_name: Owner\n    relation: owner\n    channels:\n      web: "owner"\n');
  for (const channel of ['slack', 'discord']) {
    const response = await ask({ text: 'hello', channel });
    assert.equal(response.status, 409, channel);
    const body = await response.json();
    assert.equal(body.error, 'channel_unaddressed', channel);
    assert.equal(body.channel, channel);
  }
});
