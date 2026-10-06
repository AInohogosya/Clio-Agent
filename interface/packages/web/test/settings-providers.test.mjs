import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createSettings, translate } from '@project-phone/core';
import { SettingsScreen } from '../src/screens/SettingsScreen.tsx';

/**
 * The provider list, offered the way a long list has to be.
 *
 * A quick pick for each of the vendors a person is most likely to reach for,
 * then a hundred more — and a hundred buttons on one screen is a screen nobody
 * reads. So the rest live behind a fold that names how many it is keeping, and
 * a search box answers the question the fold cannot: which of the hundred is
 * the one wanted. This file is about the two states a person can be in on the
 * way in — a provider among the quick picks, and one behind the fold — because
 * a fold that hid the provider in use would read as no provider chosen at all.
 */

const discover = async () => ({ models: [], source: 'offline' });
const NONE = { present: false, source: 'none', hint: '' };

function render({ provider = 'openai' } = {}) {
  return renderToStaticMarkup(createElement(SettingsScreen, {
    credential: NONE,
    discover,
    onClose: () => undefined,
    onUpdate: () => undefined,
    settings: createSettings({ interface: 'direct', provider }),
  }));
}

function foldButton(markup) {
  const button = markup.match(/<button[^>]*aria-expanded="[^"]*"[^>]*>[\s\S]*?<\/button>/);
  assert.ok(button, 'there is no fold on the provider list');
  return button[0];
}

function attribute(element, name) {
  const found = element.match(new RegExp(`${name}="([^"]*)"`));
  return found ? found[1] : null;
}

test('the quick picks are on the screen and the search box is above them', () => {
  const markup = render();
  assert.ok(markup.includes('OpenAI'), 'the default provider is not on the screen');
  assert.ok(markup.includes('Anthropic'), 'the quick picks are missing');
  assert.ok(markup.includes(translate('en', 'searchProviders')), 'there is no search box');
});

test('the fold names how many it keeps, and keeps them out of the markup', () => {
  const markup = render();
  const button = foldButton(markup);
  assert.equal(attribute(button, 'aria-expanded'), 'false');
  assert.ok(
    markup.includes(translate('en', 'moreProvidersShow', { count: 101 })),
    `the fold does not say ${101} more`,
  );
  // A provider from behind the fold: absent while it is folded, which is the
  // point of a fold.
  assert.ok(!markup.includes('Together AI'), 'the folded providers are painted anyway');
});

test('the fold opens by itself when the provider in use lives behind it', () => {
  const markup = render({ provider: 'together' });
  const button = foldButton(markup);
  assert.equal(attribute(button, 'aria-expanded'), 'true');
  assert.ok(markup.includes('Together AI'), 'the provider in use is not on the screen');
  assert.ok(markup.includes(translate('en', 'moreProvidersHide')), 'an open fold does not say so');
  // An open fold is the whole list, first to last.
  assert.ok(markup.includes('Telnyx AI'), 'the last provider is not behind the fold');
});
