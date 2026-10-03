import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { strict as assert } from 'node:assert';
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createSettings } from '../../core/dist/index.js';
import { screenText } from './screen.mjs';

/**
 * `/channels` in the full-screen interface, over a pty.
 *
 * Ink only paints when stdout is a terminal, so this drives the real program
 * through `script(1)` rather than rendering the component — which is the point,
 * because a panel that throws when it is actually painted is a panel no unit test
 * of the same function would have caught.
 *
 * The agent behind the link is a handful of JSON routes, and the doors it
 * reports are the deployment's: Telegram open with an address, Slack open with an
 * empty allowlist. Those are the two states a reader has to be able to tell
 * apart, and a panel that renders them the same way is worse than no panel.
 */

const DOORS = {
  channels: [
    { id: 'web', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'owner' }] },
    {
      id: 'telegram',
      enabled: true,
      admits: true,
      contacts: [{ person: 'owner', address: 'tg:819012345678' }],
    },
    { id: 'slack', enabled: true, admits: false, contacts: [] },
  ],
};

function startBridge() {
  const server = createServer((request, response) => {
    const send = (payload) => {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify(payload));
    };
    if (request.url?.startsWith('/api/channels')) return send(DOORS);
    if (request.url?.startsWith('/api/snapshot')) {
      return send({
        presence: { state: 'idle', focus: null, ts: Date.now() },
        control: {},
        model: null,
        catalogue: 0,
        cycles: [],
        intentions: [],
        actions: [],
        thoughts: [],
        budget: null,
        guardian: null,
        messages: [
          {
            id: 'm1',
            ts: new Date().toISOString(),
            direction: 'inbound',
            channel: 'telegram',
            conversation_key: 'telegram:819012345678',
            person_id: 'tg:819012345678',
            text: 'are you still there',
            social_decision: null,
            delivery: null,
          },
          {
            id: 'm2',
            ts: new Date().toISOString(),
            direction: 'outbound',
            channel: 'telegram',
            conversation_key: 'telegram:819012345678',
            person_id: 'tg:819012345678',
            text: 'I am',
            social_decision: null,
            delivery: { status: 'sent' },
          },
        ],
        identity: null,
      });
    }
    response.writeHead(404).end('{}');
    return undefined;
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));
const COLUMNS = 104;
const ROWS = 40;

function hasScript() {
  const probe = spawnSync('sh', ['-c', 'command -v script >/dev/null 2>&1'], { stdio: 'ignore' });
  return !probe.error && probe.status === 0;
}

async function runSession(directory, keys, { rows, columns, settle }) {
  const capture = join(directory, 'capture.raw');
  const wrapper = join(directory, 'session.sh');
  writeFileSync(wrapper, [
    '#!/bin/sh',
    `stty rows ${rows} cols ${columns} 2>/dev/null`,
    `exec ${JSON.stringify(process.execPath)} ${JSON.stringify(entry)}`,
    '',
  ].join('\n'));
  chmodSync(wrapper, 0o700);

  const steps = keys
    .map(([key, wait], index) => {
      const file = join(directory, `key-${index}`);
      writeFileSync(file, key);
      return `sleep ${Math.max(0.05, (wait ?? 400) / 1000)}; cat ${JSON.stringify(file)}`;
    })
    .join('; ');
  const producer = `( sleep ${Math.max(0.2, settle / 1000)}${steps ? `; ${steps}` : ''}; sleep 0.8; printf '\\021'; sleep 1 )`;
  const driver = join(directory, 'driver.sh');
  writeFileSync(driver, [
    '#!/bin/sh',
    `${producer} \\`,
    `  | script -q ${JSON.stringify(capture)} ${JSON.stringify(wrapper)}`,
    '',
  ].join('\n'));
  await new Promise((resolve, reject) => {
    const run = spawn('sh', [driver], { stdio: 'ignore' });
    run.on('error', reject);
    run.on('close', () => resolve());
  });
  return readFileSync(capture, 'utf8');
}

async function drive(keys, options = {}) {
  const { rows = ROWS, columns = COLUMNS, settle = 2200 } = options;
  const directory = mkdtempSync(join(tmpdir(), 'phone-doors-'));
  try {
    const raw = await runSession(directory, keys, { rows, columns, settle });
    return screenText(raw, { columns, rows });
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

async function withAgent(run) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-doors-'));
  const bridge = await startBridge();
  const previous = process.env.PROJECT_PHONE_CONFIG;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    settings: createSettings({ interface: 'agent', agentUrl: bridge.url, agentPerson: 'owner' }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });
  try {
    return await run({ path });
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
    await bridge.close();
  }
}

const skip = hasScript() ? false : 'needs a pty from script(1)';

test('/channels lists the doors, and says which is in use', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/channels', 600], ['\r', 1800]]);
    assert.match(screen, /CHANNELS/);
    assert.match(screen, /Telegram/);
    assert.match(screen, /Slack/);
    // The address is what makes the door usable, so it has to be on screen.
    assert.match(screen, /tg:819012345678/);
  });
});

test('a door that admits nobody is not shown as though it did', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/channels', 600], ['\r', 1800]]);
    // Slack is on with an empty allowlist. The line has to say so, because a
    // reader who is offered it as a place to talk is a reader whose messages go
    // into silence while they wait for an answer.
    assert.match(screen, /allowlist is empty/);
    assert.match(screen, /answer nobody/);
  });
});

test('a bare /channel names the door the terminal is talking through', { skip }, async () => {
  await withAgent(async () => {
    // A bare `/channel` reports rather than moves — the same way a bare `/name`
    // reports the name — and the answer is the local line until something moves
    // it. Reporting with silence would make the reporting form a no-op that looks
    // like a broken command.
    const screen = await drive([['/channel', 600], ['\r', 1500]]);
    assert.match(screen, /via Web/);
  });
});

test('the composer is badged only once a door other than the local one is chosen', { skip }, async () => {
  await withAgent(async () => {
    const before = await drive([]);
    assert.doesNotMatch(before, /^\s*Web\s+❯/m, 'the local line needs no badge');

    const after = await drive([['/channel telegram', 600], ['\r', 1500]]);
    assert.match(after, /Telegram/);
    // The badge sits on the prompt row, so the reader can see where the next
    // line is going without opening a panel.
    assert.match(after, /Telegram\s+❯/);
  });
});

test('a door that is not open is refused with the names that are', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/channel carrier-pigeon', 600], ['\r', 1500]]);
    // The refusal has to be actionable, and the only actionable part of "unknown
    // channel" is the list of what would have worked.
    assert.match(screen, /No channel named carrier-pigeon/);
    assert.match(screen, /telegram/);
  });
});

test('the transcript is narrowed to the door the terminal is on', { skip }, async () => {
  await withAgent(async () => {
    // Both messages in the fixture are Telegram, so moving there keeps them and
    // the composer shows which door will carry the next one.
    const screen = await drive([['/channel telegram', 600], ['\r', 1500]]);
    assert.match(screen, /are you still there/);
  });
});
