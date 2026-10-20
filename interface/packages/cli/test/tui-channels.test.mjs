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
      const now = new Date().toISOString();
      return send({
        presence: {
          state: 'ATTENDING',
          focus: { intention_id: 'int-1', title: 'Answer the porch', kind: 'reply' },
          ts: Date.now(),
          recentCycles: [
            { state: 'ATTENDING', ts: now, tier: 'T2' },
            { state: 'WORKING', ts: now, tier: 'T1' },
          ],
        },
        control: { paused: false, pause_actions: false, stopped: false, emergency: false, preview: false },
        model: { provider: 'anthropic', model: 'claude-sonnet-5', keyPresent: true, keyHint: 'sk-…12' },
        catalogue: 3,
        identity: { configured: true, selfName: 'Clio' },
        thoughts: Array.from({ length: 40 }, (_, index) => ({
          id: `th-${index}`,
          ts: now,
          summary: `thought number ${index}`,
        })),
        intentions: [
          {
            id: 'int-abcdef12-3456-7890',
            parent_id: null,
            kind: 'reply',
            title: 'Answer the porch',
            desired_end_state: 'the porch answered',
            status: 'active',
            priority: 1.5,
            deadline: null,
            commissioned_by: 'owner',
            budget_usd: 2,
            spent_usd: 0.5,
            origin: 'telegram',
            children: [],
            depth: 0,
          },
        ],
        actions: [
          {
            id: 'act-1234abcd-5678',
            ts_start: now,
            ts_end: null,
            thread_id: 'thread-99',
            tool: 'web.search',
            args_redacted: { q: 'porch light' },
            reason: 'the porch asked',
            status: 'running',
            result_digest: null,
            undo_ref: null,
          },
          {
            id: 'act-7777bbbb-1111',
            ts_start: now,
            ts_end: now,
            thread_id: null,
            tool: 'memory.write',
            args_redacted: null,
            reason: null,
            status: 'ok',
            result_digest: null,
            undo_ref: 'undo-1',
          },
        ],
        budget: {
          todayByBucket: [{ bucket: 'discretionary', total: 0.5 }],
          byModel: [{ model: 'claude-sonnet-5', total: 0.5 }],
          daily: [
            { day: '2026-10-16', total: 0.5 },
            { day: '2026-10-15', total: 1.5 },
          ],
          caps: { daily_usd: 20, monthly_usd: 400, commitment_usd: 10, discretionary_usd: 10 },
        },
        guardian: {
          trash: [{ id: 't1', origin: '/tmp/a', trashed_at: now, retention_until: now, reason: 'asked', path: null }],
          snapshots: [{ name: 'snap-1', created_at: now, reason: 'before the sweep', backend: 'fs' }],
          integrity: { ok: true, ran_at: now, audit_chain_ok: true, artifact_mismatches: [] },
          auditHead: { seq: 42, ts: now, summary: 'held a cycle', hash: 'deadbeefcafe' },
          protectedCount: 3,
        },
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
    // reports the name — and the answer is the terminal's own line until
    // something moves it. Reporting with silence would make the reporting form a
    // no-op that looks like a broken command.
    const screen = await drive([['/channel', 600], ['\r', 1500]]);
    assert.match(screen, /via TUI/);
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

test('a panel row number opens what the panel numbered', { skip }, async () => {
  await withAgent(async () => {
    // One correspondent, the terminal's own line, then three doors: row 4 is
    // the telegram door. `/channel 4` is the spelling that does not ask anybody
    // to retype an id the panel has already shown them.
    const screen = await drive([['/channel 4', 600], ['\r', 1500]]);
    assert.match(screen, /via Telegram/);
    assert.match(screen, /are you still there/);
  });
});

test('a row number past the list is refused with the count', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/channel 99', 600], ['\r', 1500]]);
    assert.match(screen, /No such row/);
  });
});

test('the thoughts panel is its own screen, and the whole record scrolls', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/thoughts', 600], ['\r', 1800]]);
    assert.match(screen, /THOUGHTS/);
    assert.match(screen, /thought number 29/, 'the recent tail is on screen');
    // Forty thoughts do not fit; the window says what it is showing, and the
    // rest are a few lines away rather than gone.
    assert.match(screen, /of 40/);
    const paged = await drive([
      ['/thoughts', 600], ['\r', 1200],
      ...Array.from({ length: 12 }, () => ['\u001b[B', 220]),
      ['\r', 1200],
    ]);
    assert.match(paged, /thought number 39/, 'the end of the record is reachable');
    assert.match(paged, /9\u201340 of 40/, 'the window says where it stopped');
  });
});

test('the intentions panel names what the reader can act on', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/intentions', 600], ['\r', 1800]]);
    assert.match(screen, /Answer the porch/);
    // The short id is what /cancel takes, so it is on the same line as the title.
    assert.match(screen, /int-abcd/, 'the short id is shown');
    // The same facts the web's intention card shows.
    assert.match(screen, /reply/);
    assert.match(screen, /p1\.50/);
    assert.match(screen, /the porch answered/);
    assert.match(screen, /\/cancel/);
  });
});

test('the actions panel shows the reason, the arguments and the undo path', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/actions', 600], ['\r', 1800]]);
    assert.match(screen, /web\.search/);
    assert.match(screen, /act-1234/, 'the short id is shown');
    assert.match(screen, /the porch asked/, 'the reason is shown');
    assert.match(screen, /porch light/, 'the redacted arguments are shown');
    assert.match(screen, /thread-99/);
    assert.match(screen, /\/undo/);
    assert.match(screen, /↺/, 'the undoable action is marked');
  });
});

test('the budget panel draws the seven-day chart the web draws', { skip }, async () => {
  await withAgent(async () => {
    const screen = await drive([['/budget', 600], ['\r', 1800]]);
    assert.match(screen, /BY DAY/);
    assert.match(screen, /\$0\.50/);
    assert.match(screen, /\$1\.50/);
  });
});
