import { strict as assert } from 'node:assert';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { createTranslator } from '@project-phone/core';

/**
 * A conversation has to read like a conversation.
 *
 * Three things make that true, and each of them is one line to break: the
 * transcript opens on the newest line, it stays there while the newest line is
 * moving, and it lets go the moment the reader scrolls back to read something.
 * A feed that re-anchors on every arrival cannot be scrolled at all — the view
 * is dragged down each time a token lands — and a feed that never follows leaves
 * a reader watching a reply arrive off the bottom edge.
 *
 * There is no browser here to render anything, so what is checked is the shape
 * of the answer: both transcripts go through the one component that owns this
 * behaviour, and neither screen has quietly grown a scrollbar of its own. The
 * behaviour itself is a hook over `scrollTop`, and duplicating it per screen is
 * how two conversations end up disagreeing about where their newest line is.
 */

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCREEN_ROOT = join(WEB_ROOT, 'src', 'screens');
const FEED = join(WEB_ROOT, 'src', 'components', 'ConversationFeed.tsx');

function sourceFiles(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) found.push(...sourceFiles(path));
    else if (extname(path) === '.tsx') found.push(path);
  }
  return found;
}

test('both conversations read through the shared feed', () => {
  for (const name of ['AgentScreen.tsx', 'ChatScreen.tsx']) {
    const source = readFileSync(join(SCREEN_ROOT, name), 'utf8');
    assert.match(source, /<ConversationFeed\b/, `${name} renders its transcript without ConversationFeed`);
  }
});

test('no screen moves the scrollbar itself', () => {
  const files = sourceFiles(SCREEN_ROOT);
  assert.ok(files.length > 1, 'no screens were found to check');
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    assert.doesNotMatch(
      source,
      /scrollTop|scrollHeight|scrollIntoView|isNearBottom/,
      `${relative(WEB_ROOT, file)} reaches for the scrollbar itself`,
    );
  }
});

test('a screen that asks for a feed hands that feed to the one it renders', () => {
  /**
   * The failure this is about is invisible until a person sends something.
   *
   * A hook and a component can each build their own binding, and the feed then
   * scrolls while the surface's `toLatest` writes to a ref nothing renders —
   * which reads as working, because the button in the feed still scrolls. The
   * symptom is a reader who scrolled up, sent a message, and watched their own
   * words land off the bottom edge.
   */
  for (const file of sourceFiles(SCREEN_ROOT)) {
    const source = readFileSync(file, 'utf8');
    if (!/useConversationFeed\(/.test(source)) continue;
    assert.match(
      source,
      /<ConversationFeed[\s\S]{0,200}?feed=\{feed\}/,
      `${relative(WEB_ROOT, file)} builds a feed it does not hand to the one it renders`,
    );
  }
});

test('the shared feed owns the jump button and the follow', () => {
  const source = readFileSync(FEED, 'utf8');
  // The button is only rendered off the newest line: a permanent control for a
  // problem the reader does not have is chrome, not help.
  assert.match(source, /feed\.atBottom \? null :/, 'the jump button ignores where the reader is');
  assert.match(source, /ResizeObserver/, 'the feed cannot notice content that grows on its own');
  assert.match(source, /following\.current/, 'the feed cannot tell following from reading back');
});

test('the jump button says something in every language', () => {
  const words = ['en', 'ja', 'zh'].map((language) => createTranslator(language)('jumpToLatest'));
  for (const [index, line] of words.entries()) {
    assert.notEqual(line.trim(), '', `jumpToLatest is blank in ${['en', 'ja', 'zh'][index]}`);
    assert.doesNotMatch(line, /[{}]/, `jumpToLatest left a placeholder in ${['en', 'ja', 'zh'][index]}`);
  }
  // The tables fall back to English key by key, so an untranslated entry renders
  // without an error and reads as a half-finished button.
  assert.equal(new Set(words).size, words.length, 'the jump button is only translated once');
});
