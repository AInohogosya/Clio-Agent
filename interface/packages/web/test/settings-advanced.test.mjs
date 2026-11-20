import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createSettings, translate } from '@project-phone/core';
import { SettingsScreen } from '../src/screens/SettingsScreen.tsx';

/**
 * The tuning categories — token budget, deliberation, solver, rhythm, memory,
 * permissions — are each a wall of knobs, so both settings screens keep them
 * behind an "Advanced settings" fold in the sidebar instead of listing them
 * beside the everyday categories. This file is about the states a person can
 * be in on the way in: the fold closed in agent mode, and the whole group
 * absent in direct mode, where the agent (and its tuning) is not in the
 * picture at all.
 */

const discover = async () => ({ models: [], source: 'offline' });
const NONE = { present: false, source: 'none', hint: '' };

function render({ interface: iface = 'agent' } = {}) {
  return renderToStaticMarkup(createElement(SettingsScreen, {
    credential: NONE,
    discover,
    onClose: () => undefined,
    onUpdate: () => undefined,
    settings: createSettings({ interface: iface }),
  }));
}

test('the everyday categories are on the screen and the tuning ones are folded away', () => {
  const markup = render();
  for (const key of ['catGeneral', 'catConnection', 'catMessaging', 'catAppearance']) {
    assert.ok(markup.includes(translate('en', key)), `${key} is missing from the sidebar`);
  }
  const fold = markup.match(/<button[^>]*aria-expanded="false"[^>]*>[\s\S]*?<\/button>/);
  assert.ok(fold, 'there is no advanced fold in the sidebar');
  assert.ok(fold[0].includes(translate('en', 'catAdvanced')), 'the fold is not labelled as the advanced settings');
  assert.ok(!markup.includes(translate('en', 'catBudget')), 'a tuning category is painted while the fold is closed');
  assert.ok(!markup.includes(translate('en', 'catSolver')), 'a tuning category is painted while the fold is closed');
});

test('direct mode offers no advanced section at all', () => {
  const markup = render({ interface: 'direct' });
  assert.ok(!markup.includes(translate('en', 'catAdvanced')), 'the advanced fold is offered in direct mode');
  assert.ok(!markup.includes(translate('en', 'catBudget')), 'a tuning category is on the screen in direct mode');
  assert.ok(markup.includes(translate('en', 'catGeneral')), 'the everyday categories are missing');
});
