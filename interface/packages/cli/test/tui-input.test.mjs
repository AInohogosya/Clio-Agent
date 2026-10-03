import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { MAX_TERMINAL_TEXT_LENGTH } from '@project-phone/core';
import { ACCENT_PRESETS, createPalette } from '../dist/palette.js';
import { deleteBefore, insertAt, isCtrl, nextAccent, runCommand } from '../dist/tui-input.js';

const SETTINGS = { theme: 'dark', language: 'en', accent: '#F97316' };

function recorder() {
  const calls = [];
  const actions = {
    setInput: (value) => calls.push(['setInput', value]),
    setCursor: (value) => calls.push(['setCursor', value]),
    setScroll: (value) => calls.push(['setScroll', value]),
    setScreen: (value) => calls.push(['setScreen', value]),
    setOverlay: (value) => calls.push(['setOverlay', value]),
    submit: (value) => calls.push(['submit', value]),
    clearConversation: () => calls.push(['clear']),
    interrupt: () => calls.push(['interrupt']),
    quit: () => calls.push(['quit']),
    patchSettings: (patch) => calls.push(['patch', patch]),
    say: (text, tone) => calls.push(['say', text, tone]),
    t: (key) => key,
  };
  return { calls, actions };
}

const state = {
  input: '',
  cursor: 0,
  scroll: 0,
  limit: 10,
  chatHeight: 10,
  scrollable: false,
  pending: false,
  screen: 'chat',
  overlay: 'none',
  settings: SETTINGS,
};

test('typing in the middle of a draft keeps the caret where it was put', () => {
  // The caret used to jump to the end of the line on every insertion, so a typo
  // in the middle of a message could not be corrected in place.
  assert.deepEqual(insertAt('hello', 5, ' there'), { input: 'hello there', cursor: 11 });
  assert.deepEqual(insertAt('helo', 2, 'l'), { input: 'hello', cursor: 3 }, 'a letter lands in the middle');
  assert.deepEqual(insertAt('', 0, 'hi'), { input: 'hi', cursor: 2 });
  // A caret past the end is clamped rather than producing a gap.
  assert.deepEqual(insertAt('ab', 99, 'c'), { input: 'abc', cursor: 3 });
});

test('the draft cannot grow past the terminal limit', () => {
  const full = 'x'.repeat(MAX_TERMINAL_TEXT_LENGTH);
  const overflow = insertAt(full, 10, 'yyyy');
  assert.equal(overflow.input.length, MAX_TERMINAL_TEXT_LENGTH);
  assert.ok(overflow.cursor <= MAX_TERMINAL_TEXT_LENGTH);
  // A paste is clipped rather than rejected.
  assert.equal(insertAt('ab', 2, 'z'.repeat(50_000)).input, `ab${'z'.repeat(MAX_TERMINAL_TEXT_LENGTH - 2)}`);
});

test('backspace removes the character before the caret and stops at the start', () => {
  assert.deepEqual(deleteBefore('abc', 2), { input: 'ac', cursor: 1 });
  assert.deepEqual(deleteBefore('abc', 3), { input: 'ab', cursor: 2 });
  assert.equal(deleteBefore('abc', 0), null);
});

test('a control chord is matched from either the letter or the raw code', () => {
  assert.equal(isCtrl('c', { ctrl: true }, 'c'), true);
  assert.equal(isCtrl('\u0003', { ctrl: true }, 'c'), true);
  assert.equal(isCtrl('c', {}, 'c'), false, 'a plain letter is not a chord');
  assert.equal(isCtrl('x', { ctrl: true }, 'c'), false, 'nor is another chord');
});

test('every slash command is a command, including an unknown one', () => {
  const cases = [
    ['/help', 'setOverlay'],
    ['/?', 'setOverlay'],
    ['/settings', 'setScreen'],
    ['/config', 'setScreen'],
    ['/models', 'setScreen'],
    ['/clear', 'clear'],
    ['/interrupt', 'interrupt'],
    ['/quit', 'quit'],
    ['/exit', 'quit'],
  ];
  for (const [command, expected] of cases) {
    const { calls, actions } = recorder();
    assert.equal(runCommand(command, state, actions), true, command);
    assert.equal(calls[0]?.[0], expected, command);
  }
  // An unknown command must never be posted as a message.
  const unknown = recorder();
  assert.equal(runCommand('/halp', state, unknown.actions), true);
  assert.deepEqual(unknown.calls.map((call) => call[0]), ['say']);
  assert.equal(unknown.calls[0][2], 'danger');
  // Ordinary text is not a command at all.
  const plain = recorder();
  assert.equal(runCommand('hello', state, plain.actions), false);
  assert.deepEqual(plain.calls, []);
});

test('/help toggles, so the same command closes the panel it opened', () => {
  const opened = recorder();
  runCommand('/help', { ...state, overlay: 'none' }, opened.actions);
  assert.deepEqual(opened.calls, [['setOverlay', 'help']]);

  const closed = recorder();
  runCommand('/help', { ...state, overlay: 'help' }, closed.actions);
  assert.deepEqual(closed.calls, [['setOverlay', 'none']]);
});

test('a command argument keeps the case it was typed in', () => {
  // The command word is folded but the argument is the user's own text. Folding
  // the whole line meant `/send What Is NEW?` reached the provider as
  // `what is new?`.
  const sent = recorder();
  runCommand('/send What Is NEW?', state, sent.actions);
  assert.deepEqual(sent.calls, [['submit', 'What Is NEW?']]);

  const shouty = recorder();
  runCommand('/SEND Keep Me', state, shouty.actions);
  assert.deepEqual(shouty.calls, [['submit', 'Keep Me']]);

  const bare = recorder();
  runCommand('/send', state, bare.actions);
  assert.deepEqual(bare.calls, [], 'no argument, nothing sent');
});

test('/theme and /lang step rather than set', () => {
  const theme = recorder();
  runCommand('/theme', state, theme.actions);
  assert.deepEqual(theme.calls, [['patch', { theme: 'light' }]]);

  const language = recorder();
  runCommand('/lang', state, language.actions);
  assert.deepEqual(language.calls, [['patch', { language: 'ja' }]]);

  const japanese = recorder();
  runCommand('/lang', { ...state, settings: { ...SETTINGS, language: 'zh' } }, japanese.actions);
  assert.deepEqual(japanese.calls, [['patch', { language: 'en' }]]);
});

test('/accent sets an explicit colour and otherwise walks the presets', () => {
  const explicit = recorder();
  runCommand('/accent #0ea5e9', state, explicit.actions);
  assert.deepEqual(explicit.calls, [['patch', { accent: '#0EA5E9' }]]);

  // The presets live in one place now, so the cycle cannot drift from the row
  // the wizard paints.
  const stepped = recorder();
  runCommand('/accent', state, stepped.actions);
  const accent = stepped.calls[0][1].accent;
  assert.equal(accent, ACCENT_PRESETS[1]);
  assert.equal(nextAccent('#F97316', 'nonsense'), ACCENT_PRESETS[1]);
  assert.equal(nextAccent('#CA8A04', ''), ACCENT_PRESETS[0], 'the last preset wraps to the first');
});

test('/send posts its argument as a message', () => {
  const { calls, actions } = recorder();
  assert.equal(runCommand('/send hello there', state, actions), true);
  assert.deepEqual(calls, [['submit', 'hello there']]);

  const empty = recorder();
  runCommand('/send', state, empty.actions);
  assert.deepEqual(empty.calls, [], 'no argument means nothing to send');
});

test('/preview names a direction, and never guesses one', () => {
  // A toggle with no argument would have to guess which way the flag is standing,
  // and the input layer does not hold the agent's state to guess from. A safety
  // switch that flips on a coin is worse than one that asks, so a bare `/preview`
  // says what to type and sends nothing at all.
  const on = recorder();
  on.actions.agentControl = (action) => on.calls.push(['control', action]);
  assert.equal(runCommand('/preview on', state, on.actions), true);
  assert.deepEqual(on.calls, [['control', 'preview_on']]);

  const off = recorder();
  off.actions.agentControl = (action) => off.calls.push(['control', action]);
  assert.equal(runCommand('/preview off', state, off.actions), true);
  assert.deepEqual(off.calls, [['control', 'preview_off']]);

  // The word is folded, so `ON` is the same request as `on`.
  const shouty = recorder();
  shouty.actions.agentControl = (action) => shouty.calls.push(['control', action]);
  runCommand('/preview ON', state, shouty.actions);
  assert.deepEqual(shouty.calls, [['control', 'preview_on']]);

  for (const command of ['/preview', '/preview maybe']) {
    const bare = recorder();
    bare.actions.agentControl = (action) => bare.calls.push(['control', action]);
    assert.equal(runCommand(command, state, bare.actions), true, command);
    assert.deepEqual(bare.calls, [['say', 'previewUsage', 'warning']], command);
  }
});

test('/preview is refused where there is no agent behind it', () => {
  // The direct-provider surface has no agent and so no tools to switch off. A
  // command that reported success and did nothing is worse than one that says so.
  const direct = recorder();
  assert.equal(runCommand('/preview on', state, direct.actions), true);
  assert.deepEqual(direct.calls, [['say', 'interfaceDirectHint', 'warning']]);
});

test('/name reports the name, and names it when given one', () => {
  // The whole setting is a single word, so the argument is the name itself: there is
  // no provider, model or endpoint to disambiguate, and splitting the line would only
  // give a second way to get it wrong.
  const named = recorder();
  named.actions.agentName = (argument) => named.calls.push(['name', argument]);
  runCommand('/name Aria', state, named.actions);
  assert.deepEqual(named.calls, [['name', 'Aria']]);

  // An empty argument is a complete one: it reports rather than clearing, exactly as
  // `/person` and `/link` report their own values.
  const asked = recorder();
  asked.actions.agentName = (argument) => asked.calls.push(['name', argument]);
  runCommand('/name', state, asked.actions);
  assert.deepEqual(asked.calls, [['name', '']]);

  const long = recorder();
  long.actions.agentName = (argument) => long.calls.push(['name', argument]);
  runCommand('/name Agent Seven', state, long.actions);
  assert.deepEqual(long.calls, [['name', 'Agent Seven']], 'the whole argument, not one word of it');
});

test('/name is not offered where there is no agent behind it', () => {
  // A command that reports success and does nothing is worse than a refusal, so the
  // direct-provider surface says why rather than accepting the word.
  const { calls, actions } = recorder();
  assert.equal(runCommand('/name Aria', state, actions), true);
  assert.deepEqual(calls, [['say', 'interfaceDirectHint', 'warning']]);
});

test('the palette still answers for the surfaces that paint by name', () => {
  const palette = createPalette('#F97316', 'dark', 24);
  assert.equal(palette.ink, '#F0EEE8');
  assert.notEqual(palette.code(palette.accent), '', 'colour is emitted at depth 24');
  assert.equal(createPalette('#F97316', 'dark', 0).code('#F97316'), '', 'and not at depth 0');
});

// ------------------------------------------------------------------- /channel

test('/channel moves this terminal onto another door', () => {
  const { calls, actions } = recorder();
  const handled = runCommand('/channel telegram', state, {
    ...actions,
    agentChannel: (argument) => calls.push(['channel', argument]),
  });
  assert.equal(handled, true);
  assert.deepEqual(calls, [['channel', 'telegram']]);
});

test('/via is the same command, because it is the same idea', () => {
  const { calls, actions } = recorder();
  runCommand('/via slack', state, {
    ...actions,
    agentChannel: (argument) => calls.push(['channel', argument]),
  });
  assert.deepEqual(calls, [['channel', 'slack']]);
});

test('a bare /channel reports which door this is, rather than moving', () => {
  // Reporting is what the no-argument form is for, the same way a bare `/name`
  // reports the name and `/person` reports the person. There is no door to move
  // *to*, so an empty argument cannot mean "go somewhere else".
  const { calls, actions } = recorder();
  const handled = runCommand('/channel', state, {
    ...actions,
    agentChannel: (argument) => calls.push(['channel', argument]),
  });
  assert.equal(handled, true);
  assert.deepEqual(calls, [['channel', '']]);
});

test('/channel all goes back to the local line, rather than nowhere', () => {
  // A terminal cannot send one message to every door at once, so "all" cannot
  // mean that. It means the transcript, which is the only door that is always on.
  const { calls, actions } = recorder();
  for (const word of ['all', 'none', 'ALL']) {
    runCommand(`/channel ${word}`, state, {
      ...actions,
      agentChannel: (argument) => calls.push(['channel', argument]),
    });
  }
  assert.deepEqual(calls, [['channel', ''], ['channel', ''], ['channel', '']]);
});

test('/channels opens the list, because a terminal has no dropdown to pick from', () => {
  const { calls, actions } = recorder();
  runCommand('/channels', state, actions);
  assert.deepEqual(calls, [['setScreen', 'channels']]);
});

test('/channel on a surface with no agent says so rather than pretending', () => {
  // A command that reports success and does nothing is worse than one that is
  // absent, so the direct-provider surface says why it cannot move.
  const { calls, actions } = recorder();
  const handled = runCommand('/channel telegram', state, actions);
  assert.equal(handled, true);
  assert.deepEqual(calls, [['say', 'interfaceDirectHint', 'warning']]);
});

test('a mistyped channel is consumed, not sent as a message', () => {
  // Typing `/channle telegram` must not reach the agent as a question about
  // channels. Every command reports true, including one that is not recognised.
  const { calls, actions } = recorder();
  const handled = runCommand('/channle telegram', state, actions);
  assert.equal(handled, true);
  assert.equal(calls.some(([name]) => name === 'submit'), false);
  assert.equal(calls.some(([name]) => name === 'say'), true);
});
