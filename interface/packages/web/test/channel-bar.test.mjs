import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

/**
 * A transcript with four doors on it is four conversations and one reader, and
 * the navigation has to say which is which.
 *
 * The bar above the transcript is the whole navigation, and each of its two
 * kinds of entry works differently on purpose. The interfaces — the web line and
 * the terminal's — are one conversation each, so clicking one opens it. A
 * messaging app is a door several people talk through, so clicking one opens the
 * list of names under the bar, and a name opens the conversation. Two kinds of
 * entry, one bar: a filter and a list competing for the same row is the clutter
 * this replaces.
 *
 * There is no browser here, so what is checked is the shape of the answer: which
 * component draws the bar, that the interfaces are always on it while the apps
 * come from the configuration, that the names appear only for an app, and that
 * the pane narrows by the conversation key the kernel defines rather than by a
 * channel name of its own.
 */

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCREEN = join(WEB_ROOT, 'src', 'screens', 'AgentScreen.tsx');
const KIT = join(WEB_ROOT, 'src', 'components', 'agent', 'agent-kit.tsx');
const LINK = join(WEB_ROOT, 'src', 'hooks', 'useAgentLink.ts');

const screen = readFileSync(SCREEN, 'utf8');
const kit = readFileSync(KIT, 'utf8');
const link = readFileSync(LINK, 'utf8');

test('the bar is the navigation, and it is built from the configuration', () => {
  assert.match(screen, /<ChannelBar\b/, 'the conversation bar is not on the screen');
  // The interfaces first, always: they are one conversation each and need
  // nothing configured to be on the bar.
  assert.match(kit, /id: 'web', label: 'Web'/);
  assert.match(kit, /id: 'cli', label: 'TUI'/);
  // The apps come from the doors the bridge reported, not from the transcript —
  // a door with a token on file is a place a conversation can start.
  assert.match(kit, /doors\s*\n?\s*\.filter\(\(door\) => door\.id !== 'web' && door\.id !== 'cli'\)/);
});

test('the page opens on the conversation the web interface conducts', () => {
  // The reader's own line, labeled the way this screen names them — not the
  // newest conversation, which may be somebody else's.
  assert.match(
    link,
    /conversations\.find\(\(entry\) => entry\.id === `web:\$\{agentPerson\}`\) \?\? null/,
    'the page does not open on the web line',
  );
});

test('the names appear only where several people talk', () => {
  // A messaging app is one door and several people; the interfaces are one
  // conversation each. A list of one name is chrome around nothing.
  assert.match(screen, /isApp \? \(\s*<PersonList\b/, 'the names are not gated on the door');
  assert.match(kit, /conversations\.length === 0\) return null/, 'an empty list is drawn');
  assert.match(kit, /onClick=\{\(\) => onSelect\(conversation\.id\)\}/, 'a name does not open the conversation it names');
});

test('the pane narrows to the pair, not the door', () => {
  // The kernel owns what a conversation is, and the pane narrows by it. A pane
  // that filtered on `channel` would put two correspondents in one stream.
  assert.match(screen, /conversationKey\(message\) === conversation\.id/);
});

test('a door that cannot be written to says so at the composer', () => {
  // Read it, cannot write to it — the two remaining readings of an empty reply
  // box are "nothing has been said" and "you cannot speak there", and only one
  // of them is true.
  assert.match(screen, /closedChannels\.includes\(conversation\.channel\)/);
  assert.match(screen, /!conversation\.person/, 'a door with nobody on it is not a place to send');
});
