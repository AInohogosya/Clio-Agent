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

/** A local provider the full-screen interface can actually reach. */
function startProvider() {
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const question = JSON.parse(raw).messages.at(-1)?.content ?? '';
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        choices: [{ message: { role: 'assistant', content: `An answer to ${question}.` }, finish_reason: 'stop' }],
      }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({ baseUrl: `http://127.0.0.1:${server.address().port}/v1`, close: () => new Promise((done) => server.close(done)) });
    });
  });
}

const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));
const COLUMNS = 104;
const ROWS = 40;

function hasScript() {
  // Probing by running `script` is unreliable: it exits non-zero when stdin is
  // not a terminal, so only check that the binary is present.
  const probe = spawnSync('sh', ['-c', 'command -v script >/dev/null 2>&1'], { stdio: 'ignore' });
  return !probe.error && probe.status === 0;
}

/**
 * Drives the full-screen interface through a pty, because Ink only paints when
 * stdout is a terminal.
 *
 * The session runs as `producer | script -q <capture> <wrapper>`: that is the
 * pipeline shape macOS `script` accepts, since a Node socket on stdin is
 * rejected with "tcgetattr/ioctl: Operation not supported on socket". The inner
 * command is a script file rather than `sh -c`, because BSD `script` folds
 * trailing arguments together and would break the quoting. Keystrokes are
 * `cat`-ed from their own files, so no byte passes through shell quoting.
 */
async function drive(keys, options = {}) {
  const { rows = ROWS, columns = COLUMNS, settle = 2200 } = options;
  const directory = mkdtempSync(join(tmpdir(), 'phone-tui-'));
  try {
    return replay(await runSession(directory, keys, { rows, columns, settle }), { rows, columns });
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
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
  // The pipeline has to stay on one logical line, so a newline continuation is
  // used rather than two lines.
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

  const raw = readFileSync(capture, 'utf8');
  return raw;
}

function replay(raw, { rows = ROWS, columns = COLUMNS } = {}) {
  return screenText(raw, { columns, rows });
}

async function withConfig(run) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-tui-'));
  const provider = await startProvider();
  const previous = process.env.PROJECT_PHONE_CONFIG;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  // A keyless local provider, pointed at a server that is actually there. The
  // old suite ran against no provider at all and passed by reading back the
  // program's own invented reply.
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    // `direct` is stated, not inherited: the default interface is the agent, and
    // these fixtures are about reaching a model provider over HTTP.
    settings: createSettings({ interface: 'direct', provider: 'ollama', model: 'stub-small', baseUrl: provider.baseUrl }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });
  const restore = async () => {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
    await provider.close();
  };
  try {
    return await run({ directory, path, provider });
  } finally {
    await restore();
  }
}

const skip = hasScript() ? false : 'needs a pty from script(1)';

test('the interface paints its header, composer and instrument panels', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([]);
    assert.match(screen, /Clio Agent/);
    assert.match(screen, /Direct line/);
    assert.match(screen, /COORDINATION/);
    assert.match(screen, /CONNECTION/);
    assert.match(screen, /The line is open\./);
    assert.match(screen, /❯/);
    assert.match(screen, /\/help/);
    // Nothing speaks unless it is spoken to.
    assert.doesNotMatch(screen, /AMBIENT/i);
  });
});

test('a typed message is echoed in the composer and then answered', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['a quiet question', 500], ['\r', 3000]]);
    assert.match(screen, /You/);
    assert.match(screen, /a quiet question/);
    assert.match(screen, /Assistant/);
    // The answer is the provider's, not the program's.
    assert.match(screen, /An answer to a quiet question\./);
    assert.match(screen, /Messages\s+02/);
  });
});

test('the saved transcript is read back by the next session', { skip }, async () => {
  await withConfig(async () => {
    await drive([['remembered line', 500], ['\r', 3000]]);
    const screen = await drive([]);
    assert.match(screen, /remembered line/);
    assert.match(screen, /Messages\s+02/);
  });
});

test('the settings wizard opens on the provider step', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['\u0013', 1500]]);
    assert.match(screen, /Settings/);
    assert.match(screen, /Provider setup/);
    assert.match(screen, /Select the provider to use\./);
    for (const provider of ['OpenAI', 'Anthropic', 'Google Gemini', 'Local / Ollama']) {
      assert.match(screen, new RegExp(provider.replace(/[/]/g, '\\/')), `missing ${provider}`);
    }
  });
});

test('the wizard walks to the credentials step and validates a missing key', { skip }, async () => {
  await withConfig(async ({ path }) => {
    // Start from a keyed provider, so the credential step has something to
    // refuse. A local provider needs no key and must not be blocked for it.
    const current = JSON.parse(readFileSync(path, 'utf8'));
    writeFileSync(path, `${JSON.stringify({
      ...current,
      settings: createSettings({ interface: 'direct', provider: 'openai', model: 'gpt-4o-mini' }),
    }, null, 2)}\n`, { mode: 0o600 });

    const screen = await drive([['\u0013', 1400], ['\r', 900], ['\r', 900]]);
    assert.match(screen, /API key/);
    assert.match(screen, /Base URL/);
    assert.match(screen, /Enter an API key to continue\./);
  });
});

test('a keyless local provider is not asked for a key', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['\u0013', 1400], ['\r', 900], ['\r', 1200]]);
    assert.doesNotMatch(screen, /Enter an API key to continue/);
    assert.match(screen, /Model discovery/);
  });
});

test('the help screen lists the keyboard and the slash commands', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['/help', 500], ['\r', 1500]]);
    assert.match(screen, /Keyboard/);
    assert.match(screen, /PgUp \/ PgDn/);
    assert.match(screen, /\/settings/);
    assert.match(screen, /\/accent/);
    assert.doesNotMatch(screen, /AMBIENT STREAM/);
    // /pause and /resume are gone with the ambient stream they controlled.
    assert.doesNotMatch(screen, /\/pause/);
    assert.doesNotMatch(screen, /\/resume/);
  });
});

test('a slash command runs without sending a message', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['/clear', 500], ['\r', 1200]]);
    assert.match(screen, /Messages\s+00/);
    assert.doesNotMatch(screen, /\/clear/);
  });
});

test('the composer wraps a long draft instead of corrupting the layout', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([['x'.repeat(180), 900]]);
    const rows = screen.split('\n');
    const overflow = rows.filter((line) => line.length > COLUMNS);
    assert.deepEqual(overflow, [], 'no row may exceed the terminal width');
    assert.match(screen, /180\/8192/);
  });
});

test('narrow terminals drop the sidebar instead of overlapping', { skip }, async () => {
  await withConfig(async () => {
    const screen = await drive([], { columns: 72, rows: 24, settle: 2400 });
    assert.match(screen, /Clio Agent/);
    assert.doesNotMatch(screen, /COORDINATION/, 'the sidebar is dropped at this width');
    const overflow = screen.split('\n').filter((line) => line.length > 72);
    assert.deepEqual(overflow, []);
  });
});

test('the terminal restores the screen when it exits', { skip }, async () => {
  await withConfig(async () => {
    const directory = mkdtempSync(join(tmpdir(), 'phone-tui-term-'));
    let out = '';
    try {
      out = await runSession(directory, [], { rows: ROWS, columns: COLUMNS, settle: 2200 });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
    assert.ok(out.includes('\u001B[?1049h'), 'should enter the alternate screen');
    assert.ok(out.includes('\u001B[?1049l'), 'should leave the alternate screen');
    assert.ok(out.includes('\u001B[?25h'), 'should restore the cursor');
  });
});

test('the written configuration is the shared one the web bridge reads', { skip }, async () => {
  await withConfig(async () => {
    await drive([['shared state check', 500], ['\r', 3000]]);
    const path = process.env.PROJECT_PHONE_CONFIG;
    const state = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(state.origin, 'cli');
    assert.equal(state.transport, 'file');
    assert.equal(state.messages[0].text, 'shared state check');
    assert.equal(state.messages[1].role, 'assistant');
    assert.equal(state.settings.apiKey, '');
  });
});

test('Esc closes the help panel, as the panel itself says', { skip }, async () => {
  await withConfig(async () => {
    // The panel reads "Esc closes this", and typing behind it used to land in
    // an invisible composer.
    const screen = await drive([['/help', 600], ['\r', 900], ['\u001b', 900]]);
    assert.doesNotMatch(screen, /Keyboard/, 'the panel is gone');
    assert.match(screen, /❯/, 'the composer is back');
  });
});

test('a scroll-back survives whatever happens next', { skip }, async () => {
  await withConfig(async () => {
    const keys = [];
    for (let index = 0; index < 5; index += 1) keys.push([`line ${index}`, 180], ['\r', 380]);
    for (let index = 0; index < 30; index += 1) keys.push(['\u001b[A', 60]);
    // A state change that is not a new message: it emits a snapshot, which is
    // what used to yank the view back to the newest line. With the ambient
    // stream running that happened every four seconds, so scrollback was
    // unreadable; with a second surface writing, it happens whenever anything
    // moves.
    keys.push(['/theme', 400], ['\r', 1200]);
    // A short terminal, so the transcript is taller than the pane.
    const screen = await drive(keys, { rows: 20, settle: 3400 });

    assert.match(screen, /▲ \d+\/\d+/, 'the scroll position is shown');
    assert.match(screen, /\bline 0\b/, 'the oldest line is still on screen');
    assert.doesNotMatch(screen, /\bline 4\b/, 'and the newest is not the only thing shown');
  });
});

test('sending a message brings the view back to the newest line', { skip }, async () => {
  await withConfig(async () => {
    const keys = [];
    for (let index = 0; index < 5; index += 1) keys.push([`line ${index}`, 180], ['\r', 380]);
    for (let index = 0; index < 30; index += 1) keys.push(['\u001b[A', 60]);
    keys.push(['after scrolling', 300], ['\r', 1400]);
    const screen = await drive(keys, { rows: 20, settle: 3400 });

    // Asking a question is a request to see the answer, so this one does follow.
    assert.match(screen, /An answer to after scrolling\./);
    assert.doesNotMatch(screen, /▲ \d+\/\d+/, 'the view is following again');
  });
});

test('a provider refusal is shown, and no answer is invented', { skip }, async () => {
  const directory = mkdtempSync(join(tmpdir(), 'phone-tui-refuse-'));
  const provider = await startProvider();
  const previous = process.env.PROJECT_PHONE_CONFIG;
  const path = join(directory, 'config.json');
  process.env.PROJECT_PHONE_CONFIG = path;
  // An endpoint that answers with a refusal, in the provider's own words.
  const refuser = createServer((request, response) => {
    request.on('data', () => undefined);
    request.on('end', () => {
      response.writeHead(401, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Incorrect API key provided' } }));
    });
  });
  await new Promise((resolve) => refuser.listen(0, '127.0.0.1', resolve));
  writeFileSync(path, `${JSON.stringify({
    version: 2,
    revision: 0,
    updatedAt: 0,
    origin: 'cli',
    transport: 'file',
    // A keyless local provider, so the endpoint is reachable over loopback HTTP
    // and no credential is needed to get as far as the refusal.
    settings: createSettings({
      interface: 'direct',
      provider: 'ollama',
      model: 'stub-small',
      baseUrl: `http://127.0.0.1:${refuser.address().port}/v1`,
    }),
    messages: [],
    credential: { present: false, source: 'none', hint: '' },
  }, null, 2)}\n`, { mode: 0o600 });
  try {
    const screen = await drive([['are you there', 500], ['\r', 3000]]);
    assert.match(screen, /Incorrect API key provided/, 'the provider own words are shown');
    assert.match(screen, /401/);
    assert.doesNotMatch(screen, /I hear you/, 'nothing is invented in its place');
    const state = JSON.parse(readFileSync(path, 'utf8'));
    assert.equal(state.messages.length, 1);
    assert.equal(state.messages[0].role, 'user');
  } finally {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    await new Promise((done) => refuser.close(done));
    await provider.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
