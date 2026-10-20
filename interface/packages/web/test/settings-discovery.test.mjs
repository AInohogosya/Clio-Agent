import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

/**
 * How a person gets a catalogue of models.
 *
 * The list used to arrive on exactly one condition: somebody pressed the button
 * beside it. So a person who opened settings, pasted a key, and tabbed on to the
 * model list was looking at one model — the one that was already written down —
 * and had no way to tell that from a provider with nothing to offer until they
 * found the button and pressed it. The field they had just finished with was the
 * answer sitting right there in the form, and the page had not used it.
 *
 * Leaving the field is what a finished key looks like, so that is what fetches
 * now. Which leaves the interesting half: a page that fetches without being told
 * to has to be the kind that does not fetch carelessly. A request per visit, for
 * a credential already asked about, replaces a working list with a fresh failure
 * — and a request with nothing in the field at all can only answer with a
 * complaint about credentials, written onto a form nobody has submitted.
 *
 * So the rule is not "fetch on blur" and it is not left in the screen either: it
 * is `shouldProbeCatalog` and `probeSignature`, in core, where the terminal's own
 * rules live and where they are tested as rules. What is checked here is the
 * wiring — that the field, the rule and the button are all still attached to each
 * other, which no browser is available to check and no amount of reading
 * establishes.
 */

const screen = readFileSync(new URL('../src/screens/SettingsScreen.tsx', import.meta.url), 'utf8');

/** The body of a `const name = ...` declaration, up to the next blank line. */
function handler(name) {
  const start = screen.indexOf(`const ${name}`);
  assert.notEqual(start, -1, `${name} is not on the settings screen`);
  return screen.slice(start, screen.indexOf('\n\n', start));
}

test('leaving the key field is what fetches the models', () => {
  // The key goes in a password field, and this is the only one of them that is
  // about a provider's key rather than about a door.
  const field = screen.match(/<Input[\s\S]{0,400}?type="password"[\s\S]{0,80}?\/>/);
  assert.ok(field, 'there is no key field on the page');
  assert.match(field[0], /onBlur=\{onCommitApiKey\}/, 'the key field asks for nothing when it is left');
  // And it is the screen's own handler, not something the field does itself: the
  // field holds no rules, and a rule written on an input is a rule with no test.
  assert.match(screen, /onCommitApiKey=\{discoverOnKeyBlur\}/, 'the field is not wired to anything');
  // Typing is not leaving. A key is fired at on every character, and no character
  // is a credential anybody could spend.
  assert.doesNotMatch(screen, /onChange=\{\(event\) => discoverOnKeyBlur\(\)\}/, 'a keystroke fetches a catalogue');
});

test('an unanswered credential is fetched, and the same one twice is not', () => {
  const body = handler('discoverOnKeyBlur');
  assert.match(body, /void runDiscovery\(draft, envActive\)/, 'leaving the field does not fetch anything');
  // The guard is the whole reason this can be automatic. Compared against the
  // credential the last run *asked with*, rather than against a clock: tabbing
  // past the field, or pressing into it out of curiosity, would otherwise spend a
  // request per visit and could put a fresh failure where a good list already was.
  assert.match(
    body,
    /probedWith\.current === probeSignature\(draft, envActive\)/,
    'the same credential is asked about again',
  );
});

test('a field with nothing to spend is left alone', () => {
  // The rule is in core and is tested there. What matters here is that the screen
  // asks it rather than carrying a second version of the same decision.
  assert.match(
    handler('discoverOnKeyBlur'),
    /shouldProbeCatalog\(draft, \{ useEnv: envActive, keyOnFile: savedKey\.present \}\)/,
    'the screen fetches without consulting the rule',
  );
  assert.doesNotMatch(
    screen,
    /if \(!draft\.apiKey\.trim\(\)\)/,
    'the screen decides on its own whether a credential exists',
  );
});

test('every run records the credential it was made with', () => {
  // Recorded before the first await, so a second run started while the first is
  // in flight compares against this credential rather than the one before it —
  // and so the button, which records the same way, is a retry rather than a
  // different question.
  const run = handler('runDiscovery');
  assert.match(run, /probedWith\.current = probeSignature\(candidate, useEnv\)/, 'a run forgets what it asked with');
  assert.ok(
    run.indexOf('probedWith.current') < run.indexOf('await discover('),
    'the credential is recorded after the request is already out',
  );
});

test('the button is still there, and still asks regardless', () => {
  // It is kept on purpose. Asking is a thing a person may want to do: to repeat a
  // probe whose answer has gone stale, to re-read a provider after a key was
  // rotated somewhere this page cannot see, or to fetch at all on an install
  // where there is no key in the field to fetch it with. A button that looks
  // redundant gets deleted by whoever reads the code next, and then there is no
  // retry at all.
  assert.match(screen, /<Button disabled=\{discovering\} icon="search" onClick=\{onDiscover\}/, 'the button is gone');
  assert.match(screen, /onDiscover=\{\(\) => void runDiscovery\(draft, envActive\)\}/, 'the button asks nobody');
  // Straight to the run, with nothing in between: the point of pressing it is
  // that it repeats the question rather than skipping it as already asked.
  assert.doesNotMatch(handler('discoverOnKeyBlur'), /onDiscover/, 'the button now goes through the guard');
});