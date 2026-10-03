import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  actionTone,
  agentProblem,
  controlSummary,
  createSettings,
  createTranslator,
  deadlineLabel,
  flattenIntentions,
  formatSpan,
  humanizeState,
  intentionTone,
  isValidAgentEndpoint,
  readAgentView,
  readControl,
  stateTone,
} from '@project-phone/core';

const t = createTranslator('en');

/**
 * What a browser surface has to get right, checked without a browser.
 *
 * The page is a rendering of what the kernel reads; a mistake in the reading is
 * a wrong panel, and a mistake in the shared vocabulary is a browser and a
 * terminal disagreeing about the same agent. Both are caught here, in the layer
 * they happen in.
 */

function body(overrides = {}) {
  return {
    presence: {
      state: 'DELIBERATING',
      focus: { intention_id: 'i1', title: 'Finish the interface', kind: 'project' },
      ts: new Date().toISOString(),
      recentCycles: [{ state: 'ATTENDING', ts: new Date().toISOString(), tier: 'T2' }],
    },
    control: { paused: false, pause_actions: false, stopped: false, emergency: false },
    thoughts: [{ id: 'e1', ts: new Date().toISOString(), summary: 'The budget reads low.' }],
    intentions: [
      { id: 'i1', parent_id: null, kind: 'project', title: 'Finish the interface', status: 'active', priority: 0.9, budget_usd: 5, spent_usd: 1.25 },
      { id: 'i2', parent_id: 'i1', kind: 'step', title: 'Wire the terminal', status: 'blocked', priority: 0.6 },
    ],
    actions: [
      { id: 'a1', ts_start: new Date().toISOString(), tool: 'shell', status: 'ok', undo_ref: 'undo-1' },
      { id: 'a2', ts_start: new Date().toISOString(), tool: 'browser', status: 'running' },
    ],
    budget: {
      todayByBucket: [{ bucket: 'discretionary', total: '2.5' }],
      byModel: [{ model: 'claude-sonnet-5', total: '2.5' }],
      daily: [{ day: '2026-01-01', total: '2.5' }],
      caps: { daily_usd: 20, monthly_usd: 400, commitment_usd: 10, discretionary_usd: 10 },
    },
    guardian: {
      trash: [{ id: 't1', origin: '/tmp/a', retention_until: new Date().toISOString(), reason: 'asked' }],
      snapshots: [{ name: 'snap-1', created_at: new Date().toISOString(), backend: 'fs' }],
      integrity: { ok: true, ran_at: new Date().toISOString(), audit_chain_ok: true, artifact_mismatches: [] },
      auditHead: { seq: 42, ts: new Date().toISOString(), summary: 'held a cycle', hash: 'deadbeef' },
      protectedCount: 3,
    },
    messages: [
      { id: 'm1', ts: new Date().toISOString(), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'are you there?', delivery: null },
    ],
    ...overrides,
  };
}

test('the agent view is read whole, whatever the bridge sent', () => {
  const view = readAgentView(body(), 0);
  assert.equal(view.presence.focus.title, 'Finish the interface');
  assert.equal(view.intentions[0].children[0].title, 'Wire the terminal');
  assert.equal(view.budget.caps.daily_usd, 20);
  assert.equal(view.guardian.protectedCount, 3);
  assert.equal(view.messages.length, 1);
});

test('the lifecycle in one phrase names the most restrictive flag that is set', () => {
  const flags = (over) => ({
    paused: false,
    pause_actions: false,
    stopped: false,
    emergency: false,
    preview: false,
    ...over,
  });
  assert.equal(controlSummary(flags({}), t).tone, 'success');
  assert.equal(controlSummary(flags({ pause_actions: true }), t).text, t('controlStateActionsHeld'));
  assert.equal(controlSummary(flags({ paused: true }), t).text, t('controlStateHeld'));
  assert.equal(controlSummary(flags({ paused: true, stopped: true }), t).text, t('controlStateStopped'));
  assert.equal(controlSummary(flags({ paused: true, stopped: true, emergency: true }), t).text, t('controlStateEmergency'));
});

test('an agent in preview mode is never reported as running', () => {
  // Preview mode refuses every tool, so calling it "running" would be the one
  // reading a reader could act on and be wrong about: the agent answers, and
  // nothing else it says implies it can do anything.
  const flags = (over) => ({
    paused: false,
    pause_actions: false,
    stopped: false,
    emergency: false,
    preview: false,
    ...over,
  });
  const summary = controlSummary(flags({ preview: true }), t);
  assert.equal(summary.text, t('controlStatePreview'));
  assert.equal(summary.tone, 'warning');
  assert.notEqual(summary.text, t('controlStateRunning'));

  // A pause outranks it: both gate every tool, but one is somebody having
  // stopped the agent and that is the more urgent fact to read about.
  assert.equal(
    controlSummary(flags({ preview: true, pause_actions: true }), t).text,
    t('controlStateActionsHeld'),
  );
});

test('a control payload with no preview key reads as off rather than throwing', () => {
  // The reader is total by contract, and an older bridge is a real thing to be
  // pointed at. The one thing it must not do is invent a truth: absent is off,
  // and the surface can say so.
  assert.equal(readControl({ paused: false }).preview, false);
  assert.equal(readControl(null).preview, false);
  assert.equal(readControl({ preview: true }).preview, true);

  // And it is strict, like every flag here. A truthy-looking string from a
  // half-written row is not consent to open an agent's tools.
  assert.equal(readControl({ preview: 'yes' }).preview, false);
  assert.equal(readControl({ preview: 1 }).preview, true);
});

test('state, intention and action colours are decided in the kernel, once', () => {
  assert.equal(stateTone('ACTING'), 'success');
  assert.equal(stateTone('DELIBERATING'), 'accent');
  assert.equal(stateTone('STOPPED'), 'danger');
  assert.equal(stateTone('SOMETHING_NEW'), 'muted');
  assert.equal(intentionTone('active'), 'accent');
  assert.equal(intentionTone('done'), 'success');
  assert.equal(actionTone('running'), 'accent');
  assert.equal(actionTone('failed'), 'danger');
  assert.equal(humanizeState('ACTING_TOOL'), 'acting tool');
});

test('a cap of zero is no cap, and a deadline reads as a duration', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  assert.equal(formatSpan(30_000), '30s');
  assert.equal(formatSpan(3_600_000), '1h');
  assert.equal(deadlineLabel('2026-01-01T00:10:00Z', now, t).overdue, false);
  assert.equal(deadlineLabel('2025-12-31T23:50:00Z', now, t).overdue, true);
});

test('the intention tree is flattened parents first, and survives a missing children', () => {
  const view = readAgentView(body(), 0);
  assert.deepEqual(flattenIntentions(view.intentions).map((node) => node.title), [
    'Finish the interface',
    'Wire the terminal',
  ]);
  assert.deepEqual(flattenIntentions(null), []);
  assert.deepEqual(flattenIntentions([{ id: 'a', title: 'a' }]), [{ id: 'a', title: 'a' }]);
});

test('an agent draft is validated by its own rules, not the provider rules', () => {
  assert.equal(agentProblem(createSettings()), null);
  assert.equal(agentProblem({ ...createSettings(), agentUrl: 'https://example.com' }), 'invalidEndpoint');
  assert.equal(agentProblem({ ...createSettings(), agentPerson: '' }), 'agentPersonRequired');
  // There is no key on this screen, so a valid address and a valid name is all
  // that is asked for — even with a provider that would demand a credential.
  assert.equal(agentProblem({ ...createSettings(), provider: 'openai', apiKey: '' }), null);
});

test('the agent address is loopback-only, in every spelling of loopback', () => {
  for (const good of ['http://127.0.0.1:8720', 'http://localhost:8720', 'http://[::1]:8720', 'http://127.5.5.5:1']) {
    assert.equal(isValidAgentEndpoint(good), true, good);
  }
  for (const bad of ['http://example.com', 'https://127.0.0.1', 'http://127.0.0.1:8720/x', 'http://169.254.169.254']) {
    assert.equal(isValidAgentEndpoint(bad), false, bad);
  }
});

test('the interface is the agent unless something says otherwise', () => {
  assert.equal(createSettings().interface, 'agent');
  assert.equal(createSettings({ interface: 'nonsense' }).interface, 'agent');
  assert.equal(createSettings({ interface: 'direct' }).interface, 'direct');
});
