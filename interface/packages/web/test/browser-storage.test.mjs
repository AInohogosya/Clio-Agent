import { strict as assert } from 'node:assert';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

/**
 * The page keeps nothing in the browser.
 *
 * A browser-owned store is the one failure this design cannot absorb: it belongs
 * to a hostname rather than to a person, so settings reappear in a session the
 * user did not mean to resume and a transcript is missing from every other
 * browser they open. The shared configuration file has neither problem, so
 * nothing in the page may reach for the alternative. The rule is worth a test
 * because breaking it takes one line, and the symptom — two windows that
 * quietly disagree about the conversation — shows up nowhere else.
 */

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE_ROOT = join(WEB_ROOT, 'src');

/**
 * The stores a browser hands out for free, plus the caches that outlive a tab
 * in the same way. `window.name` and the Cache API are here because both have
 * been used to carry state past a check like this one.
 */
const BROWSER_STORES = [
  ['localStorage', /\blocalStorage\b/],
  ['sessionStorage', /\bsessionStorage\b/],
  ['IndexedDB', /\bindexedDB\b/],
  ['Cache Storage', /\bcaches\s*\./],
  ['cookies', /\bdocument\s*\.\s*cookie\b/],
  ['window.name', /\bwindow\s*\.\s*name\b/],
  ['a file picker', /\bshowSaveFilePicker\b/],
];

function sourceFiles(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) found.push(...sourceFiles(path));
    else if (['.ts', '.tsx'].includes(extname(path))) found.push(path);
  }
  return found;
}

test('no page source reaches for a store the browser owns', () => {
  const files = [...sourceFiles(SOURCE_ROOT), join(WEB_ROOT, 'index.html')];
  assert.ok(files.length > 1, 'no page sources were found to check');
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    for (const [store, pattern] of BROWSER_STORES) {
      assert.doesNotMatch(source, pattern, `${file.slice(WEB_ROOT.length + 1)} uses ${store}`);
    }
  }
});

test('durable state reaches the page through the bridge and nothing else', () => {
  // The corollary of the rule above, stated positively: the one place the page
  // is given a stored state is the answer to `GET /__phone/session`. A page
  // that loaded its state any other way would be keeping a second copy.
  const protocol = readFileSync(join(SOURCE_ROOT, 'bridge-protocol.ts'), 'utf8');
  const bridge = readFileSync(join(SOURCE_ROOT, 'bridge.ts'), 'utf8');
  assert.equal(protocol.includes("session: 'session'"), true);
  assert.equal(bridge.includes("bridgePath('session')"), true);
  assert.doesNotMatch(bridge, /readFile|require\(|import\(/);
});
