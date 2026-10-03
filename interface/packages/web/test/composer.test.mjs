import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

/**
 * A composer has to hold what was written into it.
 *
 * A textarea does not resize itself: `rows` decides where it starts and nothing
 * after that moves it. So a field left at a fixed few rows shows a person the
 * last three lines of their own message and hides the rest — and because the
 * part on screen is the part the field is scrolled to, the lines they can see
 * are the ones they did not just write. It is not a cosmetic fault: a field that
 * cannot show its contents is a field a person cannot check their sentence in.
 *
 * There is no browser here to render anything, so what is checked is the shape of
 * the answer: both surfaces send through the one component that owns the growth,
 * the growth is measured before the frame is painted, and the ceiling is decided
 * in the stylesheet rather than repeated in code. The behaviour itself is a
 * measurement of the box, and re-implementing it per screen is how two
 * composers end up disagreeing about how tall a message is allowed to be.
 */

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCREEN_ROOT = join(WEB_ROOT, 'src', 'screens');
const COMPOSER = join(WEB_ROOT, 'src', 'components', 'Composer.tsx');
const STYLES = join(WEB_ROOT, 'src', 'styles.css');
const SCREENS = ['AgentScreen.tsx', 'ChatScreen.tsx'];

test('both conversations compose through the one field', () => {
  for (const name of SCREENS) {
    const source = readFileSync(join(SCREEN_ROOT, name), 'utf8');
    assert.match(source, /<Composer\b/, `${name} composes without Composer`);
    assert.doesNotMatch(source, /<textarea\b/, `${name} keeps a field of its own`);
  }
});

test('a screen that renders a composer hands it the draft it owns', () => {
  // The failure is silent until somebody types. A composer given its own `value`
  // rather than the screen's draft renders a field that ignores every keystroke,
  // and a field that ignores every keystroke is indistinguishable from a field
  // that works.
  for (const name of SCREENS) {
    const source = readFileSync(join(SCREEN_ROOT, name), 'utf8');
    assert.match(source, /<Composer[\s\S]{0,200}?value=\{draft\}/, `${name} composes from a draft it does not own`);
    assert.match(source, /<Composer[\s\S]{0,400}?onSubmit=/, `${name} renders a composer it never sends from`);
  }
});

test('the field is measured, and measured before the paint', () => {
  const source = readFileSync(COMPOSER, 'utf8');
  assert.match(source, /<textarea\b/, 'the composer owns no field');
  // Height cannot be read from a field that is holding one, so it has to be let
  // go first — and then handed a height back, in pixels, before the browser
  // draws. Measured in an ordinary effect the field spends a frame short.
  assert.match(source, /useLayoutEffect/, 'the field is measured after the paint');
  assert.match(source, /style\.height = 'auto'/, 'a field pinned to a height cannot be measured');
  assert.match(source, /style\.height = `\$\{/, 'the field measures its content and never writes it back');
  // A textarea's own height is the height of `rows`, whatever the text in it:
  // read the box and a forty-line message measures as one line. `scrollHeight` is
  // the only reading of the content that exists.
  assert.match(source, /scrollHeight/, 'the field asks the box how tall its text is');
  assert.doesNotMatch(
    source,
    /getBoundingClientRect\(\)\.height/,
    'the field trusts the box, which never grows past its rows',
  );
  // The floor and the ceiling are read back from the stylesheet rather than
  // written down a second time here, so `max-h` on `.composer-field` stays the
  // one place where "up to a point" is decided.
  assert.match(source, /getComputedStyle\(field\)/, 'the field ignores the bounds the stylesheet sets');
  assert.match(source, /style\.minHeight/, 'the field cannot see the floor it may not shrink past');
  assert.match(source, /style\.maxHeight/, 'the field cannot see the ceiling it may not grow past');
});

test('the field re-measures when the text rewraps under it', () => {
  const source = readFileSync(COMPOSER, 'utf8');
  assert.match(source, /ResizeObserver/, 'a pane that narrows leaves the field at a height for text that no longer exists');
  // The observer also sees the height this hook writes; reacting to that would
  // be measuring a change it has already accounted for.
  assert.match(source, /offsetWidth/, 'the field re-measures on its own writing');
});

test('the field eases into its new height, and scrolls past the ceiling', () => {
  const source = readFileSync(COMPOSER, 'utf8');
  const styles = readFileSync(STYLES, 'utf8');
  const field = /\.composer-field\s*\{[^}]*\}/.exec(styles)?.[0] ?? '';
  // Growing in the right place but arriving there is still a glitch: the eye is
  // on the words, so the frame around them has to move with them.
  assert.match(field, /transition:\s*height\s+[\d.]+m?s/, '.composer-field jumps between heights');
  assert.match(field, /max-height:/, 'nothing bounds how tall the composer may grow');
  assert.match(field, /min-height:/, 'nothing keeps the composer from collapsing past one line');
  // Past the ceiling the message is still the reader's to see, so it is still
  // reachable; short of it, a scrollbar would be a control for nothing.
  assert.match(source, /style\.overflowY = scrolls \? 'auto' : 'hidden'/, 'the field cannot reach text past its ceiling');
});

test('the field answers to the keys the interface promises', () => {
  const source = readFileSync(COMPOSER, 'utf8');
  assert.match(source, /event\.key === 'Enter' && !event\.shiftKey/, 'Enter no longer sends, or Shift+Enter no longer breaks the line');
  assert.match(source, /event\.key === 'Escape' && pending/, 'Escape no longer interrupts a reply in flight');
  assert.match(source, /maxLength=\{MAX_PROMPT_LENGTH\}/, 'the field lets through messages the agent will refuse');
});