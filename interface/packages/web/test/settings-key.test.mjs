import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createSettings, translate } from '@project-phone/core';
import { SettingsScreen } from '../src/screens/SettingsScreen.tsx';

/**
 * The key field, on a screen that cannot read the key.
 *
 * A page is handed a hint and never the value, so the field is empty by
 * construction and leaving it empty means *keep what is on file*. That is the
 * right design and it has one failure mode, which is the whole subject of this
 * file: an empty field over a configured model reads as *nothing is set*. After a
 * restart that is a false all-clear about the only thing the reader opened the
 * screen to check, and it is the state that loses — because the configuration
 * being reported as unapplied is one that is working.
 *
 * So the answer is not to hand the key over. It is to say where the key is: the
 * mask in the field's own placeholder, and a line under it. Rendered here rather
 * than asserted on the rule alone, because the rule could be right and the screen
 * could still not be showing it — which is what it was doing.
 */

const HINT = '••••6a94';
const PRESENT = { present: true, source: 'file', hint: HINT };
const NONE = { present: false, source: 'none', hint: '' };

const discover = async () => ({ models: [], source: 'offline' });
const screen = readFileSync(new URL('../src/screens/SettingsScreen.tsx', import.meta.url), 'utf8');

function render({ credential = NONE, language = 'en' } = {}) {
  return renderToStaticMarkup(createElement(SettingsScreen, {
    credential,
    discover,
    onClose: () => undefined,
    onUpdate: () => undefined,
    settings: createSettings({ interface: 'direct', language }),
  }));
}

/** The password field the key goes in, as the markup wrote it. */
function keyField(markup) {
  const field = markup.match(/<input[^>]*type="password"[^>]*>/);
  assert.ok(field, 'there is no key field on the page');
  return field[0];
}

function attribute(element, name) {
  const found = element.match(new RegExp(`${name}="([^"]*)"`));
  return found ? found[1] : null;
}

test('a saved key is visible in the field, as a placeholder and not as a value', () => {
  const field = keyField(render({ credential: PRESENT }));
  // In the placeholder, so it describes the saved key and stays out of every save.
  assert.equal(attribute(field, 'placeholder'), HINT);
  // And emphatically not the value: a mask as a value would be posted to the
  // agent as though somebody had typed it.
  assert.equal(attribute(field, 'value'), '');
  // The field's own text is the empty string, so nothing here is one save away
  // from becoming the credential.
  assert.doesNotMatch(field, new RegExp(`value="${HINT.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`));
});

test('the line under the field says the key is saved', () => {
  assert.ok(
    render({ credential: PRESENT }).includes(translate('en', 'apiKeyOnFile', { hint: HINT })),
    'nothing says where the saved key is',
  );
});

test('nothing saved puts the provider back in the placeholder', () => {
  const field = keyField(render({ credential: NONE }));
  assert.equal(attribute(field, 'placeholder'), translate('en', 'apiKeyPlaceholder', { provider: 'OpenAI' }));
  assert.ok(!render({ credential: NONE }).includes('apiKeyOnFile'));
});

test("the shared file's key is described but not removable from the page", () => {
  // `phone config unset apiKey` is the deliberate way to remove it, because a
  // redacted snapshot also sends an empty key and a page that could clear it by
  // omission would clear it by being opened. Offering the button would make that
  // true the moment somebody typed into any other field on the form.
  const markup = render({ credential: PRESENT });
  assert.ok(markup.includes(translate('en', 'apiKeyOnFile', { hint: HINT })), 'the key is not described at all');
  assert.ok(!markup.includes(translate('en', 'apiKeyForget')), 'the shared key offers to be removed here');
});

test('the mask follows the language the screen is in', () => {
  for (const language of ['en', 'ja', 'zh']) {
    assert.ok(
      render({ credential: PRESENT, language }).includes(translate(language, 'apiKeyOnFile', { hint: HINT })),
      `${language}: the saved key is not described`,
    );
  }
});

test("the agent's own key is the one the page is wired to remove", () => {
  // `base` arrives from an effect, so the button is not reachable from a first
  // render. What is checked is the wiring that puts it there: the section is given
  // a Remove only when `savedApiKey` says this page owns the write, which for the
  // agent's file it does.
  assert.match(
    screen,
    /onForgetKey=\{savedKey\.removable \? \(\) => void forgetBaseModelKey\(\) : null\}/,
    'the Remove button is not tied to who owns the write',
  );
  assert.match(screen, /clearKey: true/, 'forgetting does not say that it is a removal');
});

test('a removal takes the key back off the model that is on file', () => {
  // The values come from the base model rather than the draft, because a person
  // removes a key from a form that may be half-finished and a draft with a model
  // name typed into it is refused by the agent. A rule about the model standing in
  // the way of forgetting a key is a rule about the wrong field.
  const block = screen.slice(screen.indexOf('const forgetBaseModelKey'));
  assert.match(block, /provider: base\.provider/);
  assert.match(block, /model: base\.model/);
  assert.match(block, /baseUrl: base\.baseUrl/);
});