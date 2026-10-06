import { useEffect, useMemo, useState } from 'react';
import {
  conversationKey,
  createTranslator,
  type AgentControlAction,
  type AgentConversation,
  type AgentDoor,
  type AgentView,
  type ClientSnapshot,
  type Language,
  type TranslationKey,
} from '@project-phone/core';
import { Composer } from '../components/Composer';
import { ConversationFeed, useConversationFeed } from '../components/ConversationFeed';
import { Icon } from '../components/Icon';
import { Button, Divider, IconButton, SectionLabel, StatusPill, classNames } from '../components/Primitives';
import {
  ChannelBar,
  EmptyNote,
  IntentionNode,
  PersonList,
  SpendBar,
  TONE_DOT,
  TONE_TEXT,
  actionTone,
  formatClock,
  formatDay,
  formatUsd,
  humanizeState,
  stateTone,
  type AgentT,
} from '../components/agent/agent-kit';

type Panel = 'intentions' | 'actions' | 'thoughts' | 'budget' | 'guardian';

const PANELS: Array<{ id: Panel; label: TranslationKey; icon: 'edit' | 'database' | 'spark' | 'activity' | 'shield' }> = [
  { id: 'intentions', label: 'panelIntentions', icon: 'edit' },
  { id: 'actions', label: 'panelActions', icon: 'database' },
  { id: 'thoughts', label: 'panelThoughts', icon: 'spark' },
  { id: 'budget', label: 'panelBudget', icon: 'activity' },
  { id: 'guardian', label: 'panelGuardian', icon: 'shield' },
];

interface AgentScreenProps {
  snapshot: ClientSnapshot;
  view: AgentView;
  streaming: boolean;
  connected: boolean;
  pendingAction: AgentControlAction | null;
  armedEmergency: boolean;
  notice: string | null;
  onArmEmergency: (armed: boolean) => void;
  onControl: (action: AgentControlAction) => void;
  onSend: (text: string) => void;
  onInterrupt: () => void;
  onUndo: (id: string) => void;
  onCloseIntention: (id: string) => void;
  onRefresh: () => void;
  onOpenSettings: () => void;
  /** Everyone the agent is talking to, out of the transcript, most recent first. */
  conversations: readonly AgentConversation[];
  /** Doors the agent has open, as its configuration reported them. */
  doors: readonly AgentDoor[];
  /** Doors the agent has spoken on and no longer has open. */
  closedChannels: readonly string[];
  /** The conversation this surface is on, or `null` before the transcript says. */
  conversation: AgentConversation | null;
  onSelectConversation: (id: string) => void;
}

/**
 * The agent's interface.
 *
 * One conversation and one instrument panel, side by side. The conversation is
 * where a person talks to the agent; the panel is where they watch it be
 * something awake — what it is doing, what it intends, what it has done, what it
 * has spent and what it is protecting. Neither half is decoration: an agent that
 * thinks when nobody is talking needs somewhere for that thinking to be visible,
 * or this screen has nothing to show for the whole of its life.
 *
 * The lifecycle controls sit in the header rather than in a preferences page on
 * purpose. Pausing an agent and stopping one are decisions about *right now*, and
 * a decision whose only entry point is four clicks away is one nobody makes at the
 * moment they need it.
 */
export function AgentScreen({
  snapshot,
  view,
  streaming,
  connected,
  pendingAction,
  armedEmergency,
  notice,
  onArmEmergency,
  onControl,
  onSend,
  onInterrupt,
  onUndo,
  onCloseIntention,
  onRefresh,
  onOpenSettings,
  conversations,
  doors,
  closedChannels,
  conversation,
  onSelectConversation,
}: AgentScreenProps) {
  const language = snapshot.settings.language;
  const t = useMemo(() => createTranslator(language), [language]);
  const [panel, setPanel] = useState<Panel>('intentions');
  const [draft, setDraft] = useState('');
  const now = Date.now();

  /**
   * The people, named the way this screen names them.
   *
   * The reader's own lines — the web line and the terminal's line — are the two
   * names the store does not hold: they are the conversations this reader
   * conducts themselves, and a list of correspondents with the reader in it
   * under a deployment's word for them is a list they have to read twice to work
   * out who they are. Everywhere else the name on file is the right one, because
   * the channel's own spelling of a person is the one they recognise.
   *
   * Only the label moves. Which conversation is selected is the door and the
   * address, and that is decided where the reply will be sent from — renaming a
   * conversation here cannot change where anything goes.
   */
  const localLine = `web:${snapshot.settings.agentPerson}`;
  const terminalLine = `cli:${snapshot.settings.agentPerson}`;
  const people = useMemo(
    () => conversations.map((entry) => (
      entry.id === localLine || entry.id === terminalLine
        ? { ...entry, label: t('you') }
        : entry
    )),
    [conversations, localLine, terminalLine, t],
  );
  const current = people.find((entry) => entry.id === conversation?.id) ?? null;

  /**
   * A place in the bar is a click to a conversation.
   *
   * The interfaces are one conversation each — the reader's own on that door. A
   * messaging app is many, so its newest is opened and the list of names below
   * the bar is how the reader moves between them; a door nobody has written on
   * yet opens with no one in it, which the pane says rather than hiding.
   */
  const selectChannel = (id: string) => {
    const onChannel = people.filter((entry) => entry.channel === id);
    const newest = [...onChannel].sort((left, right) => right.lastAt - left.lastAt)[0];
    onSelectConversation(newest?.id ?? `${id}:`);
  };

  const submit = () => {
    const value = draft.trim();
    if (!value) return;
    setDraft('');
    onSend(value);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-[var(--line)] px-4 py-3 sm:px-6">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <PresenceSummary t={t} view={view} />

          <div className="flex flex-wrap items-center gap-2">
            <StatusPill
              label={!connected ? t('agentLinkOffline') : streaming ? t('agentLinkLive') : t('agentLinkPolling')}
              status={!connected ? 'offline' : streaming ? 'online' : 'busy'}
            />
            <LifecycleControls
              armedEmergency={armedEmergency}
              control={view.control}
              onArmEmergency={onArmEmergency}
              onControl={onControl}
              pendingAction={pendingAction}
              t={t}
            />
            <Button icon="activity" variant="outline" onClick={onRefresh} title={t('presenceUpdated')}>
              {formatClock(view.presence.ts, language)}
            </Button>
            <IconButton icon="settings" label={t('settings')} onClick={onOpenSettings} />
          </div>
        </div>
        {armedEmergency ? <p className="mx-auto mt-2 w-full max-w-[1600px] text-xs text-[var(--danger)]">{t('controlArmEmergency')}</p> : null}
        {notice ? <NoticeLine notice={notice} t={t} view={view} /> : null}
        <CycleStrip t={t} view={view} />
      </header>

      <div className="mx-auto grid w-full max-w-[1600px] min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden px-4 pb-4 pt-4 lg:grid-cols-[minmax(0,1fr)_23rem]">
        <TimelinePane
          closedChannels={closedChannels}
          conversation={current}
          conversations={people}
          doors={doors}
          draft={draft}
          language={language}
          onDraft={setDraft}
          onInterrupt={onInterrupt}
          onSelectChannel={selectChannel}
          onSelectConversation={onSelectConversation}
          onSubmit={submit}
          snapshot={snapshot}
          t={t}
        />

        <aside className="hidden min-h-0 flex-col gap-3 lg:flex">
          {/* One line, scrollable rather than wrapped: a tab row that reflows
              changes which panel a reader is looking at when the window is
              resized, and a control that moves is a control that is missed. */}
          <div className="scrollbar-thin flex shrink-0 gap-1.5 overflow-x-auto" role="group">
            {PANELS.map((entry) => (
              <button
                aria-pressed={panel === entry.id}
                className={classNames(
                  'instrument-button inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 py-1.5 text-[0.68rem] font-medium transition',
                  panel === entry.id
                    ? 'border-[var(--accent-line)] bg-[var(--accent-soft)] accent-text'
                    : 'border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-faint)] hover:text-[var(--ink-muted)]',
                )}
                key={entry.id}
                onClick={() => setPanel(entry.id)}
                type="button"
              >
                <Icon name={entry.icon} size={12} />
                <span>{t(entry.label)}</span>
              </button>
            ))}
          </div>
          <section className="instrument-panel scrollbar-thin min-h-0 flex-1 overflow-y-auto rounded-2xl p-4">
            {panel === 'intentions' ? (
              <IntentionsPane language={language} now={now} onClose={onCloseIntention} t={t} view={view} />
            ) : null}
            {panel === 'actions' ? <ActionsPane language={language} onUndo={onUndo} t={t} view={view} /> : null}
            {panel === 'thoughts' ? <ThoughtsPane language={language} t={t} view={view} /> : null}
            {panel === 'budget' ? <BudgetPane language={language} t={t} view={view} /> : null}
            {panel === 'guardian' ? <GuardianPane language={language} now={now} t={t} view={view} /> : null}
          </section>
        </aside>
      </div>
    </div>
  );
}

/**
 * What the last command did, in the agent's own terms.
 *
 * An undo and a cancelled intention are queued, not performed: the agent decides
 * whether it will act on them. Saying "queued" is the honest word, and it is the
 * one that stops a reader believing an action has already been reversed.
 */
function NoticeLine({ notice, t, view }: { notice: string; t: AgentT; view: AgentView }) {
  let body = t('controlQueued', { action: notice });
  if (notice.startsWith('undo:')) {
    const id = notice.slice('undo:'.length);
    const action = view.actions.find((entry) => entry.id === id);
    body = action
      ? t('actionUndoQueued', { tool: action.tool })
      : t('controlQueued', { action: notice.slice('undo:'.length) });
  }
  if (notice.startsWith('close:')) {
    const id = notice.slice('close:'.length);
    const title = flattenTitles(view).find((node) => node.id === id)?.title;
    body = title ? t('intentionCloseQueued', { title }) : t('controlQueued', { action: id });
  }
  return (
    <p className="mx-auto mt-2 w-full max-w-[1600px] text-xs text-[var(--ok)]">
      <span className="mr-1.5">✓</span>
      {body}
    </p>
  );
}

function flattenTitles(view: AgentView): AgentView['intentions'] {
  const out: AgentView['intentions'] = [];
  const walk = (nodes: AgentView['intentions']): void => {
    for (const node of nodes) {
      out.push(node);
      walk(node.children);
    }
  };
  walk(view.intentions);
  return out;
}

function PresenceSummary({ view, t }: { view: AgentView; t: AgentT }) {
  const tone = stateTone(view.presence.state);
  return (
    <div className="flex min-w-0 items-center gap-4">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--line-strong)] bg-[var(--canvas-raised)] accent-text shadow-emboss">
        <Icon name="spark" size={19} />
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-sm font-semibold tracking-wide text-[var(--ink)]">{t('agentTitle')}</h1>
          <span className={classNames('inline-flex items-center gap-1.5 text-xs', TONE_TEXT[tone])}>
            <span className={classNames('h-1.5 w-1.5 rounded-full', TONE_DOT[tone], tone === 'accent' && 'signal-pulse')} />
            <span>{view.loaded ? humanizeState(view.presence.state) : t('presenceNever')}</span>
          </span>
        </div>
        <p className="truncate text-xs text-[var(--ink-faint)]">
          {view.presence.focus
            ? `${t('presenceFocus')}: ${view.presence.focus.title}`
            : view.loaded ? t('presenceIdle') : t('presenceNeverRun')}
        </p>
      </div>
    </div>
  );
}

function LifecycleControls({
  control, pendingAction, armedEmergency, t, onControl, onArmEmergency,
}: {
  control: AgentView['control'];
  pendingAction: AgentControlAction | null;
  armedEmergency: boolean;
  t: AgentT;
  onControl: (action: AgentControlAction) => void;
  onArmEmergency: (armed: boolean) => void;
}) {
  const busy = Boolean(pendingAction);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        disabled={busy || control.paused}
        icon="pause"
        variant="outline"
        onClick={() => onControl('pause_all')}
        title={t('controlPauseAll')}
      >
        {t('controlPauseAll')}
      </Button>
      <Button
        disabled={busy || (!control.paused && !control.stopped)}
        icon="play"
        variant="outline"
        onClick={() => onControl('resume')}
        title={t('controlResume')}
      >
        {t('controlResume')}
      </Button>
      <Button
        disabled={busy || control.stopped}
        icon="pause"
        variant="outline"
        onClick={() => onControl('stop')}
        title={t('controlStop')}
      >
        {t('controlStop')}
      </Button>
      {armedEmergency ? (
        // The confirm reads as what it is: the button that does the thing is
        // gone, and what is left only fires after it has been pressed on purpose.
        // Cancel disarms without firing, so a mistake stays a mistake and not
        // an emergency.
        <>
          <Button icon="shield" variant="danger" onClick={() => { onControl('emergency_stop'); onArmEmergency(false); }}>
            {t('controlConfirm')}
          </Button>
          <Button icon="close" variant="outline" onClick={() => onArmEmergency(false)} title={t('controlCancel')}>
            {t('controlCancel')}
          </Button>
        </>
      ) : (
        <Button
          disabled={busy || control.emergency}
          icon="shield"
          variant="outline"
          onClick={() => onArmEmergency(true)}
          title={t('controlEmergencyStop')}
        >
          {t('controlEmergencyStop')}
        </Button>
      )}
    </div>
  );
}

/**
 * One mark per recent cycle, oldest at the left.
 *
 * Fixed-width marks rather than equal fractions of the bar: with only two cycles
 * recorded, a strip that divides the width evenly reads as a progress bar at
 * fifty per cent, which is a claim about the agent that nothing supports. A short
 * history should look short.
 */
function CycleStrip({ view, t }: { view: AgentView; t: AgentT }) {
  const cycles = view.presence.recentCycles.slice(0, 24).reverse();
  if (!cycles.length) return null;
  return (
    <div className="mx-auto mt-2 flex w-full max-w-[1600px] items-center gap-2">
      <span className="shrink-0 text-[0.62rem] uppercase tracking-[0.14em] text-[var(--ink-faint)]">
        {t('presenceCycles')}
      </span>
      <span className="flex items-center gap-1 overflow-hidden">
        {cycles.map((cycle, index) => (
          <span
            className={classNames('h-1 w-3 shrink-0 rounded-full opacity-80', TONE_DOT[stateTone(cycle.state)])}
            key={`${cycle.ts ?? 'x'}-${index}`}
            title={`${cycle.state}${cycle.tier ? ` · ${cycle.tier}` : ''}`}
          />
        ))}
      </span>
    </div>
  );
}

function TimelinePane({
  snapshot, draft, language, t, onDraft, onSubmit, onInterrupt,
  conversations, doors, closedChannels, conversation, onSelectChannel, onSelectConversation,
}: {
  snapshot: ClientSnapshot;
  draft: string;
  language: Language;
  t: AgentT;
  onDraft: (value: string) => void;
  onSubmit: () => void;
  onInterrupt: () => void;
  conversations: readonly AgentConversation[];
  doors: readonly AgentDoor[];
  closedChannels: readonly string[];
  conversation: AgentConversation | null;
  onSelectChannel: (channel: string) => void;
  onSelectConversation: (id: string) => void;
}) {
  /**
   * What this pane shows: one conversation, narrowed by the person named above
   * it.
   *
   * Narrowed rather than filtered, because a conversation is the only unit the
   * agent can answer in — a reply has to go to somebody, and "everybody at once"
   * is not an address. The key is the agent's own, so the line drawn here and the
   * row the agent will answer are the same line by construction.
   */
  const messages = useMemo(
    () => (conversation
      ? snapshot.messages.filter((message) => conversationKey(message) === conversation.id)
      : []),
    [snapshot.messages, conversation],
  );
  const feed = useConversationFeed();
  // A messaging app is a door several people talk through, so it is the one
  // place with a list of names under the bar. The interfaces are one
  // conversation each, and a list of one name would be chrome around nothing.
  const isApp = conversation !== null
    && conversation.channel !== 'web'
    && conversation.channel !== 'cli';
  const people = useMemo(
    () => (conversation
      ? conversations.filter((entry) => entry.channel === conversation.channel && entry.person)
      : []),
    [conversations, conversation],
  );

  /**
   * A different conversation is a different place in the scrollback, so it opens
   * where that one ends. Without this the reader would keep the place they were
   * in another conversation's scrollbar, which is how you end up looking at the
   * oldest message of a conversation that has never scrolled.
   */
  useEffect(() => {
    feed.toLatest();
  }, [conversation?.id, feed.toLatest]);

  const send = () => {
    // Sending says the newest line is the one being read, whatever the reader
    // was looking at a moment ago.
    feed.toLatest();
    onSubmit();
  };

  return (
    <section className="instrument-panel flex min-h-[22rem] min-w-0 flex-col overflow-hidden rounded-2xl lg:min-h-0" aria-label={t('panelTimeline')}>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-[var(--line)] px-4 py-3 sm:px-5">
        <SectionLabel icon="command">{t('panelTimeline')}</SectionLabel>
        <div className="flex items-center gap-2">
          <span className="text-[0.68rem] text-[var(--ink-faint)]">
            {messages.length === 1
              ? t('messageCountOne', { count: messages.length })
              : t('messageCount', { count: messages.length })}
          </span>
          {/* Not `stop`: the lifecycle already has a button by that name, and two
              buttons in one view that read the same and do opposite things is a
              control nobody can trust. */}
          <Button disabled={!snapshot.pending} icon="pause" variant="outline" onClick={onInterrupt}>
            {t('stopWaiting')}
          </Button>
        </div>
      </div>

      {/* The bar is the whole navigation: the interfaces one click to one
          conversation each, and a messaging app one click to its list of names
          below. One bar, not a filter and a list competing for the same row. */}
      <div className="shrink-0 border-b border-[var(--line)] px-4 py-2.5 sm:px-5">
        <ChannelBar
          doors={doors}
          onSelect={onSelectChannel}
          selected={conversation?.channel ?? 'web'}
          t={t}
        />
        {isApp ? (
          <PersonList
            conversations={people}
            onSelect={onSelectConversation}
            selected={conversation?.id ?? null}
            t={t}
          />
        ) : null}
      </div>

      <ConversationFeed
        contentClassName="space-y-4 px-4 py-5 sm:px-6"
        feed={feed}
        label={t('jumpToLatest')}
      >
        {messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--line-strong)] bg-[var(--canvas-raised)] accent-text shadow-emboss">
              <Icon name="spark" size={24} />
            </div>
            {/* An app nobody has written on yet is not the same empty as the
                reader's own line: the hint has to say which of the two it is,
                or a reader waiting for a first message reads a prompt to send
                one into a door with nobody on it. */}
            <p className="max-w-sm text-sm leading-6 text-[var(--ink-muted)]">
              {isApp && people.length === 0 ? t('channelNobody') : t('agentSendHint')}
            </p>
          </div>
        ) : (
          messages.map((message) => {
            const isUser = message.role === 'user';
            /**
             * Who this line is from.
             *
             * The sender the agent worked out, and the conversation's own label
             * when there is none — which is the local line, and is the reader
             * themselves. Never a hard-coded "You" for an arrival: an agent that
             * can be written to from four doors has several people talking to it
             * at once, and a transcript that calls all of them the reader is not
             * a transcript, it is a bug with bubbles.
             */
            const sender = isUser ? (message.author ?? conversation?.label ?? t('you')) : t('assistant');
            return (
              <div className={classNames('flex gap-3', isUser ? 'justify-end' : 'justify-start')} key={message.id}>
                {!isUser ? (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)]">
                    <Icon name="cpu" size={15} />
                  </div>
                ) : null}
                <div className="max-w-[min(86%,680px)]">
                  <div className={classNames('mb-1 flex items-center gap-2 text-[0.65rem] text-[var(--ink-faint)]', isUser ? 'justify-end' : 'justify-start')}>
                    <span className={classNames('truncate', isUser && 'max-w-[16rem]')}>{sender}</span>
                    <span>{formatClock(message.createdAt, language)}</span>
                  </div>
                  <div className={classNames(
                    'whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6',
                    isUser
                      ? 'rounded-tr-md border border-[var(--accent-line)] bg-[var(--accent-soft)] text-[var(--ink)]'
                      : 'rounded-tl-md border border-[var(--line)] bg-[var(--canvas-well)] text-[var(--ink)]',
                  )}>
                    {message.text}
                  </div>
                </div>
                {isUser ? (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)]">
                    <Icon name="home" size={15} />
                  </div>
                ) : null}
              </div>
            );
          })
        )}
        {snapshot.pending ? (
          <div className="flex items-center gap-3 text-xs text-[var(--ink-faint)]">
            <div className="flex gap-1 rounded-lg border border-[var(--line)] bg-[var(--canvas-well)] px-3 py-2.5">
              <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
              <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:180ms]" />
              <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:360ms]" />
            </div>
            <span>{t('agentAwaiting')}…</span>
          </div>
        ) : null}
      </ConversationFeed>

      {snapshot.lastError ? (
        <div className="flex shrink-0 items-start gap-2 border-t border-[var(--danger-line)] bg-[var(--danger-soft)] px-4 py-2.5 text-xs leading-5 text-[var(--danger)]" role="alert">
          <Icon className="mt-0.5 shrink-0" name="close" size={14} />
          <span>{snapshot.lastError}</span>
        </div>
      ) : null}

      <Composer
        value={draft}
        onChange={onDraft}
        onSubmit={send}
        onInterrupt={onInterrupt}
        pending={snapshot.pending}
        // Said above the field, and enforced here: a conversation on a door the
        // agent has stopped having can still be read, and a question typed into
        // it would go nowhere while the pane said it had been asked. The same is
        // true of a messaging app with nobody on it yet — a reply has to go to
        // somebody, and "the door" is not an address.
        disabled={conversation !== null && (
          closedChannels.includes(conversation.channel)
          || (conversation.channel !== 'web' && conversation.channel !== 'cli' && !conversation.person)
        )}
        placeholder={t('agentSendHint')}
        sendLabel={t('send')}
      />
    </section>
  );
}

function IntentionsPane({
  view, t, language, now, onClose,
}: {
  view: AgentView;
  t: AgentT;
  language: Language;
  now: number;
  onClose: (id: string) => void;
}) {
  return (
    <div>
      <SectionLabel icon="edit">{t('panelIntentions')}</SectionLabel>
      {view.intentions.length === 0 ? (
        <EmptyNote>{t('intentionsEmpty')}</EmptyNote>
      ) : (
        <ul>
          {view.intentions.map((node) => (
            <IntentionNode depth={0} key={node.id} language={language} node={node} now={now} onClose={onClose} t={t} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ActionsPane({
  view, t, language, onUndo,
}: {
  view: AgentView;
  t: AgentT;
  language: Language;
  onUndo: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <SectionLabel icon="database">{t('panelActions')}</SectionLabel>
      {view.actions.length === 0 ? (
        <EmptyNote>{t('actionsEmpty')}</EmptyNote>
      ) : (
        <ul className="space-y-1">
          {view.actions.map((action) => {
            const tone = actionTone(action.status);
            const expanded = open === action.id;
            return (
              <li className="rounded-lg px-2 py-1.5 transition hover:bg-[var(--canvas-well)]" key={action.id}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={classNames('h-1.5 w-1.5 shrink-0 rounded-full', TONE_DOT[tone], action.status === 'running' && 'signal-pulse')} />
                      <span className="truncate font-mono text-xs text-[var(--ink)]">{action.tool}</span>
                      <span className={classNames('shrink-0 text-[0.65rem]', TONE_TEXT[tone])}>{humanizeState(action.status)}</span>
                    </div>
                    <div className="mt-0.5 text-[0.65rem] text-[var(--ink-faint)]">
                      {formatClock(action.ts_start, language)}
                      {action.thread_id ? ` · ${action.thread_id}` : ''}
                    </div>
                    {action.reason ? (
                      <p className="mt-0.5 text-[0.65rem] leading-4 text-[var(--ink-muted)]">
                        <span className="opacity-70">{t('actionWhy')}:</span> {action.reason}
                      </p>
                    ) : null}
                    {action.args_redacted ? (
                      <button
                        className="mt-1 text-[0.65rem] accent-text underline underline-offset-2"
                        onClick={() => setOpen(expanded ? null : action.id)}
                        type="button"
                      >
                        {t('actionArgs')}
                      </button>
                    ) : null}
                    {expanded && action.args_redacted ? (
                      <pre className="scrollbar-thin mt-1 max-h-40 overflow-auto rounded-md border border-[var(--line)] bg-[var(--canvas-well)] p-2 font-mono text-[0.62rem] leading-4 text-[var(--ink-muted)]">
                        {JSON.stringify(action.args_redacted, null, 2)}
                      </pre>
                    ) : null}
                  </div>
                  {action.undo_ref ? (
                    <Button className="shrink-0 self-center" icon="check" variant="outline" onClick={() => onUndo(action.id)} title={t('actionUndo')}>
                      {t('actionUndo')}
                    </Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ThoughtsPane({ view, t, language }: { view: AgentView; t: AgentT; language: Language }) {
  return (
    <div>
      <SectionLabel icon="spark">{t('panelThoughts')}</SectionLabel>
      {view.thoughts.length === 0 ? (
        <EmptyNote>{t('thoughtsEmpty')}</EmptyNote>
      ) : (
        <ul className="space-y-2">
          {view.thoughts.map((thought) => (
            <li className="border-l-2 border-[var(--line)] pl-3" key={thought.id}>
              <p className="font-mono text-xs leading-5 text-[var(--ink-muted)]">{thought.summary}</p>
              <p className="mt-0.5 text-[0.62rem] text-[var(--ink-faint)]">{formatClock(thought.ts, language)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BudgetPane({ view, t, language }: { view: AgentView; t: AgentT; language: Language }) {
  // The buckets carry their own labels, so the section needs no heading of its
  // own — and repeating "Today" twice on one panel is noise, not structure.
  const caps = view.budget.caps;
  const rows = view.budget.todayByBucket;
  const total = rows.reduce((sum, row) => sum + row.total, 0);
  const dailyCap = caps.daily_usd;
  return (
    <div className="space-y-4">
      <div>
        <SectionLabel icon="activity">{t('panelBudget')}</SectionLabel>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs text-[var(--ink-faint)]">{t('budgetToday')}</span>
          <span className="font-mono text-sm text-[var(--ink)]">{formatUsd(total, language)}</span>
        </div>
        {dailyCap > 0 ? (
          <div className="mt-1.5">
            <SpendBar
              spent={total}
              cap={dailyCap}
              tone={total > dailyCap ? 'danger' : total > dailyCap * 0.75 ? 'warning' : 'accent'}
            />
            <p className="mt-1 text-[0.65rem] text-[var(--ink-faint)]">
              {t('budgetOf', { spent: formatUsd(total, language), cap: formatUsd(dailyCap, language) })}
            </p>
          </div>
        ) : (
          <p className="mt-1 text-[0.65rem] text-[var(--ink-faint)]">{t('budgetNoCap')}</p>
        )}
      </div>

      {rows.length > 0 ? (
        <div className="space-y-2">
          <Divider />
          {rows.map((row) => (
            <div className="mb-2" key={row.bucket}>
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="text-[var(--ink-muted)]">
                  {row.bucket === 'commitment' ? t('budgetCommitment') : t('budgetDiscretionary')}
                </span>
                <span className="font-mono text-[var(--ink)]">{formatUsd(row.total, language)}</span>
              </div>
              <div className="mt-1">
                <SpendBar
                  cap={row.bucket === 'commitment' ? caps.commitment_usd : caps.discretionary_usd}
                  spent={row.total}
                  tone={row.bucket === 'commitment' ? 'accent' : 'warning'}
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {view.budget.byModel.length > 0 ? (
        <div>
          <p className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--ink-faint)]">{t('budgetByModel')}</p>
          <ul className="space-y-1">
            {view.budget.byModel.map((row) => (
              <li className="flex items-baseline justify-between gap-2 text-xs" key={row.model}>
                <span className="truncate text-[var(--ink-muted)]">{row.model}</span>
                <span className="font-mono text-[var(--ink)]">{formatUsd(row.total, language)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {view.budget.daily.length > 1 ? <DailyBars daily={view.budget.daily} language={language} /> : null}
    </div>
  );
}

function DailyBars({ daily, language }: { daily: AgentView['budget']['daily']; language: Language }) {
  const peak = Math.max(...daily.map((row) => row.total), 0.0001);
  return (
    <div className="flex h-16 items-end gap-1">
      {daily.map((row) => (
        <div className="flex flex-1 flex-col items-center gap-1" key={row.day} title={`${row.day} ${formatUsd(row.total, language)}`}>
          <div className="w-full rounded-t bg-[var(--accent)] opacity-70" style={{ height: `${Math.max(2, (row.total / peak) * 44)}px` }} />
          <span className="text-[0.58rem] text-[var(--ink-faint)]">{formatDay(row.day, language)}</span>
        </div>
      ))}
    </div>
  );
}

function GuardianPane({ view, t, language, now }: { view: AgentView; t: AgentT; language: Language; now: number }) {
  const guardian = view.guardian;
  const integrity = guardian.integrity;
  return (
    <div className="space-y-4">
      <div>
        <SectionLabel icon="shield">{t('guardianIntegrity')}</SectionLabel>
        {!integrity ? (
          <p className="text-xs text-[var(--ink-faint)]">{t('guardianIntegrityStale')}</p>
        ) : (
          <div className={classNames(
            'rounded-lg border px-3 py-2 text-xs',
            integrity.ok
              ? 'border-[var(--ok-line)] bg-[var(--ok-soft)] text-[var(--ok)]'
              : 'border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)]',
          )}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{integrity.ok ? t('guardianIntegrityOk') : t('guardianIntegrityFail')}</span>
              <span className="text-[0.65rem] opacity-80">{formatClock(integrity.ran_at, language)}</span>
            </div>
            {integrity.artifact_mismatches.length > 0 ? (
              <p className="mt-1 text-[0.65rem]">{t('guardianMismatches', { count: integrity.artifact_mismatches.length })}</p>
            ) : null}
          </div>
        )}
        {guardian.auditHead ? (
          <p className="mt-2 flex flex-wrap items-baseline justify-between gap-2 text-[0.65rem] text-[var(--ink-faint)]">
            <span>{t('guardianAudit')}</span>
            <span className="font-mono text-[var(--ink-muted)]">
              #{guardian.auditHead.seq ?? '—'} · {(guardian.auditHead.hash ?? '').slice(0, 12)}
            </span>
          </p>
        ) : null}
        <p className="mt-1 text-[0.65rem] text-[var(--ink-faint)]">
          {t('budgetProtected')}: <span className="font-mono text-[var(--ink-muted)]">{guardian.protectedCount}</span>
        </p>
      </div>

      <div>
        <p className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--ink-faint)]">{t('guardianSnapshots')}</p>
        {guardian.snapshots.length === 0 ? (
          <EmptyNote>{t('guardianSnapshotsEmpty')}</EmptyNote>
        ) : (
          <ul className="space-y-1">
            {guardian.snapshots.map((snapshot) => (
              <li className="flex items-baseline justify-between gap-2 text-xs" key={snapshot.name}>
                <span className="truncate text-[var(--ink-muted)]">{snapshot.name}</span>
                <span className="shrink-0 text-[0.62rem] text-[var(--ink-faint)]">{formatClock(snapshot.created_at, language)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--ink-faint)]">{t('guardianTrash')}</p>
        {guardian.trash.length === 0 ? (
          <EmptyNote>{t('guardianTrashEmpty')}</EmptyNote>
        ) : (
          <ul className="space-y-1.5">
            {guardian.trash.map((entry, index) => {
              const retained = entry.retention_until ? Date.parse(entry.retention_until) : Number.NaN;
              const expired = Number.isFinite(retained) && retained <= now;
              return (
                <li className="text-xs" key={`${entry.id}-${index}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[var(--ink-muted)]">{entry.origin}</span>
                    <span className={classNames('shrink-0 text-[0.62rem]', expired ? 'text-[var(--ink-faint)]' : 'text-[var(--warn)]')}>
                      {entry.retention_until ? formatClock(entry.retention_until, language) : t('presenceNever')}
                    </span>
                  </div>
                  {entry.reason ? <p className="text-[0.62rem] text-[var(--ink-faint)]">{entry.reason}</p> : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
