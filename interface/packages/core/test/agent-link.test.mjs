import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  AGENT_CONTROL_ACTIONS,
  AgentClient,
  AgentRequestError,
  bindAgentConversation,
  channelLabel,
  conversationsIn,
  conversationKey,
  createAgentTransport,
  createSettings,
  describeAgentFailure,
  DuplexClient,
  emptyAgentView,
  isValidAgentEndpoint,
  normalizeMessages,
  readAgentDoors,
  readAgentView,
  readIntentions,
  reconcileDoors,
  resolveAgentEndpoints,
  senderLabel,
  toChatMessages,
  toChatTimeline,
} from '../dist/index.js';

const NOW = 1_760_000_000_000;

function iso(offsetMs = 0) {
  return new Date(NOW + offsetMs).toISOString();
}

function snapshotBody(overrides = {}) {
  return {
    presence: {
      state: 'DELIBERATING',
      focus: { intention_id: 'i1', title: 'Finish the interface', kind: 'project' },
      ts: iso(),
      recentCycles: [{ state: 'ATTENDING', ts: iso(-1000), tier: 'T2' }],
    },
    control: { paused: false, pause_actions: false, stopped: false, emergency: false },
    thoughts: [{ id: 'e1', ts: iso(), summary: 'The budget reads low.' }],
    intentions: [
      { id: 'i1', parent_id: null, kind: 'project', title: 'Finish the interface', status: 'active', priority: 0.9, deadline: iso(86_400_000), budget_usd: 5, spent_usd: 1.25 },
      { id: 'i2', parent_id: 'i1', kind: 'step', title: 'Wire the terminal', status: 'active', priority: 0.6 },
    ],
    actions: [
      { id: 'a1', ts_start: iso(), tool: 'shell', status: 'ok', undo_ref: 'undo-1', reason: 'read a file' },
      { id: 'a2', ts_start: iso(), tool: 'browser', status: 'running' },
    ],
    budget: {
      todayByBucket: [{ bucket: 'commitment', total: '1.25' }, { bucket: 'discretionary', total: 0.75 }],
      byModel: [{ model: 'claude-sonnet-5', total: 2 }],
      daily: [{ day: '2026-01-01', total: 2 }],
      caps: { daily_usd: 20, monthly_usd: 400, commitment_usd: 10, discretionary_usd: 10 },
    },
    guardian: {
      trash: [{ id: 't1', origin: '/tmp/a', retention_until: iso(86_400_000), reason: 'asked' }],
      snapshots: [{ name: 'snap-1', created_at: iso(), backend: 'fs' }],
      integrity: { ok: true, ran_at: iso(), audit_chain_ok: true, artifact_mismatches: [] },
      auditHead: { seq: 42, ts: iso(), summary: 'held a cycle', hash: 'deadbeef' },
      protectedCount: 3,
    },
    messages: [
      { id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'are you there?', delivery: null },
      { id: 'm2', ts: iso(-4000), direction: 'outbound', channel: 'web', person_id: 'owner', text: 'yes.', delivery: { status: 'delivered', urgency: 'normal' } },
    ],
    ...overrides,
  };
}

/** A fetch double that answers the four routes the agent link uses. */
function fakeFetch(handler) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? 'GET', body: init.body });
    return handler(String(url), init, calls.length);
  };
  impl.calls = calls;
  return impl;
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** A socket stand-in that records what was sent and lets a test push frames. */
function fakeSocketFactory() {
  const sockets = [];
  const factory = (url) => {
    const socket = {
      url,
      sent: [],
      closed: false,
      onopen: null,
      onmessage: null,
      onerror: null,
      onclose: null,
      send(data) {
        socket.sent.push(data);
      },
      close() {
        socket.closed = true;
      },
      open() {
        socket.onopen?.({});
      },
      frame(value) {
        socket.onmessage?.({ data: JSON.stringify(value) });
      },
    };
    sockets.push(socket);
    return socket;
  };
  factory.sockets = sockets;
  return factory;
}

/** A channel that is driven entirely by the test, with no socket involved. */
function stubChannel() {
  const handlers = { onOpen: null, onFrame: null, onClose: null };
  return {
    kind: 'poll',
    url: 'ws://127.0.0.1:8720/events',
    opened: false,
    sent: [],
    open(next) {
      handlers.onOpen = next.onOpen;
      handlers.onFrame = next.onFrame;
      handlers.onClose = next.onClose;
      this.opened = true;
    },
    send(payload) {
      this.sent.push(payload);
      return true;
    },
    close() {
      this.opened = false;
    },
    push(frame) {
      handlers.onFrame?.(frame);
    },
    drop() {
      handlers.onClose?.();
    },
  };
}

function makeClient(overrides = {}) {
  const channel = overrides.channel ?? stubChannel();
  const client = new AgentClient({
    url: 'http://127.0.0.1:8720',
    now: () => NOW,
    channel,
    // Short enough that a test can assert a stall without waiting for the real
    // budget, which is deliberately generous.
    replyStallMs: 40,
    fetchImpl: overrides.fetchImpl ?? fakeFetch((url) => {
      if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
      if (url.endsWith('/api/message')) return jsonResponse({ message_id: 'm-sent' });
      if (url.endsWith('/api/control')) return jsonResponse({ ok: true });
      return jsonResponse({ queued: true });
    }),
    ...overrides,
  });
  return { client, channel };
}

// ------------------------------------------------------------------- reading

test('a snapshot is read whole, and every field is bounded', () => {
  const view = readAgentView(snapshotBody(), NOW);
  assert.equal(view.loaded, true);
  assert.equal(view.presence.state, 'DELIBERATING');
  assert.equal(view.presence.focus?.title, 'Finish the interface');
  assert.equal(view.thoughts.length, 1);
  assert.equal(view.intentions.length, 1, 'one root, one child');
  assert.equal(view.intentions[0].children[0].title, 'Wire the terminal');
  assert.equal(view.actions.length, 2);
  assert.equal(view.budget.caps.daily_usd, 20);
  assert.equal(view.budget.todayByBucket[0].total, 1.25, 'a NUMERIC string is a number');
  assert.equal(view.guardian.integrity.ok, true);
  assert.equal(view.guardian.auditHead.seq, 42);
  assert.equal(view.messages.length, 2);
  assert.equal(view.messages[0].delivery_status, null);
  assert.equal(view.messages[1].delivery_status, 'delivered');
});

test('an intention whose parent is not in the page is lifted, not dropped', () => {
  const roots = readIntentions([
    { id: 'child', parent_id: 'gone', title: 'orphan' },
    { id: 'loop-a', parent_id: 'loop-b', title: 'a' },
    { id: 'loop-b', parent_id: 'loop-a', title: 'b' },
  ]);
  const titles = roots.map((node) => node.title);
  assert.ok(titles.includes('orphan'));
  // The two that point at each other cannot be placed under one another, so
  // neither is lost and neither nests forever.
  assert.equal(titles.includes('a'), true);
  assert.equal(titles.includes('b'), true);
});

test('anything at all reads as an empty view instead of throwing', () => {
  for (const payload of [null, undefined, 7, 'nope', [], { presence: 'offline', budget: 3 }]) {
    const view = readAgentView(payload, NOW);
    assert.equal(view.presence.state, 'UNKNOWN');
    assert.deepEqual(view.thoughts, []);
    assert.deepEqual(view.intentions, []);
    assert.equal(view.loaded, true);
  }
  const idle = emptyAgentView(NOW);
  assert.equal(idle.loaded, false, 'an unread view is not an empty agent');
  assert.deepEqual(idle.messages, []);
});

test('a message with an unparseable timestamp still has a place in the timeline', () => {
  const view = readAgentView({
    messages: [
      { id: 'good', ts: iso(0), direction: 'outbound', text: 'first' },
      { id: 'bad', ts: 'not-a-time', direction: 'inbound', text: 'second' },
    ],
  }, NOW);
  assert.equal(view.messages.length, 2);
  assert.equal(view.messages[1].ts, 0);
});

test('the agent timeline becomes a transcript, with direction as the only change', () => {
  const view = readAgentView(snapshotBody(), NOW);
  const transcript = toChatMessages(view.messages);
  assert.deepEqual(transcript.map((message) => message.role), ['user', 'assistant']);
  assert.deepEqual(transcript.map((message) => message.id), ['m1', 'm2']);
  assert.equal(transcript[0].createdAt, Date.parse(iso(-5000)));
});

test('a turn keeps the door it came through', () => {
  const view = readAgentView(snapshotBody({
    messages: [
      { id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'telegram', text: 'hello' },
      { id: 'm2', ts: iso(-4000), direction: 'outbound', channel: 'telegram', text: 'yes.' },
      { id: 'm3', ts: iso(-3000), direction: 'inbound', channel: 'web', text: 'and here' },
    ],
  }), NOW);
  const transcript = toChatMessages(view.messages);
  assert.deepEqual(transcript.map((message) => message.channel), ['telegram', 'telegram', 'web']);
});

test('a turn the agent was sent is attributed to the person who sent it', () => {
  // The reason the agent records a sender's name. A transcript of four Telegram
  // messages that renders as four undifferentiated bubbles cannot answer "who said
  // the third one", and an agent that can be talked to by the public has to be
  // able to.
  const view = readAgentView(snapshotBody({
    messages: [
      {
        id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'telegram',
        text: 'hello', person_id: 'tg:819012345678',
        sender_name: 'Ada Lovelace', sender_username: 'ada',
      },
      { id: 'm2', ts: iso(-4000), direction: 'outbound', channel: 'telegram', text: 'yes.' },
      {
        id: 'm3', ts: iso(-3000), direction: 'inbound', channel: 'slack',
        text: 'and here', person_id: 'slack:C1',
      },
    ],
  }), NOW);
  const transcript = toChatMessages(view.messages);
  assert.equal(transcript[0].author, 'Ada Lovelace (@ada, tg:819012345678)');
  // Absent rather than invented: the agent's own turns have no author, and a Slack
  // message is an author the channel could not name unless the app holds
  // `users:read`. Rendering either as somebody would be a claim about the world
  // this program cannot back.
  assert.equal('author' in transcript[1], false);
  assert.equal('author' in transcript[2], false);
});

test('a turn keeps the address of the person it is with, on both halves', () => {
  // The address is what makes the two halves one conversation. Without it a reply
  // belongs to nobody, a surface cannot tell one correspondent's thread from
  // another's, and the only way to group a transcript is by the door — which is
  // how four people in one Telegram channel end up as four copies of the same
  // conversation.
  const view = readAgentView(snapshotBody({
    messages: [
      {
        id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'telegram',
        text: 'hello', person_id: 'tg:819012345678',
      },
      {
        id: 'm2', ts: iso(-4000), direction: 'outbound', channel: 'telegram',
        text: 'yes.', person_id: 'tg:819012345678',
      },
    ],
  }), NOW);
  const transcript = toChatMessages(view.messages);
  assert.deepEqual(transcript.map((message) => message.person), ['tg:819012345678', 'tg:819012345678']);
});

test('a conversation is a door and an address, which is the agent\'s own key', () => {
  // The same string the store writes into `conversation_key`, so the line a
  // surface draws and the row the agent will answer cannot be two different
  // threads. The same person on two doors is two conversations, because the
  // agent can reach one of them and not the other.
  assert.equal(conversationKey({ channel: 'telegram', person: 'tg:1' }), 'telegram:tg:1');
  assert.equal(conversationKey({ channel: 'web', person: 'owner' }), 'web:owner');
  // A turn with no door of its own is the local line, and a turn with no address
  // is still a conversation rather than nothing to show.
  assert.equal(conversationKey({ person: 'owner' }), 'web:owner');
  assert.equal(conversationKey({ channel: 'slack' }), 'slack:');
});

test('the people in a transcript are the conversations it holds', () => {
  // Most recently active first, because the list answers "who did I miss?" and a
  // reader who just came back does not want to hunt for the conversation that
  // moved while they were away.
  const view = readAgentView(snapshotBody({
    messages: [
      {
        id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'telegram',
        text: 'hello', person_id: 'tg:1', sender_name: 'Ada Lovelace',
      },
      { id: 'm2', ts: iso(-4500), direction: 'outbound', channel: 'telegram', text: 'yes.', person_id: 'tg:1' },
      {
        id: 'm3', ts: iso(-3000), direction: 'inbound', channel: 'telegram',
        text: 'me too', person_id: 'tg:2', sender_name: 'Grace Hopper',
      },
      { id: 'm4', ts: iso(-2000), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'and me' },
    ],
  }), NOW);
  const found = conversationsIn(toChatMessages(view.messages), 'owner');
  assert.deepEqual(found.map((entry) => entry.id), ['web:owner', 'telegram:tg:2', 'telegram:tg:1']);
  // The name the channel gave, and the door underneath it.
  assert.deepEqual(
    found.map((entry) => entry.label),
    ['owner', 'Grace Hopper (tg:2)', 'Ada Lovelace (tg:1)'],
  );
  // Both halves of Ada's exchange are counted in hers, and not in Grace's.
  assert.equal(found[2].messages, 2);
  assert.equal(found[1].messages, 1);
  assert.equal(found[2].channel, 'telegram');
});

test('a rename is a name the reader sees, and the address is what it is filed under', () => {
  // The newest name wins, so somebody who renames themselves in Telegram is
  // called what they call themselves — while the address underneath stays put,
  // because that is where the reply has to go.
  const found = conversationsIn([
    { id: 'm1', role: 'user', text: 'hi', createdAt: 1, channel: 'telegram', person: 'tg:1', author: 'ada' },
    { id: 'm2', role: 'user', text: 'again', createdAt: 2, channel: 'telegram', person: 'tg:1', author: 'Ada Lovelace (@ada, tg:1)' },
  ]);
  assert.equal(found[0].label, 'Ada Lovelace (@ada, tg:1)');
  assert.equal(found[0].person, 'tg:1');
});

test('the local line is a conversation before anything has been said in it', () => {
  // A browser that has never been typed into holds no `web` row at all. A list
  // built only from what has been said offers no way to say anything, which is a
  // reader who opens the page, finds nobody to talk to, and concludes the agent
  // is not there.
  const found = conversationsIn([], 'owner');
  assert.deepEqual(found.map((entry) => entry.id), ['web:owner']);
  assert.equal(found[0].messages, 0);
  // And it is offered once, not twice, once it has something in it.
  assert.equal(conversationsIn([
    { id: 'm1', role: 'user', text: 'hello', createdAt: 1, channel: 'web', person: 'owner' },
  ], 'owner').length, 1);
});

test('a conversation the door could not name is still shown, under the door', () => {
  // A row with no address is a message somebody wrote. Dropping it would be a
  // worse answer than showing it under the door it came in on, and a reader who
  // cannot see a message they know arrived cannot tell what went wrong.
  const found = conversationsIn([
    { id: 'm1', role: 'user', text: 'from a slack app with no scope', createdAt: 1, channel: 'slack' },
  ], 'owner');
  assert.deepEqual(found.map((entry) => entry.id), ['slack:', 'web:owner']);
  assert.equal(found[0].label, 'Slack');
});

test('a name and an address survive the shared file, or a restart loses the people', () => {
  // The transcript is read back out of untrusted storage on every load. A sender
  // dropped here is not a missing caption: the whole list of who the agent is
  // talking to is built from these, so a page that reloads would show one
  // conversation with everybody's messages in it.
  const [kept] = normalizeMessages([
    {
      id: 'm1',
      role: 'user',
      text: 'hello',
      createdAt: 1,
      channel: 'telegram',
      person: 'tg:819012345678',
      author: 'Ada Lovelace (@ada, tg:819012345678)',
    },
  ]);
  assert.equal(kept.person, 'tg:819012345678');
  assert.equal(kept.author, 'Ada Lovelace (@ada, tg:819012345678)');
  assert.equal(conversationsIn([kept], 'owner')[0].label, 'Ada Lovelace (@ada, tg:819012345678)');

  // An address with a line of a name in it is not an address, and a name with a
  // newline in it is not one line.
  const [refused] = normalizeMessages([
    { id: 'm2', role: 'user', text: 'hi', createdAt: 1, person: 'Ada\nLovelace', author: 'Ada\n  Lovelace' },
  ]);
  assert.equal('person' in refused, false);
  assert.equal(refused.author, 'Ada Lovelace');
});

test('the agent timeline and this kit say the same thing about who wrote', () => {
  // The same fallback chain the agent reads, duplicated on purpose: this runs in a
  // browser and a terminal where the agent's Python does not, and a transcript that
  // says `Ada Lovelace` next to a prompt that says `tg:819012345678` leaves the
  // reader guessing which of the two is the person.
  assert.equal(senderLabel({ person_id: 'tg:1', sender_name: 'Ada Lovelace', sender_username: 'ada' }),
    'Ada Lovelace (@ada, tg:1)');
  assert.equal(senderLabel({ person_id: 'wa:1', sender_name: 'Alice Smith', sender_username: null }),
    'Alice Smith (wa:1)');
  assert.equal(senderLabel({ person_id: 'slack:C1', sender_name: null, sender_username: null }),
    'slack:C1');
  // A handle that is also the name is shown once, not twice.
  assert.equal(senderLabel({ person_id: 'dc:1', sender_name: 'ada', sender_username: 'ada' }),
    'ada (dc:1)');
  // And nothing at all is `unknown`, never the string `undefined`.
  assert.equal(senderLabel({ person_id: null, sender_name: null, sender_username: null }),
    'unknown');
});

test('the doors on offer are the ones the transcript has, plus the local line', () => {
  const view = readAgentView(snapshotBody({
    messages: [
      { id: 'm1', ts: iso(-5000), direction: 'inbound', channel: 'telegram', text: 'hello' },
      { id: 'm2', ts: iso(-4000), direction: 'outbound', channel: 'telegram', text: 'yes.' },
    ],
  }), NOW);
  const timeline = toChatTimeline(view.messages);
  assert.deepEqual(timeline.channels, ['telegram', 'web']);
  assert.equal(timeline.messages.length, 2);
});

test('an empty transcript still offers the local line', () => {
  // The surface's own turns live there before the agent has answered anything,
  // so a filter that hid `web` would hide the question the reader just typed.
  assert.deepEqual(toChatTimeline([]).channels, ['web']);
});

test('a channel is named the way a reader would say it', () => {
  assert.equal(channelLabel('telegram'), 'Telegram');
  assert.equal(channelLabel('web'), 'Web');
  assert.equal(channelLabel('whatsapp'), 'WhatsApp');
  assert.equal(channelLabel('something-new'), 'something-new', 'a name this build does not know is shown, not replaced');
  assert.equal(channelLabel(''), '');
});

test('the doors the agent has open are read whole', () => {
  const doors = readAgentDoors({
    channels: [
      { id: 'web', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'owner' }] },
      {
        id: 'telegram',
        enabled: true,
        admits: true,
        contacts: [{ person: 'owner', address: 'tg:819012345678' }],
      },
    ],
  });
  assert.deepEqual(doors.map((door) => door.id), ['web', 'telegram']);
  assert.deepEqual(doors[1].contacts, [{ person: 'owner', address: 'tg:819012345678' }]);
});

test('a route that is missing leaves the local line standing', () => {
  // The local door is the one whose being open needs nothing configured, so it is
  // the honest floor rather than an empty list.
  for (const answer of [null, undefined, {}, { channels: 'nope' }, { channels: [null, 7] }]) {
    assert.deepEqual(readAgentDoors(answer).map((door) => door.id), ['web'], JSON.stringify(answer));
  }
});

test('a door that is on but admits nobody is not the same as a door that is off', () => {
  const doors = readAgentDoors({
    channels: [
      { id: 'telegram', enabled: true, admits: false, contacts: [] },
      { id: 'whatsapp', enabled: false, admits: false, contacts: [] },
    ],
  });
  const telegram = doors.find((door) => door.id === 'telegram');
  const whatsapp = doors.find((door) => door.id === 'whatsapp');
  assert.equal(telegram.enabled, true);
  assert.equal(telegram.admits, false);
  assert.equal(whatsapp.enabled, false);
});

test('a contact with no address is dropped rather than offered', () => {
  // A door listed with a contact that cannot be written to is a door a surface
  // will offer and then fail on, so the half-record is not a contact.
  const doors = readAgentDoors({
    channels: [{ id: 'telegram', enabled: true, admits: true, contacts: [{ person: 'owner' }, { address: 'tg:1' }, null] }],
  });
  assert.deepEqual(doors[0].contacts, []);
});

test('a configured door is offered before anybody has spoken on it', () => {
  // The reason the filter is not derived from the transcript: a Telegram
  // conversation that has not started yet has no Telegram in it, so deriving the
  // list that way makes the one door a browser could open the one it cannot offer.
  const doors = readAgentDoors({
    channels: [{ id: 'web', enabled: true, admits: true, contacts: [] }],
  });
  const { offered } = reconcileDoors(doors, ['web']);
  assert.deepEqual(offered.map((door) => door.id), ['web']);
  const withTelegram = reconcileDoors(
    [...doors, { id: 'telegram', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'tg:1' }] }],
    ['web'],
  );
  assert.deepEqual(withTelegram.offered.map((door) => door.id), ['telegram', 'web']);
});

test('a door that has closed keeps its history and is named, not offered', () => {
  const { offered, closed } = reconcileDoors(
    [{ id: 'web', enabled: true, admits: true, contacts: [] }],
    ['web', 'telegram', 'telegram'],
  );
  assert.deepEqual(offered.map((door) => door.id), ['web']);
  assert.deepEqual(closed, ['telegram'], 'one name per door, however many turns came through it');
});

test('the client asks the agent which doors it has', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    if (url.endsWith('/api/channels')) {
      return jsonResponse({
        channels: [
          { id: 'web', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'owner' }] },
          { id: 'telegram', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'tg:7' }] },
        ],
      });
    }
    return jsonResponse({ queued: true });
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  const doors = await client.doors();
  assert.deepEqual(doors.map((door) => door.id), ['web', 'telegram']);
  assert.equal(fetcher.calls.some((call) => call.url.endsWith('/api/channels')), true);
  client.dispose();
});

test('an agent that cannot say which doors it has is not an error', async () => {
  // An older bridge has no such route, and a bridge that is down has nothing to
  // say at all. Both are answered with the local line rather than a rejection,
  // because a filter that threw would take the composer down with it.
  for (const answer of [
    (url) => (url.endsWith('/api/channels') ? jsonResponse({}, 404) : jsonResponse(snapshotBody())),
    () => { throw new Error('network down'); },
  ]) {
    const fetcher = fakeFetch(answer);
    const { client } = makeClient({ fetchImpl: fetcher });
    await client.connect();
    assert.deepEqual((await client.doors()).map((door) => door.id), ['web']);
    client.dispose();
  }
});

test('a door that is on but empty is closed too, not merely unusable', () => {
  // Telegram with an empty allowlist answers nobody, so sending into it produces
  // a deliberation and a reply that is discarded at the last step. That has to be
  // sayable before the send, not discovered as silence afterwards.
  const { offered, closed } = reconcileDoors(
    [
      { id: 'web', enabled: true, admits: true, contacts: [] },
      { id: 'telegram', enabled: true, admits: false, contacts: [] },
    ],
    ['web', 'telegram'],
  );
  assert.deepEqual(offered.map((door) => door.id), ['web']);
  assert.deepEqual(closed, ['telegram']);
});

// ------------------------------------------------------------------ endpoints

test('the agent address must be this machine', () => {
  for (const good of ['http://127.0.0.1:8720', 'http://localhost:8720', 'http://[::1]:8720']) {
    assert.equal(isValidAgentEndpoint(good), true, good);
  }
  for (const bad of [
    'http://ethos.example.com:8720',
    'http://192.168.1.10:8720',
    'https://127.0.0.1:8720',
    'http://user:pw@127.0.0.1:8720',
    'http://127.0.0.1:8720/admin',
    'http://127.0.0.1:8720?x=1',
    'http://169.254.169.254',
    'file:///etc/passwd',
    '',
    null,
  ]) {
    assert.equal(isValidAgentEndpoint(bad), false, String(bad));
  }
});

test('a hostile agent address in a settings payload falls back to the local default', () => {
  const settings = createSettings({ agentUrl: 'http://evil.example.com', agentPerson: 'x'.repeat(200) });
  assert.equal(settings.agentUrl, 'http://127.0.0.1:8720');
  assert.equal(settings.agentPerson, 'owner');
});

test('endpoints are derived from the base, and the event stream follows the scheme', () => {
  assert.deepEqual(resolveAgentEndpoints('http://127.0.0.1:8720'), {
    http: 'http://127.0.0.1:8720',
    events: 'ws://127.0.0.1:8720/events',
  });
  assert.throws(() => resolveAgentEndpoints('not a url'), AgentRequestError);
});

// --------------------------------------------------------------------- client

test('connecting reads the agent and opens the stream', async () => {
  const { client, channel } = makeClient();
  assert.equal(await client.connect(), true);
  assert.equal(client.getStatus(), 'online');
  assert.equal(channel.opened, true);
  assert.equal(client.getView().presence.state, 'DELIBERATING');
  client.dispose();
});

test('an agent that is not running leaves the link offline and says so', async () => {
  const { client } = makeClient({
    fetchImpl: fakeFetch(() => { throw new Error('connect ECONNREFUSED'); }),
  });
  assert.equal(await client.connect(), false);
  assert.equal(client.getStatus(), 'offline');
  client.dispose();
});

test('one change in flight is one read, however many callers ask', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({});
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  const before = fetcher.calls.filter((call) => call.url.endsWith('/api/snapshot')).length;
  await Promise.all([client.refresh(), client.refresh(), client.refresh()]);
  const after = fetcher.calls.filter((call) => call.url.endsWith('/api/snapshot')).length;
  assert.equal(after - before, 1);
  client.dispose();
});

test('presence and a thought are answered from the event, the rest is re-read', async () => {
  const { client, channel } = makeClient();
  await client.connect();
  const fetcher = client;
  void fetcher;

  channel.push({
    type: 'event',
    kind: 'presence.update',
    payload: { state: 'ACTING', focus: { intention_id: 'i1', title: 'Run the suite', kind: 'project' } },
  });
  assert.equal(client.getView().presence.state, 'ACTING');
  assert.equal(client.getView().presence.focus?.title, 'Run the suite');

  channel.push({ type: 'event', kind: 'thought.new', payload: { episode_id: 'e9', summary: 'A new thought.' } });
  assert.equal(client.getView().thoughts[0].summary, 'A new thought.');

  // An action update carries no arguments, so the only honest answer is to go
  // and read the journal again.
  channel.push({ type: 'event', kind: 'action.update', payload: { thread_id: 't1' } });
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(client.getView().actions.length, 2);
  client.dispose();
});

test('a frame that cannot be read does not tear the stream down', async () => {
  const { client, channel } = makeClient();
  await client.connect();
  channel.push('not a frame at all');
  channel.push({ type: 'nonsense' });
  channel.push({ type: 'error', error: 'invalid JSON' });
  assert.equal(client.getStatus(), 'online');
  assert.equal(channel.opened, true);
  client.dispose();
});

test('a message event lands on the timeline and settles the wait for it', async () => {
  const { client, channel } = makeClient();
  await client.connect();
  const waiting = client.awaitReply(NOW - 1);
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm9', ts: iso(10), direction: 'outbound', person_id: 'owner', text: 'Yes.' },
  });
  const reply = await waiting;
  assert.equal(reply.text, 'Yes.');
  assert.equal(client.getView().messages.at(-1).id, 'm9');
  client.dispose();
});

// ------------------------------------------------------------------- commands

test('a question is queued over HTTP and returns the agent own id', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({ message_id: 'm-sent' });
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  assert.equal(await client.sendMessage('  hello  '), 'm-sent');
  const sent = fetcher.calls.find((call) => call.url.endsWith('/api/message'));
  assert.deepEqual(JSON.parse(sent.body), { text: 'hello', person_id: 'owner', channel: 'web' });
  client.dispose();
});

test('a message goes out of the door the surface named', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({ message_id: 'm-sent' });
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  client.setChannel('telegram');
  await client.sendMessage('same question, different door');
  const sent = fetcher.calls.filter((call) => call.url.endsWith('/api/message')).at(-1);
  assert.equal(JSON.parse(sent.body).channel, 'telegram');
  client.dispose();
});

test('a door that is not an identifier is refused, and web is used instead', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({ message_id: 'm-sent' });
  });
  const { client } = makeClient({ fetchImpl: fetcher, sendChannel: '../../etc/passwd' });
  await client.connect();
  assert.equal(client.getChannel(), 'web', 'a path is not a channel');
  client.dispose();
});

test('an empty question is refused before it reaches the agent', async () => {
  const fetcher = fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot') ? snapshotBody() : { message_id: 'x' }));
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  await assert.rejects(() => client.sendMessage('   \n  '), (error) => {
    assert.equal(error.code, 'agent_refused');
    return true;
  });
  assert.equal(fetcher.calls.some((call) => call.url.endsWith('/api/message')), false);
  client.dispose();
});

test('every lifecycle verb is accepted, and an unknown one is not', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({ ok: true });
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  for (const action of AGENT_CONTROL_ACTIONS) {
    await client.control(action);
    const sent = fetcher.calls.filter((call) => call.url.endsWith('/api/control'));
    assert.equal(JSON.parse(sent.at(-1).body).action, action);
  }
  await assert.rejects(() => client.control('self_destruct'), (error) => {
    assert.equal(error.code, 'agent_refused');
    return true;
  });
  client.dispose();
});

test('an undo and a cancelled intention are addressed by id', async () => {
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    return jsonResponse({ queued: true });
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  await client.undoAction('a1');
  await client.closeIntention('i2');
  const undo = fetcher.calls.find((call) => call.url.includes('/api/actions/a1/undo'));
  const close = fetcher.calls.find((call) => call.url.includes('/api/intentions/i2/close'));
  assert.ok(undo, 'the undo route was addressed');
  assert.ok(close, 'the intention route was addressed');
  await assert.rejects(() => client.undoAction('  '), AgentRequestError);
  client.dispose();
});

// --------------------------------------------------------------------- replies

test('a question with no answer writes nothing and names the reason', async () => {
  const { client } = makeClient();
  await client.connect();
  await assert.rejects(() => client.awaitReply(NOW + 10_000, undefined), (error) => {
    assert.equal(error.code, 'agent_timeout');
    return true;
  });
  client.dispose();
});

test('the previous turn answer cannot satisfy the next wait', async () => {
  const { client } = makeClient();
  await client.connect();
  // The reply from before the question went out is on the timeline already, with
  // a timestamp inside the clock slack.
  await assert.rejects(
    () => client.awaitReply(NOW - 1000, undefined, new Set(['m2'])),
    (error) => {
      assert.equal(error.code, 'agent_timeout');
      return true;
    },
  );
  client.dispose();
});

test('an interrupted wait is reported as an interruption', async () => {
  const { client } = makeClient();
  await client.connect();
  const controller = new AbortController();
  const waiting = client.awaitReply(NOW + 10_000, controller.signal);
  controller.abort();
  await assert.rejects(() => waiting, (error) => {
    assert.equal(error.code, 'agent_aborted');
    return true;
  });
  client.dispose();
});

test('a link that goes away does not leave a reader waiting for it', async () => {
  let healthy = true;
  const fetcher = fakeFetch((url) => {
    if (!healthy) throw new Error('ECONNRESET');
    return jsonResponse(url.endsWith('/api/snapshot') ? snapshotBody() : {});
  });
  const { client } = makeClient({ fetchImpl: fetcher });
  await client.connect();
  const waiting = client.awaitReply(NOW + 10_000);
  healthy = false;
  await client.refresh();
  await assert.rejects(() => waiting, (error) => {
    assert.equal(error.code, 'agent_unreachable');
    return true;
  });
  client.dispose();
});

// ------------------------------------------------------------------- transport

test('the agent answers a question through the shared turn lifecycle', async () => {
  let sent = false;
  const fetcher = fakeFetch((url) => {
    if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
    if (url.endsWith('/api/message')) {
      sent = true;
      return jsonResponse({ message_id: 'm-sent' });
    }
    return jsonResponse({ queued: true });
  });
  const { client: agent, channel } = makeClient({ fetchImpl: fetcher });
  await agent.connect();

  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  const pending = duplex.sendMessage('are you there?');
  // The agent has not answered yet, so the transcript holds the question alone.
  assert.deepEqual(duplex.getSnapshot().messages.map((message) => message.role), ['user']);
  assert.equal(duplex.getSnapshot().status, 'thinking');

  assert.equal(sent, true);
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm9', ts: iso(10), direction: 'outbound', person_id: 'owner', text: 'I am here.' },
  });

  const reply = await pending;
  assert.equal(reply?.text, 'I am here.');
  assert.equal(duplex.getSnapshot().messages.at(-1).role, 'assistant');
  assert.equal(duplex.getSnapshot().pending, false);
  agent.dispose();
  duplex.dispose();
});

test('a stopped agent is refused before the question is queued', async () => {
  const { client: agent } = makeClient({
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot')
      ? snapshotBody({ control: { stopped: true, emergency: false } })
      : { message_id: 'm-sent' })),
  });
  await agent.connect();
  const fetcher = agent;
  void fetcher;
  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  const reply = await duplex.sendMessage('hello?');
  assert.equal(reply, undefined);
  assert.deepEqual(duplex.getSnapshot().messages.map((message) => message.role), ['user']);
  assert.match(String(duplex.getSnapshot().lastError), /stopped/i);
  agent.dispose();
  duplex.dispose();
});

test('an agent model list comes from the agent, because it holds the key', async () => {
  // This link used to report that it had no catalogue, on the grounds that the
  // agent picks its own model per call. That was true of the routing catalogue
  // and wrong about the base model — the one a person configures and the agent
  // really does use — so the list is asked of the agent instead.
  const agentCalls = [];
  const agent = new AgentClient({
    url: 'http://127.0.0.1:8720',
    channel: null,
    fetchImpl: async (url, init = {}) => {
      agentCalls.push({ url: String(url), method: init.method ?? 'GET' });
      return jsonResponse({ models: ['a/1', 'b/2'], source: 'remote' });
    },
  });
  const transport = createAgentTransport({ resolve: () => agent });
  const found = await transport.discoverModels();
  assert.deepEqual(found.models, ['a/1', 'b/2']);
  assert.equal(found.source, 'remote');
  assert.equal(agentCalls.at(-1).url, 'http://127.0.0.1:8720/api/models');
  agent.dispose();
});

test('with no link there is nobody to ask for a catalogue', async () => {
  const transport = createAgentTransport({ resolve: () => null });
  const found = await transport.discoverModels();
  assert.deepEqual(found.models, []);
  // Not a guess: a form showing an invented list would be showing a lie.
  assert.equal(found.error, 'agent_offline');
});

test('a socket is used when the runtime has one', async () => {
  const factory = fakeSocketFactory();
  const client = new AgentClient({
    url: 'http://127.0.0.1:8720',
    now: () => NOW,
    socketFactory: factory,
    replyStallMs: 40,
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot') ? snapshotBody() : {})),
  });
  await client.connect();
  assert.equal(factory.sockets.length, 1);
  assert.equal(factory.sockets[0].url, 'ws://127.0.0.1:8720/events');
  factory.sockets[0].open();
  assert.equal(client.isStreaming(), true);
  client.dispose();
  assert.equal(factory.sockets[0].closed, true);
});

test('a broken frame body leaves the socket alone', async () => {
  const factory = fakeSocketFactory();
  const client = new AgentClient({
    url: 'http://127.0.0.1:8720',
    now: () => NOW,
    socketFactory: factory,
    replyStallMs: 40,
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot') ? snapshotBody() : {})),
  });
  await client.connect();
  const socket = factory.sockets[0];
  socket.open();
  socket.onmessage?.({ data: 'not json' });
  socket.onmessage?.({ data: 12345 });
  assert.equal(client.getStatus(), 'online');
  client.dispose();
});

// --------------------------------------------------------------------- binding

test('the agent own transcript is merged into the surface one, not replaced', async () => {
  const { client: agent } = makeClient();
  await agent.connect();
  const duplex = new DuplexClient({ settings: createSettings({ interface: 'agent' }) });
  duplex.connect();
  const stop = bindAgentConversation(duplex, agent);

  // The surface's own optimistic turn has an id the agent has never seen.
  duplex.adoptHistory([{ id: 'local-1', role: 'user', text: 'are you there?', createdAt: NOW }]);
  stop();
  bindAgentConversation(duplex, agent);
  const ids = duplex.getSnapshot().messages.map((message) => message.id);
  assert.ok(ids.includes('local-1'), 'the surface turn is kept');
  assert.ok(ids.includes('m1'), 'the agent timeline arrives');
  assert.ok(ids.includes('m2'));
  agent.dispose();
  duplex.dispose();
});

test('one exchange is two bubbles, however both sides recorded it', async () => {
  // The bug this file's binding exists to prevent, end to end.
  //
  // A surface writes its own turn optimistically — the question the instant Enter
  // is pressed, the answer when it comes back — and the agent's store records the
  // same two messages again under the row ids it assigned. The two lists are
  // unioned *by id*, and an optimistic id is minted locally while the store's is a
  // UUID, so nothing recognised them as the same pair of messages. One question
  // and one answer were drawn as four bubbles, and from the reader's side that is
  // an agent that has repeated itself twice without being asked.
  //
  // So the ids the agent assigned have to reach the surface's own turn, and then
  // the union does what a union is for: the agent's row wins, and the exchange is
  // drawn once.
  const { client: agent, channel } = makeClient();
  await agent.connect();

  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  // Binding is what the browser does in agent mode: the transcript is the agent's
  // store, unioned with the turn the surface wrote itself.
  const stop = bindAgentConversation(duplex, agent);

  const pending = duplex.sendMessage('hello');
  // The agent records the question it was given, then answers it.
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm-sent', ts: iso(1), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'hello' },
  });
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm9', ts: iso(2), direction: 'outbound', channel: 'web', person_id: 'owner', text: 'Hello, how can I help you?' },
  });

  await pending;

  const shown = duplex.getSnapshot().messages.filter((message) => message.id === 'm-sent' || message.id === 'm9');
  assert.equal(shown.length, 2, 'the question and the answer are each drawn once');
  assert.deepEqual(
    shown.map((message) => [message.role, message.text]),
    [['user', 'hello'], ['assistant', 'Hello, how can I help you?']],
    'and the agent row is the one that survives',
  );
  const texts = duplex.getSnapshot().messages
    .filter((message) => message.text === 'Hello, how can I help you?');
  assert.equal(texts.length, 1, 'a reply is never on screen twice');

  stop();
  agent.dispose();
  duplex.dispose();
});

test('a question the reader superseded is still drawn once', async () => {
  // The same reconciliation has to happen for a turn that never finishes.
  //
  // A second question supersedes the first, so the first turn's answer is dropped
  // — but its question stays in the transcript, and the agent's row for that
  // question is already in its store. Reconciling the two ids only when the answer
  // comes back would leave every superseded question drawn twice, permanently,
  // because the turn that could have fixed it is the one that was abandoned.
  let minted = 0;
  const { client: agent, channel } = makeClient({
    fetchImpl: fakeFetch((url) => {
      if (url.endsWith('/api/snapshot')) return jsonResponse(snapshotBody());
      // The bridge mints a row id per message, so each question has its own.
      if (url.endsWith('/api/message')) {
        minted += 1;
        return jsonResponse({ message_id: `q-${minted}` });
      }
      return jsonResponse({ queued: true });
    }),
  });
  await agent.connect();

  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  const stop = bindAgentConversation(duplex, agent);

  const first = duplex.sendMessage('first');
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'q-1', ts: iso(1), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'first' },
  });
  // A second question before the first has been answered.
  const second = duplex.sendMessage('second');
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'q-2', ts: iso(2), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'second' },
  });
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'a-2', ts: iso(3), direction: 'outbound', channel: 'web', person_id: 'owner', text: 'answer to second' },
  });

  assert.equal(await first, undefined, 'the superseded turn is dropped');
  assert.equal((await second)?.text, 'answer to second');

  // Scoped to this test's own two questions: the snapshot fixture arrives with an
  // earlier exchange already in the transcript, and that one is not what's on trial.
  const mine = duplex.getSnapshot().messages
    .filter((message) => message.text === 'first' || message.text === 'second');
  assert.deepEqual(
    mine.map((message) => message.text),
    ['first', 'second'],
    'each question is drawn once, superseded or not',
  );

  stop();
  agent.dispose();
  duplex.dispose();
});

test('the question a reader just sent is filed under the conversation it went into', async () => {
  // A surface drawing one conversation per person cannot put an unlabelled turn
  // anywhere, so a question typed into somebody else's thread would not appear in
  // it — and a reader who watches their own question vanish has been told the
  // agent did not hear it, which is the one reading they cannot act on. The
  // destination is read when the turn is asked, because the reader is looking at
  // a different conversation than the one they were looking at a moment ago.
  let destination = { channel: 'telegram', person: 'tg:819012345678' };
  const { client: agent, channel } = makeClient();
  await agent.connect();

  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
    destination: () => destination,
  });
  duplex.connect();
  const stop = bindAgentConversation(duplex, agent);

  const first = duplex.sendMessage('hello, Ada');
  const asked = duplex.getSnapshot().messages.find((message) => message.text === 'hello, Ada');
  assert.equal(asked.channel, 'telegram');
  assert.equal(asked.person, 'tg:819012345678');
  assert.equal(
    conversationsIn(duplex.getSnapshot().messages, 'owner')[0].id,
    'telegram:tg:819012345678',
    'and it is the conversation the reader is now on',
  );

  destination = { channel: 'web', person: 'owner' };
  const second = duplex.sendMessage('and me');
  const mine = duplex.getSnapshot().messages.find((message) => message.text === 'and me');
  assert.equal(mine.channel, 'web');
  assert.equal(mine.person, 'owner');

  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm-sent', ts: iso(1), direction: 'inbound', channel: 'web', person_id: 'owner', text: 'and me' },
  });
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: { id: 'm9', ts: iso(2), direction: 'outbound', channel: 'web', person_id: 'owner', text: 'Hello.' },
  });
  await second;
  assert.equal(await first, undefined, 'the superseded turn is dropped, as ever');

  stop();
  agent.dispose();
  duplex.dispose();
});

// --------------------------------------------------------------------- reasons

test('each refusal reads as the different thing it is', () => {
  const reasons = [
    ['agent_offline', /not connected/i],
    ['agent_unreachable', /answered nothing/i],
    ['agent_invalid_endpoint', /not a local interface service/i],
    ['agent_not_running', /not running/i],
    ['agent_stopped', /stopped/i],
    ['agent_declined', /chose not to reply/i],
    ['agent_unanswered', /could not answer/i],
    ['agent_timeout', /went quiet/i],
    ['agent_aborted', /Stopped waiting/i],
  ];
  for (const [code, pattern] of reasons) {
    const said = describeAgentFailure(new AgentRequestError(code), 'en');
    assert.match(String(said), pattern, code);
  }
  assert.equal(describeAgentFailure(new Error('other'), 'en'), null);
});

// ------------------------------------------- whether there is an agent to ask

test('a machine with no agent on it is named, not waited on', async () => {
  // The reader's report has to name the situation, because the fixes are
  // opposites: "went quiet" means resume the agent, "not running" means start
  // one. A bridge answers as happily with no agent behind it as with one, so the
  // only thing that tells them apart is what the agent itself last recorded — and
  // a snapshot carrying a cycle from an hour ago is not a sign of life fetched
  // just now. Reporting this as a stall is what sent the reader looking for a
  // `resume` button on a machine that had nothing running on it at all.
  const { client } = makeClient({
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot')
      ? snapshotBody({ presence: { state: 'WAITING', focus: null, ts: iso(-3_600_000), recentCycles: [] } })
      : { message_id: 'm-sent' })),
  });
  await client.connect();
  assert.equal(client.isAgentAbsent(), true);
  await assert.rejects(() => client.awaitReply(NOW + 10_000, undefined), (error) => {
    assert.equal(error.code, 'agent_not_running');
    return true;
  });
  client.dispose();
});

test('an agent that has never run is absent too', async () => {
  const { client } = makeClient({
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot')
      ? snapshotBody({ presence: { state: 'UNKNOWN', focus: null, ts: null, recentCycles: [] } })
      : { message_id: 'm-sent' })),
  });
  await client.connect();
  assert.equal(client.isAgentAbsent(), true);
  client.dispose();
});

test('a link that has not looked yet does not claim the agent is gone', async () => {
  // "No sign of life" and "not asked yet" are the same null, and only one of them
  // is a fact about the agent. A reader who typed before the first read came back
  // was told the agent was not running on the strength of a link that had not
  // spoken.
  const { client } = makeClient();
  assert.equal(client.isAgentAbsent(), false);
  client.dispose();
});

test('an agent that is working is never absent', async () => {
  const { client } = makeClient();
  await client.connect();
  assert.equal(client.isAgentAbsent(), false);
  client.dispose();
});

test('a question is not queued at all when there is no agent to answer it', async () => {
  const fetcher = fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot')
    ? snapshotBody({ presence: { state: 'WAITING', focus: null, ts: iso(-3_600_000), recentCycles: [] } })
    : { message_id: 'm-sent' }));
  const { client: agent } = makeClient({ fetchImpl: fetcher });
  await agent.connect();
  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  const reply = await duplex.sendMessage('hello?');
  assert.equal(reply, undefined);
  assert.match(String(duplex.getSnapshot().lastError), /not running/i);
  assert.equal(
    fetcher.calls.some((call) => call.url.endsWith('/api/message')),
    false,
    'a question nothing can answer must not be recorded as asked',
  );
  agent.dispose();
  duplex.dispose();
});

test('a paused agent is reported as stopped, which resume can fix', async () => {
  const { client: agent } = makeClient({
    fetchImpl: fakeFetch((url) => jsonResponse(url.endsWith('/api/snapshot')
      ? snapshotBody({ control: { paused: true, pause_actions: true, stopped: false, emergency: false } })
      : { message_id: 'm-sent' })),
  });
  await agent.connect();
  const duplex = new DuplexClient({
    settings: createSettings({ interface: 'agent' }),
    transport: createAgentTransport({ resolve: () => agent, now: () => NOW }),
  });
  duplex.connect();
  await duplex.sendMessage('hello?');
  assert.match(String(duplex.getSnapshot().lastError), /stopped/i);
  agent.dispose();
  duplex.dispose();
});

test('a presence event is a sign of life even for a kind nothing draws', async () => {
  // Only a presence, a thought and an action used to count, so a link whose agent
  // was demonstrably working — publishing its budget, its intentions, its
  // declines — looked dead, and every wait on it ran out.
  const { client, channel } = makeClient();
  await client.connect();
  channel.push({ type: 'event', kind: 'budget.update', payload: { day_total: 1 } });
  assert.equal(client.isAgentAbsent(), false);
  channel.push({ type: 'event', kind: 'something.new', payload: {} });
  assert.equal(client.isAgentAbsent(), false);
  client.dispose();
});

// ------------------------------------------- a reply that arrives, and one that
// ------------------------------------------------------ is deliberately not given

test('a pushed reply settles the wait on its own timestamp', async () => {
  // The event used to carry no `ts`, so the message read back with a time of
  // zero and could never satisfy `ts >= after`. The reply was on the wire the
  // whole time and the wait still ran out, which is what turned a working agent
  // into a stall in the reader's eyes.
  const { client, channel } = makeClient();
  await client.connect();
  const waiting = client.awaitReply(NOW - 1000, undefined, new Set(['m2']));
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: {
      id: 'm9',
      ts: iso(500),
      direction: 'outbound',
      channel: 'web',
      person_id: 'owner',
      text: 'here is the answer',
      delivery: { status: 'sent', urgency: 'normal' },
    },
  });
  const reply = await waiting;
  assert.equal(reply.text, 'here is the answer');
  client.dispose();
});

test('a pushed reply reports the delivery the agent recorded', async () => {
  // `delivery` was rebuilt from `payload.urgency`, so the status came out as the
  // word "normal" for every message. A queued reply and a delivered one read
  // identically, and neither matched what the agent had actually done.
  const { client, channel } = makeClient();
  await client.connect();
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: {
      id: 'm9',
      ts: iso(500),
      direction: 'outbound',
      channel: 'web',
      person_id: 'owner',
      text: 'batched',
      urgency: 'normal',
      delivery: { status: 'queued', urgency: 'normal' },
    },
  });
  const message = client.getView().messages.find((entry) => entry.id === 'm9');
  assert.equal(message.delivery_status, 'queued');
  assert.equal(message.urgency, 'normal');
  client.dispose();
});

test('a question the agent answers with nothing is a decline, not a stall', async () => {
  // The agent is free to answer with nothing [D-05]. When it does, the surface
  // used to wait out the whole budget and then report that the agent had gone
  // quiet — describing a decision as a failure, with nothing to tell the person
  // reading it that the agent had chosen.
  const { client, channel } = makeClient();
  await client.connect();
  const transport = createAgentTransport({ resolve: () => client, now: () => NOW });
  const waiting = transport.complete('are you there?', [], new AbortController().signal, () => {});
  // The id the bridge returned for the question, and the id the agent declines.
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: { message_id: 'm-sent', action: 'ignore', reason: 'no_response_needed' },
  });
  await assert.rejects(() => waiting, (error) => {
    assert.equal(error.code, 'agent_declined');
    assert.match(describeAgentFailure(error, 'en'), /chose not to reply/i);
    return true;
  });
  client.dispose();
});

test('a question nobody managed to answer is not reported as a decline', async () => {
  // Both arrive as `act_silently` with no outbound row behind them, so the agent
  // says which one it is. It has to: a model that failed, ran out of room before
  // writing anything, or answered in prose all produced exactly what a chosen
  // silence produces, and telling somebody the agent had chosen not to reply to
  // every message it was sent was not a reading of the evidence — it was the
  // opposite of it. The two also call for opposite things, since one is worth
  // sending again.
  const { client, channel } = makeClient();
  await client.connect();
  const transport = createAgentTransport({ resolve: () => client, now: () => NOW });
  const waiting = transport.complete('are you there?', [], new AbortController().signal, () => {});
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: {
      message_id: 'm-sent',
      action: 'act_silently',
      reason: 'the model thought at length and wrote no answer',
      decided: false,
    },
  });
  await assert.rejects(() => waiting, (error) => {
    assert.equal(error.code, 'agent_unanswered');
    assert.match(describeAgentFailure(error, 'en'), /could not answer/i);
    assert.match(describeAgentFailure(error, 'en'), /thought at length/i);
    return true;
  });
  client.dispose();
});

test('a wait that arms after the event arrived still reads it as what it was', async () => {
  // The question goes out over HTTP and is waited on afterwards, so the agent can
  // read it, decide against replying, and say so inside that gap. The memory that
  // bridges the gap therefore has to keep the code as well as the reason, or a
  // failure that arrives first is reported to the next wait as a decision.
  const { client, channel } = makeClient();
  await client.connect();
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: { message_id: 'm-sent', action: 'act_silently', reason: 'no answer', decided: false },
  });
  await assert.rejects(
    () => client.awaitReply(NOW, new AbortController().signal, new Set(), 'm-sent'),
    (error) => error.code === 'agent_unanswered',
    'the remembered failure is still a failure',
  );
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: { message_id: 'm-sent', action: 'ignore', reason: 'no_response_needed' },
  });
  await assert.rejects(
    () => client.awaitReply(NOW, new AbortController().signal, new Set(), 'm-sent'),
    (error) => error.code === 'agent_declined',
    'and the freshest word about a question wins',
  );
  client.dispose();
});

test('a decline about some other question does not end this wait', async () => {
  // The id is the whole mechanism. A decline naming a message that is not the one
  // this surface asked about says nothing about this turn, and ending the wait on
  // it would report a decision that was never made.
  const { client, channel } = makeClient();
  await client.connect();
  const transport = createAgentTransport({ resolve: () => client, now: () => NOW });
  const waiting = transport.complete('are you there?', [], new AbortController().signal, () => {});
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: { message_id: 'somebody-elses-message', action: 'ignore', reason: 'spam' },
  });
  await assert.rejects(() => waiting, (error) => {
    assert.equal(error.code, 'agent_timeout');
    return true;
  });
  client.dispose();
});

test('a reply that lands after a decline still answers the question', async () => {
  // The agent may deliberate, decide to be silent, and then have something to
  // say anyway. Whichever arrives second must not reopen or overwrite the turn,
  // so the message is recorded either way and the wait is only ever settled once.
  const { client, channel } = makeClient();
  await client.connect();
  const waiting = client.awaitReply(NOW - 1000, undefined, new Set(['m2']), 'm-sent');
  channel.push({
    type: 'event',
    kind: 'message.new',
    payload: {
      id: 'm9', ts: iso(500), direction: 'outbound', channel: 'web',
      person_id: 'owner', text: 'on reflection, yes',
      delivery: { status: 'sent', urgency: 'normal' },
    },
  });
  channel.push({
    type: 'event',
    kind: 'message.handled',
    payload: { message_id: 'm-sent', action: 'ignore', reason: 'no_response_needed' },
  });
  const reply = await waiting;
  assert.equal(reply.text, 'on reflection, yes');
  assert.equal(
    client.getView().messages.filter((entry) => entry.id === 'm9').length,
    1,
    'the reply is on the timeline once',
  );
  client.dispose();
});

// ------------------------------------------------------------- the doors

test('a door is found by the name a reader would say it', async () => {
  // A terminal has no dropdown, so `/channel Telegram` has to be the same answer
  // as `/channel telegram`. The name on screen is the label; the name in the
  // config is the id; a reader should not have to know which they are typing.
  const { findDoor, readAgentDoors } = await import('../dist/agent/types.js');
  const doors = readAgentDoors({
    channels: [
      { id: 'web', enabled: true, admits: true, contacts: [] },
      { id: 'telegram', enabled: true, admits: true, contacts: [] },
    ],
  });
  for (const name of ['telegram', 'Telegram', ' TELEGRAM ']) {
    const found = findDoor(doors, name);
    assert.equal(found.ok, true, name);
    assert.equal(found.ok && found.door.id, 'telegram', name);
  }
});

test('a door that is not open answers with the ones that are', async () => {
  // "Unknown channel" alone leaves the only question a reader has — which? — with
  // no answer. And the list is the deployment's own, so a door added by
  // configuration is as nameable as a built-in one.
  const { findDoor, readAgentDoors } = await import('../dist/agent/types.js');
  const doors = readAgentDoors({
    channels: [
      { id: 'web', enabled: true, admits: true, contacts: [] },
      { id: 'slack', enabled: true, admits: true, contacts: [] },
    ],
  });
  const found = findDoor(doors, 'telegram');
  assert.equal(found.ok, false);
  assert.deepEqual(found.ok === false && found.offered, ['slack', 'web']);
});

test('a door knows where it reaches one person and not another', async () => {
  // The address is not the person, and this is the one place on the surface side
  // that has to hold both: `owner` is not a place Telegram can post to.
  const { addressFor, readAgentDoors } = await import('../dist/agent/types.js');
  const door = readAgentDoors({
    channels: [
      {
        id: 'telegram',
        enabled: true,
        admits: true,
        contacts: [{ person: 'owner', address: 'tg:819012345678' }],
      },
    ],
  }).find((entry) => entry.id === 'telegram');
  assert.equal(addressFor(door, 'owner'), 'tg:819012345678');
  assert.equal(addressFor(door, 'Owner'), null, 'a person is a key, not a guess');
  assert.equal(addressFor(door, 'someone-else'), null);
  assert.equal(addressFor(undefined, 'owner'), null);
});

test('a door that admits nobody is read as unusable, not as working', async () => {
  // A door that is on with an empty allowlist *looks* configured and answers
  // nobody, and a surface that offers it sends the person's messages into silence.
  const { reconcileDoors, readAgentDoors } = await import('../dist/agent/types.js');
  const doors = readAgentDoors({
    channels: [
      { id: 'telegram', enabled: true, admits: false, contacts: [] },
      { id: 'slack', enabled: true, admits: true, contacts: [] },
    ],
  });
  const { offered, closed } = reconcileDoors(doors, ['telegram']);
  assert.deepEqual(offered.map((door) => door.id), ['slack', 'web']);
  // A door the transcript holds and the deployment does not offer is *closed*, and
  // that is the one thing a filter cannot work out on its own.
  assert.deepEqual(closed, ['telegram']);
});
