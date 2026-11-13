import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { translate } from '@project-phone/core';
import App from '../src/App.tsx';

/**
 * First launch comes up on the provider and LLM settings, not the chat.
 *
 * There is no browser here, so this is a static render of the very first
 * frame: before the bridge answers, the surface holds defaults rather
 * than the configured file, which is exactly the not-yet-configured
 * state. What has to be true of that frame is that the settings screen
 * is already on screen — closing it hands the chat over afterwards,
 * which is the `onClose` the App passes it.
 */

test('the first frame is the settings screen', () => {
  const markup = renderToStaticMarkup(createElement(App));
  assert.ok(markup.includes(translate('en', 'settings')), 'the settings heading is missing');
  // The screen is a sidebar of categories now, and the first frame opens on the
  // interface choice: agent or direct, which is the one decision a first run has
  // to make before any provider field means anything.
  assert.ok(markup.includes(translate('en', 'interfaceLabel')), 'the interface choice is missing');
  assert.ok(markup.includes(translate('en', 'catConnection')), 'the categories are missing');
});

test('the first frame is not the chat', () => {
  const markup = renderToStaticMarkup(createElement(App));
  assert.ok(!markup.includes(translate('en', 'welcomeTitle')), 'the chat welcome is on the first frame');
  assert.ok(!markup.includes(translate('en', 'liveChat')), 'the chat header is on the first frame');
});
