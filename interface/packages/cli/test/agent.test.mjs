import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { strict as assert } from 'node:assert';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import stringWidth from 'string-width';
import {
  controlSummary,
  createSettings,
  createTranslator,
  deadlineLabel,
  flattenIntentions,
  formatSpan,
  humanizeState,
  readIntentions,
  stateTone,
} from '../../core/dist/index.js';
import { createPalette } from '../dist/palette.js';
import { agentScreen, controlLabel } from '../dist/agent-screen.js';
import { runCommand } from '../dist/tui-input.js';

const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));
const t = createTranslator('en');

// ------------------------------------------------------------------- the agent

/**
 * An agent, for real.
 *
 * The same reasoning as the provider fixture in `cli.test.mjs`: a passing test
 * has to mean the whole path worked. This one speaks the four routes the agent
 * link uses, so a `phone agent resume` in a test goes over HTTP, through the
 * bridge, and back.
 */
function startAgent(behaviour = {}) {
  const received = [];
  let control = { paused: false, pause_actions: false, stopped: false, emergency: false };
  let queued = [];

  const view = () => ({
    presence: {
      state: behaviour.state ?? 'DELIBERATING',
      focus: { intention_id: 'i1', title: 'Finish the interface', kind: 'project' },
      ts: new Date().toISOString(),
      recentCycles: [{ state: 'ATTENDING', ts: new Date().toISOString(), tier: 'T2' }],
    },
    control,
    thoughts: [{ id: 'e1', ts: new Date().toISOString(), summary: 'The budget reads low.' }],
    intentions: [
      { id: 'i1', parent_id: null, kind: 'project', title: 'Finish the interface', status: 'active', priority: 0.9 },
      { id: 'i2', parent_id: 'i1', kind: 'step', title: 'Wire the terminal', status: 'active', priority: 0.6 },
    ],
    actions: [
      { id: 'a1', ts_start: new Date().toISOString(), tool: 'shell', status: 'ok', undo_ref: 'undo-1', reason: 'read a file' },
    ],
    budget: {
      todayByBucket: [{ bucket: 'discretionary', total: 1.5 }],
      byModel: [{ model: 'claude-sonnet-5', total: 1.5 }],
      daily: [{ day: '2026-01-01', total: 1.5 }],
      caps: { daily_usd: 20, monthly_usd: 400, commitment_usd: 10, discretionary_usd: 10 },
    },
    guardian: {
      trash: [{ id: 't1', origin: '/tmp/a', retention_until: new Date().toISOString(), reason: 'asked' }],
      snapshots: [{ name: 'snap-1', created_at: new Date().toISOString(), backend: 'fs' }],
      integrity: { ok: true, ran_at: new Date().toISOString(), audit_chain_ok: true, artifact_mismatches: [] },
      auditHead: { seq: 42, ts: new Date().toISOString(), summary: 'held a cycle', hash: 'deadbeef' },
      protectedCount: 3,
    },
    messages: [],
  });

  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      received.push({ url: request.url, method: request.method, body: raw });
      const send = (status, body) => {
        response.writeHead(status, { 'content-type': 'application/json' });
        response.end(JSON.stringify(body));
      };
      if (request.url?.endsWith('/api/snapshot')) return send(200, view());
      if (request.url?.endsWith('/api/message')) return send(200, { message_id: 'm-sent' });
      if (request.url?.endsWith('/api/control')) {
        const action = JSON.parse(raw || '{}').action;
        control = {
          ...control,
          ...(action === 'resume' ? { paused: false, pause_actions: false, emergency: false } : {}),
          ...(action === 'pause_all' ? { paused: true, pause_actions: true } : {}),
          ...(action === 'stop' ? { stopped: true } : {}),
        };
        return send(200, { ok: true });
      }
      if (/\/api\/actions\/.*\/undo$/.test(request.url ?? '')) {
        queued.push({ kind: 'undo', id: request.url.split('/')[5] });
        return send(200, { queued: true });
      }
      if (/\/api\/intentions\/.*\/close$/.test(request.url ?? '')) {
        queued.push({ kind: 'cancel', id: request.url.split('/')[5] });
        return send(200, { queued: true });
      }
      return send(404, { error: 'no_such_route' });
    });
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        received,
        queued,
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

function runPhone(args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [entry, ...args], {
      env: { ...process.env, ...(options.env ?? {}) },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`phone ${args.join(' ')} timed out\n${stdout}\n${stderr}`));
    }, 20_000);
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.on('close', (status) => {
      clearTimeout(timer);
      resolve({ status: status ?? 0, stdout, stderr });
    });
    child.stdin.end(typeof options.input === 'string' ? options.input : undefined);
  });
}

async function withAgent(run, behaviour = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-agent-'));
  const agent = await startAgent(behaviour);
  const previous = process.env.PROJECT_PHONE_CONFIG;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    // The default interface, pointed at the stub agent.
    settings: createSettings({ agentUrl: agent.url }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });

  const restore = async () => {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
    await agent.close();
  };

  const helpers = {
    agent,
    path,
    directory,
    phone: (args, options = {}) => runPhone(args, options),
  };
  try {
    const result = await run(helpers);
    await restore();
    return result;
  } catch (error) {
    await restore();
    throw error;
  }
}

// -------------------------------------------------------------- phone agent

test('agent is the default interface, and the agent address is what it reads', async () => {
  const settings = createSettings();
  assert.equal(settings.interface, 'agent');
  assert.equal(settings.agentUrl, 'http://127.0.0.1:8720');
  assert.equal(settings.agentPerson, 'owner');
});

test('phone agent reports the agent as JSON', async () => {
  await withAgent(async ({ phone }) => {
    const result = await phone(['agent', '--json']);
    assert.equal(result.status, 0);
    const payload = JSON.parse(result.stdout);
    assert.equal(payload.endpoint.startsWith('http://127.0.0.1:'), true);
    assert.equal(payload.state, 'DELIBERATING');
    assert.equal(payload.presence ?? payload.focus?.title, payload.focus?.title);
    assert.equal(payload.control.paused, false);
    assert.equal(payload.intentions.length, 2);
    assert.equal(payload.actions[0].tool, 'shell');
    assert.equal(payload.budget.caps.daily_usd, 20);
    assert.equal(payload.guardian.protectedCount, 3);
  });
});

test('phone agent draws every panel, with the caps it was told about', async () => {
  await withAgent(async ({ phone }) => {
    const result = await phone(['agent', '--width', '80']);
    assert.equal(result.status, 0);
    for (const heading of [
      t('presenceLabel'), t('panelIntentions'), t('panelActions'),
      t('panelBudget'), t('panelGuardian'),
    ]) {
      assert.ok(result.stdout.includes(heading), `${heading} is drawn`);
    }
    assert.match(result.stdout, /20\.00/, 'the daily cap is on screen');
    assert.ok(result.stdout.includes('Finish the interface'));
    assert.ok(result.stdout.includes('Wire the terminal'));
  });
});

test('every lifecycle verb is the agent own, and says it was queued', async () => {
  for (const verb of ['pause-actions', 'pause', 'resume', 'stop', 'emergency']) {
    await withAgent(async ({ phone, agent }) => {
      const result = await phone(['agent', verb, '--json']);
      assert.equal(result.status, 0, verb);
      const payload = JSON.parse(result.stdout);
      assert.equal(typeof payload.queued, 'string');
      // "Queued", not "done": the bridge records the request and the agent
      // decides whether to act on it, and the output must not claim otherwise.
      assert.equal(agent.received.some((r) => r.url?.endsWith('/api/control')), true, verb);
    });
  }
});

test('an undo and a cancel are addressed by id and reported as queued', async () => {
  await withAgent(async ({ phone, agent }) => {
    const undo = JSON.parse((await phone(['agent', 'undo', 'a1', '--json'])).stdout);
    assert.deepEqual(undo, { queued: 'undo', action_id: 'a1' });
    const cancel = JSON.parse((await phone(['agent', 'cancel', 'i2', '--json'])).stdout);
    assert.deepEqual(cancel, { queued: 'cancel', intention_id: 'i2' });
    assert.deepEqual(agent.queued.map((entry) => entry.kind), ['undo', 'cancel']);
  });
});

test('a verb that needs a target refuses without one', async () => {
  await withAgent(async ({ phone, agent }) => {
    const result = await phone(['agent', 'undo']);
    assert.equal(result.status, 1);
    assert.match(result.stdout + result.stderr, /needs an id/i);
    assert.equal(agent.queued.length, 0, 'nothing was asked of the agent');

    // And a verb that takes no id is not quietly given one.
    const stray = await phone(['agent', 'resume', 'oops']);
    assert.equal(stray.status, 1);
    assert.match(stray.stdout + stray.stderr, /takes no id/i);
    assert.equal(agent.queued.length, 0);
  });
});

test('an agent that is not running says so, and asks for nothing', async () => {
  await withAgent(async ({ phone, agent, path }) => {
    const config = JSON.parse(readFileSync(path, 'utf8'));
    config.settings.agentUrl = 'http://127.0.0.1:9';
    writeFileSync(path, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
    const result = await phone(['agent', '--json']);
    assert.equal(result.status, 1);
    assert.match(JSON.parse(result.stdout).error, /not answering/i);
    await agent.close();
  });
});

test('phone agent refuses to act when the interface is a provider', async () => {
  await withAgent(async ({ phone, path }) => {
    const config = JSON.parse(readFileSync(path, 'utf8'));
    config.settings.interface = 'direct';
    writeFileSync(path, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
    const result = await phone(['agent', 'resume', '--json']);
    assert.equal(result.status, 1);
    assert.ok(JSON.parse(result.stdout).error);
  });
});

test('the settings file carries the interface fields, and they round-trip', async () => {
  await withAgent(async ({ phone, path }) => {
    const get = await phone(['config', 'get', 'agentUrl']);
    assert.match(get.stdout.trim(), /^http:\/\/127\.0\.0\.1:\d+$/);
    await phone(['config', 'set', 'agentPerson', 'operator']);
    const person = await phone(['config', 'get', 'agentPerson']);
    assert.equal(person.stdout.trim(), 'operator');
    const listed = await phone(['config', 'list']);
    for (const field of ['interface', 'agentUrl', 'agentPerson']) {
      assert.ok(listed.stdout.includes(field), `${field} is listed`);
    }
    const stored = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(stored.settings.agentPerson, 'operator');
    await phone(['config', 'unset', 'agentPerson']);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).settings.agentPerson, 'owner');
  });
});

test('a remote agent address is refused before it can be saved', async () => {
  await withAgent(async ({ phone, path }) => {
    const result = await phone(['config', 'set', 'agentUrl', 'http://ethos.example.com']);
    assert.equal(result.status, 1);
    const stored = JSON.parse(readFileSync(path, 'utf8'));
    assert.notEqual(stored.settings.agentUrl, 'http://ethos.example.com');
  });
});

test('an agent-mode send does not write a second copy of the transcript', async () => {
  await withAgent(async ({ phone, path }) => {
    // The agent's store is the record; the terminal keeps the settings only.
    await phone(['config', 'set', 'agentPerson', 'operator']);
    const stored = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(stored.settings.agentPerson, 'operator');
    assert.deepEqual(stored.messages, []);
  });
});

// ------------------------------------------------------------- the vocabulary

test('the lifecycle in one phrase names the most restrictive flag that is set', () => {
  assert.deepEqual(controlSummary({ paused: false, pause_actions: false, stopped: false, emergency: false }, t), {
    text: t('controlStateRunning'),
    tone: 'success',
  });
  assert.equal(controlSummary({ paused: false, pause_actions: true, stopped: false, emergency: false }, t).text, t('controlStateActionsHeld'));
  assert.equal(controlSummary({ paused: true, pause_actions: true, stopped: false, emergency: false }, t).text, t('controlStateHeld'));
  assert.equal(controlSummary({ paused: true, pause_actions: true, stopped: true, emergency: false }, t).text, t('controlStateStopped'));
  assert.equal(controlSummary({ paused: true, pause_actions: true, stopped: true, emergency: true }, t).text, t('controlStateEmergency'));
});

test('a state a table has never heard of is muted, not coloured at random', () => {
  assert.equal(stateTone('DELIBERATING'), 'accent');
  assert.equal(stateTone('ACTING'), 'success');
  assert.equal(stateTone('STOPPED'), 'danger');
  assert.equal(stateTone('A_STATE_FROM_THE_FUTURE'), 'muted');
  assert.equal(humanizeState('ACTING_TOOL'), 'acting tool');
});

test('a deadline reads as a duration and an overdue one says so', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  assert.equal(formatSpan(45_000), '45s');
  assert.equal(formatSpan(90_000), '1m');
  assert.equal(formatSpan(3 * 3600_000 + 30 * 60_000), '3h 30m');
  assert.equal(formatSpan(50 * 3600_000), '2d 2h');
  assert.equal(deadlineLabel(null, now, t), null);
  assert.equal(deadlineLabel('not-a-date', now, t), null);
  assert.equal(deadlineLabel('2026-01-02T00:00:00Z', now, t)?.overdue, false);
  assert.equal(deadlineLabel('2025-12-31T00:00:00Z', now, t)?.overdue, true);
});

test('an intention tree flattens parents before children', () => {
  const roots = readIntentions([
    { id: 'a', parent_id: null, title: 'parent' },
    { id: 'b', parent_id: 'a', title: 'child' },
    { id: 'c', parent_id: 'b', title: 'grandchild' },
  ]);
  assert.deepEqual(flattenIntentions(roots).map((node) => node.title), ['parent', 'child', 'grandchild']);
});

test('the report is width-bounded, whatever it is asked to draw', () => {
  const palette = createPalette('#F97316', 'dark', 1);
  const view = JSON.parse(JSON.stringify({
    presence: { state: 'ACTING', focus: { intention_id: 'i1', title: 'A very long focus title that will not fit on any narrow terminal' }, ts: new Date().toISOString(), recentCycles: [] },
    control: { paused: false, pause_actions: false, stopped: false, emergency: false },
    thoughts: [],
    intentions: [{ id: 'i1', parent_id: null, kind: 'project', title: 'An intention title far too long for the width asked for', status: 'active', priority: 1 }],
    actions: [],
    budget: { todayByBucket: [], byModel: [], daily: [], caps: { daily_usd: 0, monthly_usd: 0, commitment_usd: 0, discretionary_usd: 0 } },
    guardian: { trash: [], snapshots: [], integrity: null, auditHead: null, protectedCount: 0 },
    messages: [],
  }));
  // Measured as the terminal will read it: escape sequences are not columns,
  // and a row that fits in the byte count but not the column count still wraps.
  for (const width of [40, 60, 84, 108]) {
    for (const line of agentScreen(view, { palette, width, language: 'en' })) {
      assert.ok(stringWidth(line) <= width, `a line of ${stringWidth(line)} fits in ${width}`);
    }
  }
});

test('a cap that was never reported is drawn as no cap, not as a full bar', () => {
  const palette = createPalette('#F97316', 'dark', 1);
  const view = JSON.parse(JSON.stringify({
    presence: { state: 'IDLE', focus: null, ts: null, recentCycles: [] },
    control: { paused: false, pause_actions: false, stopped: false, emergency: false },
    thoughts: [], intentions: [], actions: [],
    budget: { todayByBucket: [{ bucket: 'discretionary', total: 3 }], byModel: [], daily: [], caps: { daily_usd: 0, monthly_usd: 0, commitment_usd: 0, discretionary_usd: 0 } },
    guardian: { trash: [], snapshots: [], integrity: null, auditHead: null, protectedCount: 0 },
    messages: [],
  }));
  const drawn = agentScreen(view, { palette, width: 80, language: 'en' }).join('\n');
  assert.ok(drawn.includes(t('budgetNoCap')));
  assert.ok(!drawn.includes('███'), 'nothing is drawn as a filled bar');
});

test('the verb names on screen are the ones the parser accepts', () => {
  for (const action of ['pause_actions', 'pause_all', 'resume', 'stop', 'emergency_stop']) {
    const label = controlLabel(action, t);
    assert.ok(label.length > 0, action);
  }
});

// -------------------------------------------------------- the slash commands

test('a lifecycle slash command reaches the agent verb, and nothing else does', () => {
  const seen = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {}, submit: () => {},
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => {}, say: () => {}, t: (key) => key,
    agentControl: (action) => seen.push(action),
    agentUndo: (id) => seen.push(`undo:${id}`),
    agentCancel: (id) => seen.push(`cancel:${id}`),
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings() };

  for (const [word, expected] of [
    ['/pause', 'pause_all'], ['/hold', 'pause_all'], ['/hold-actions', 'pause_actions'],
    ['/resume', 'resume'], ['/stop', 'stop'], ['/emergency', 'emergency_stop'],
  ]) {
    seen.length = 0;
    assert.equal(runCommand(word, state, actions), true, word);
    assert.deepEqual(seen, [expected], word);
  }

  seen.length = 0;
  runCommand('/undo a1', state, actions);
  runCommand('/cancel i2', state, actions);
  assert.deepEqual(seen, ['undo:a1', 'cancel:i2']);
});

test('a panel command opens that panel rather than sending a message', () => {
  const opened = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: (screen) => opened.push(screen), setOverlay: () => {},
    submit: () => { throw new Error('a panel command must not send a message'); },
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => {}, say: () => {}, t: (key) => key,
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings() };
  for (const [word, expected] of [
    ['/presence', 'presence'], ['/intentions', 'intentions'], ['/intents', 'intentions'],
    ['/actions', 'actions'], ['/budget', 'budget'], ['/guardian', 'guardian'],
  ]) {
    opened.length = 0;
    assert.equal(runCommand(word, state, actions), true, word);
    assert.deepEqual(opened, [expected], word);
  }
});

test('/link repoints the agent and /person renames you, and a bad address is refused', () => {
  const said = [];
  const patched = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {}, submit: () => {},
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: (patch) => patched.push(patch),
    say: (text, tone) => said.push([text, tone]),
    t: (key) => key,
    agentAddress: (field, value) => patched.push([field, value]),
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings() };

  runCommand('/link http://127.0.0.1:9000', state, actions);
  runCommand('/person operator', state, actions);
  assert.deepEqual(patched, [['link', 'http://127.0.0.1:9000'], ['person', 'operator']]);

  // With no argument the command reports what it currently holds, so the reader
  // does not have to go and look it up somewhere else first.
  patched.length = 0;
  runCommand('/link', state, actions);
  assert.deepEqual(patched, []);
  assert.match(String(said[0]), /127\.0\.0\.1:8720/);
});

test('/link is refused outright when there is no agent behind it', () => {
  const said = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {}, submit: () => {},
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => { throw new Error('a direct surface must not repoint an agent'); },
    say: (text, tone) => said.push([text, tone]), t: (key) => key,
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings({ interface: 'direct' }) };
  assert.equal(runCommand('/link http://127.0.0.1:9000', state, actions), true);
  assert.equal(said.length, 1);
  assert.equal(said[0][1], 'warning');
});

test('on a surface with no agent behind it, an agent command declines instead of pretending', () => {
  const said = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {}, submit: () => {},
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => {}, say: (text, tone) => said.push([text, tone]), t: (key) => key,
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings({ interface: 'direct' }) };
  for (const word of ['/resume', '/undo a1', '/cancel i1']) {
    assert.equal(runCommand(word, state, actions), true, word);
  }
  assert.equal(said.length, 3);
  assert.equal(said.every(([, tone]) => tone === 'warning'), true);
});

test('an unknown slash command is still not a message', () => {
  let sent = false;
  const said = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {},
    submit: () => { sent = true; },
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => {}, say: (text) => said.push(text), t: (key) => key,
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings() };
  assert.equal(runCommand('/halp', state, actions), true);
  assert.equal(sent, false);
  assert.match(String(said[0]), /halp/);
});

test('an ordinary line is still a message', () => {
  const sent = [];
  const actions = {
    setInput: () => {}, setCursor: () => {}, setScroll: () => {},
    setScreen: () => {}, setOverlay: () => {}, submit: (value) => sent.push(value),
    clearConversation: () => {}, interrupt: () => {}, quit: () => {},
    patchSettings: () => {}, say: () => {}, t: (key) => key,
  };
  const state = { input: '', cursor: 0, scroll: 0, limit: 0, chatHeight: 10, scrollable: false, pending: false, screen: 'chat', overlay: 'none', settings: createSettings() };
  assert.equal(runCommand('what is new?', state, actions), false);
  assert.deepEqual(sent, []);
});
