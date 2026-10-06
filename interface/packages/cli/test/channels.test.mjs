import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { doorNeedsAddress, doorSummary, findConversation, resolveDoor } from '../dist/channels.js';

/** The doors a deployment with Telegram and an empty Slack would report. */
const DOORS = [
  { id: 'web', enabled: true, admits: true, contacts: [{ person: 'owner', address: 'owner' }] },
  {
    id: 'telegram',
    enabled: true,
    admits: true,
    contacts: [{ person: 'owner', address: 'tg:819012345678' }],
  },
  { id: 'slack', enabled: true, admits: false, contacts: [] },
];

const MESSAGES = [
  { id: '1', role: 'user', text: 'hi', createdAt: 0, channel: 'telegram' },
  { id: '2', role: 'assistant', text: 'hello', createdAt: 1, channel: 'telegram' },
];

/** A transcript that also holds a conversation on the Slack door. */
const MESSAGES_WITH_SLACK = [
  ...MESSAGES,
  { id: '3', role: 'user', text: 'earlier', createdAt: 2, channel: 'slack' },
];

// ------------------------------------------------------------- what a door needs

test('a local door needs no address, because it delivers to whoever is reading', () => {
  assert.equal(doorNeedsAddress('web'), false);
  assert.equal(doorNeedsAddress('cli'), false);
  assert.equal(doorNeedsAddress('telegram'), true);
  assert.equal(doorNeedsAddress('slack'), true);
  assert.equal(doorNeedsAddress('discord'), true);
});

test('no channel at all is this terminal\'s own line, and always works', () => {
  // The local line is the one door whose being open is a property of the bridge
  // rather than of a config file, so it is the answer that cannot fail. The
  // terminal's words travel on the terminal's door — `web` is the conversation
  // the browser conducts.
  const choice = resolveDoor(DOORS, 'owner');
  assert.equal(choice.ok, true);
  assert.equal(choice.ok && choice.channel, 'cli');
  assert.equal(choice.ok && choice.address, null);
});

test('a door the deployment has open is found by its name', () => {
  const choice = resolveDoor(DOORS, 'owner', 'Telegram');
  assert.equal(choice.ok, true);
  assert.equal(choice.ok && choice.channel, 'telegram');
  // The address comes out of the contact book, so a reader does not have to learn
  // the namespace the door addresses people in.
  assert.equal(choice.ok && choice.address, 'tg:819012345678');
});

test('a door that is not open answers with the ones that are', () => {
  const choice = resolveDoor(DOORS, 'owner', 'carrier-pigeon');
  assert.equal(choice.ok, false);
  assert.equal(choice.ok === false && choice.code, 'channel_unknown');
  // The values carry the list, which is the only part of "unknown channel"
  // anybody can act on.
  assert.equal(choice.ok === false && choice.values.offered, 'slack, telegram, web');
});

test('a door that is on but admits nobody is refused before the send', () => {
  // It looks configured and answers nobody, so a question sent into it is a
  // question the agent deliberates on and the reader never gets answered.
  const choice = resolveDoor(DOORS, 'owner', 'slack');
  assert.equal(choice.ok, false);
  assert.equal(choice.ok === false && choice.code, 'channel_closed');
  assert.equal(choice.ok === false && choice.values.channel, 'Slack');
});

test('a person with no address on a door is refused before the send', () => {
  // The refusal, rather than a row written and a reply discarded at the last step
  // — which is a question, a long pause, and silence.
  const choice = resolveDoor(DOORS, 'someone-else', 'telegram');
  assert.equal(choice.ok, false);
  assert.equal(choice.ok === false && choice.code, 'channel_unaddressed');
  assert.equal(choice.ok === false && choice.values.person, 'someone-else');
});

test('an address handed over directly is used instead of the contact book', () => {
  // The escape hatch for a deployment that knows an address the book has not been
  // taught. The adapter is still the last word on whether it is usable.
  const choice = resolveDoor(DOORS, 'owner', 'telegram', 'tg:999');
  assert.equal(choice.ok, true);
  assert.equal(choice.ok && choice.address, 'tg:999');
});

test('a local door is chosen even with an address, because it needs none', () => {
  // `--to` on a local door is not a destination anywhere, so the local answer
  // stands rather than a request that would be refused at the far end.
  const choice = resolveDoor(DOORS, 'owner', 'web', 'owner');
  assert.equal(choice.ok, true);
  assert.equal(choice.ok && choice.channel, 'web');
});

// ------------------------------------------------------------ offered / closed

test('a door the transcript holds and the deployment does not is closed', () => {
  // The difference a filter cannot work out on its own: a conversation happened
  // there, and the agent can no longer be spoken to on it. Slack is on but
  // admits nobody, so it is neither offered nor sendable — while the transcript
  // still holds what was said on it, which is exactly the state that reads from
  // outside as "nothing has been said".
  const { offered, closed } = doorSummary(DOORS, MESSAGES_WITH_SLACK);
  assert.deepEqual(offered.map((door) => door.id), ['telegram', 'web']);
  assert.deepEqual(closed, ['slack']);
});

test('a door the transcript holds and the deployment still has stays open', () => {
  // Being in the transcript is not what closes a door; losing it is. A door that
  // is on and admitting somebody is offered however much has been said on it.
  const { offered, closed } = doorSummary(DOORS, MESSAGES);
  assert.deepEqual(offered.map((door) => door.id), ['telegram', 'web']);
  assert.deepEqual(closed, []);
});

test('a door the transcript has never held is not called closed', () => {
  // "Closed" is a fact about the transcript as well as the configuration, so a
  // door nobody has ever spoken on is simply not mentioned.
  const { closed } = doorSummary(DOORS, []);
  assert.deepEqual(closed, []);
});

// ----------------------------------------------------------- picking a conversation

/** Three correspondents, from the conversations the transcript holds. */
const CONVERSATIONS = [
  { id: 'telegram:tg:819012345678', channel: 'telegram', person: 'tg:819012345678', label: 'Ada Lovelace', messages: 2, lastAt: 3 },
  { id: 'telegram:tg:2', channel: 'telegram', person: 'tg:2', label: 'Grace Hopper', messages: 1, lastAt: 2 },
  { id: 'web:owner', channel: 'web', person: 'owner', label: 'owner', messages: 1, lastAt: 1 },
];

test('a conversation id opens the conversation, and not merely the door', () => {
  // Two people writing in on one Telegram door are one door and two
  // conversations. Selecting the door would answer both of them in one voice, so
  // the id a reader copies out of `/channels` has to carry the address with it —
  // which is exactly the key the browser's people list selects on.
  const found = findConversation(CONVERSATIONS, 'telegram:tg:2');
  assert.equal(found?.label, 'Grace Hopper');
  assert.equal(found?.channel, 'telegram');
  assert.equal(found?.person, 'tg:2');
});

test('the reader\'s own conversation is opened the same way', () => {
  // The local line is a conversation too, and it is the one a terminal is on by
  // default — so the spelling that opens it has to be the same as every other's.
  assert.equal(findConversation(CONVERSATIONS, 'web:owner')?.channel, 'web');
});

test('a door name is not a conversation, so the two spellings cannot be confused', () => {
  // A door id holds no colon, which is what keeps `/channel telegram` meaning the
  // door and `/channel telegram:tg:2` meaning one exchange on it.
  assert.equal(findConversation(CONVERSATIONS, 'telegram'), null);
});

test('an id nothing holds is left to the door refusals, which name what would work', () => {
  // A mistyped conversation is no worse off than a mistyped door: the caller falls
  // through to `resolveDoor`, whose refusal lists the doors that are open.
  assert.equal(findConversation(CONVERSATIONS, 'telegram:tg:999'), null);
  assert.equal(findConversation(CONVERSATIONS, ''), null);
});
