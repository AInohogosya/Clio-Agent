import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

/**
 * Who owns the base model in agent mode.
 *
 * In agent mode the base model lives in the agent's own home — its own file, its
 * own home directory — and the shared settings file keeps a *copy* of it so the
 * terminal and the browser can show the same thing without asking anybody. A
 * copy drifts: the agent's base model can be changed where the file knows
 * nothing about it.
 *
 * So the copy is a copy, and a copy is never allowed to become the original.
 * That is a rule about two files and it cannot be checked by reading one of
 * them, and there is no browser here to open the page with: what is checked is
 * the wiring — that the draft is seeded from the agent, that a change made
 * elsewhere does not overwrite the draft's base model, and that the agent's own
 * file is written only when a base-model field was actually edited.
 */

const screen = readFileSync(new URL('../src/screens/SettingsScreen.tsx', import.meta.url), 'utf8');

/** The text between two markers, which is how a block is found in a screen. */
function between(start, end) {
  const from = screen.indexOf(start);
  assert.notEqual(from, -1, `nothing on the screen starts with: ${start}`);
  const rest = screen.slice(from);
  const to = typeof end === 'string' ? rest.indexOf(end) : rest.search(end);
  assert.notEqual(to, -1, `nothing on the screen ends with: ${end}`);
  return rest.slice(0, to);
}

/** The end of a hook's body: its dependency list, which closes at the top level. */
const HOOK_END = /\n {2}\}, \[/;

test('the draft starts from the agent, and without the shared file\'s key', () => {
  // The key is the one field the shared file holds and the agent's file does
  // not: the agent's key is never read back over the link. A draft that starts
  // with the shared file's key in it is a draft that can write another
  // vendor's credential into the agent's own file on the next save.
  assert.match(
    screen,
    /settings\.interface === 'agent' \? \{ \.\.\.settings, apiKey: '' \} : \{ \.\.\.settings \}/,
    'the draft starts with the shared file\'s key in agent mode',
  );
  // And the agent's own answer is what fills the rest of it.
  const seeding = between('// Ask the agent what it is using, and let that answer the form.', HOOK_END);
  assert.match(seeding, /provider,\n\s+baseUrl: found\.baseUrl/, 'the draft is not seeded from the agent');
  assert.match(seeding, /model: found\.model/, 'the model is not the agent\'s');
  assert.match(seeding, /apiKey: '',/, 'the seeding carries a key');
});

test('a shared file that has drifted does not overwrite the draft', () => {
  // The effect that adopts a change made elsewhere takes the interface, the
  // agent's address, the language and the appearance — and in agent mode
  // nothing else. The provider, the endpoint, the model and the key are the
  // agent's, and the copy in the shared file may name a vendor the agent
  // stopped using a week ago.
  const adopting = between('if (dirty) return;', HOOK_END);
  assert.match(adopting, /if \(!agentMode\) return \{ \.\.\.settings \};/, 'direct mode does not adopt the settings');
  assert.match(adopting, /\.\.\.current,/, 'the draft is replaced rather than kept');
  for (const field of ['provider', 'model', 'baseUrl', 'apiKey']) {
    assert.doesNotMatch(
      adopting,
      new RegExp(`${field}: settings\\.`),
      `the shared file's ${field} is allowed to overwrite the draft`,
    );
  }
});

test('the agent\'s file is written only when a base-model field was edited', () => {
  // A save that changed the language, the theme or the agent's name says
  // nothing about which model the agent should think with. Writing one anyway
  // would take the provider, the endpoint and the model from a draft — which,
  // in agent mode, is a copy of the agent's base model that may have drifted
  // while the page was open. That is how a base model somebody chose by hand
  // comes back as another vendor's model, some time after the change that was
  // asked for.
  const writing = between('const writeBaseModel = useCallback(async () => {', HOOK_END);
  assert.match(writing, /if \(!baseTouched\) return null;/, 'a base model is written without a base-model edit');
  assert.doesNotMatch(writing, /dirty/, 'the write is tied to any dirty save');
  // And the edit is recorded by the one updater that patches the base model.
  assert.match(screen, /const patchBase = \(values: Partial<Settings>\) => \{\n\s+patch\(values\);\n\s+if \(agentMode\) setBaseTouched\(true\);/, 'an edit of the base model is not recorded');
});

test('a save that never reached the base model writes nothing to the agent', () => {
  // The write is asked for by the Save button, and the answer is what says
  // "saved". A save that changed nothing about the base model must not claim
  // one, and must not make one either.
  assert.match(screen, /if \(await writeBaseModel\(\)\) said = t\('agentBaseModelSaved'\);/, 'the save does not ask the agent');
  // Closing with a base-model edit pending discards it rather than writing half
  // a save: persisting the shared file while the agent's file still holds the
  // old vendor is the split that reads as a revert on the next open.
  const closing = between('const close = () => {', 'return (');
  assert.match(closing, /!\(agentMode && baseTouched\)/, 'a pending base-model edit is written on close');
});
