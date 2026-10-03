import { strict as assert } from 'node:assert';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

/**
 * The settings page has to be readable with the pointer anywhere.
 *
 * A screen is not one box. It is a pinned header, a reading column, and the
 * margins either side of that column — and a wheel only reaches the surface it is
 * over. The failure this is about is quiet and easy to write, because the page
 * *does* scroll: the scroll box is the width of the column it scrolls, which
 * looks right in review and leaves the reader wheeling away over the header, or
 * over the margin beside the text, with nothing happening at all. It is a page
 * whose whole job is a long form, and reading to the bottom of it means first
 * finding the text with the pointer.
 *
 * The column stays the width it was — narrowing it to the scroll box's old width
 * would move the card and its text 80px on a desktop screen, which is a design
 * change nobody asked for. So the header and the margin are covered instead: both
 * are outside the scroll box by construction, and a wheel over either is handed
 * to the scroll box by the one hook that owns this. A screen that grows its own
 * copy of that is how two surfaces end up disagreeing about how far the page turns
 * per notch.
 */

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCREEN_ROOT = join(WEB_ROOT, 'src', 'screens');
const SCREEN = join(SCREEN_ROOT, 'SettingsScreen.tsx');
const HOOK = join(WEB_ROOT, 'src', 'hooks', 'useWheelScroll.ts');

test('a wheel over the settings header and margin still turns the page', () => {
  const source = readFileSync(SCREEN, 'utf8');
  // The handler belongs on the screen, not on the scroll box: the two places it
  // has to cover are the ones the scroll box is not.
  assert.match(source, /<div className="flex min-h-0 flex-1 flex-col" onWheel=\{wheelToBody\}>/, 'a wheel outside the settings column does nothing');
  assert.match(source, /const wheelToBody = useWheelScroll\(body\)/, 'the settings screen scrolls its own column by hand');
  // The ref has to be on the scroll box, or the hook forwards to nothing and
  // reads as working.
  assert.match(source, /overflow-y-auto[^"]*" ref=\{body\}/, 'the wheel is forwarded to something other than the scroll box');
});

test('the reading column keeps the width it had', () => {
  const source = readFileSync(SCREEN, 'utf8');
  const scroller = source.match(/<div className="[^"]*overflow-y-auto[^"]*" ref=\{body\}>/);
  assert.ok(scroller, 'the settings page has no scroll box');
  assert.match(scroller[0], /max-w-3xl/, 'the reading column moved, and with it the card and everything in it');
});

test('no screen translates a wheel itself', () => {
  // The same rule the feed test draws around `scrollTop`: one owner per
  // behaviour. The wheel has an owner now, and it is a hook rather than a screen.
  const files = readdirSync(SCREEN_ROOT);
  assert.ok(files.length > 1, 'no screens were found to check');
  for (const file of files) {
    const source = readFileSync(join(SCREEN_ROOT, file), 'utf8');
    assert.doesNotMatch(
      source,
      /scrollBy|addEventListener\(\s*['"]wheel/,
      `${file} moves the page for a wheel itself`,
    );
  }
});

test('the shared hook leaves a wheel over the content to the browser', () => {
  const source = readFileSync(HOOK, 'utf8');
  // Forwarding a wheel the browser already answered for scrolls the page twice per
  // notch, and it does it silently — momentum and nested scrollers included,
  // because the handler cannot tell them from its own.
  assert.match(source, /element\.contains\(/, 'the hook forwards wheels the content already handled');
  // Neither end may be pushed past: the content is finite, and a page that cannot
  // come back from a gesture is worse than one that refuses to turn.
  assert.match(source, /atTop/, 'the hook scrolls past the top of the content');
  assert.match(source, /atBottom/, 'the hook scrolls past the bottom of the content');
  // Anything that reports lines rather than pixels would otherwise turn the page
  // one pixel per notch, which reads as a stuck page rather than a slow one.
  assert.match(source, /deltaMode/, 'the hook ignores how the wheel counts');
});