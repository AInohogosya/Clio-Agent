import { useEffect, useMemo, useState } from 'react';
import {
  conversationKey,
  createTranslator,
  flattenIntentions,
  toChatMessages,
  type AgentApproval,
  type AgentControlAction,
  type AgentConversation,
  type AgentDoor,
  type AgentHistoryPage,
  type AgentView,
  type ClientSnapshot,
  type Language,
  type TranslationKey,
} from '@project-phone/core';
import { Composer } from '../components/Composer';
import { BackgroundScreenPane } from '../components/agent/BackgroundScreenPane';
import { MessageRow, PendingIndicator } from '../components/MessageRow.js';
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

type Panel = 'intentions' | 'actions' | 'thoughts' | 'budget' | 'guardian' | 'screen' | 'approvals';

const PANELS: Array<{ id: Panel; label: TranslationKey; icon: 'edit' | 'database' | 'spark' | 'activity' | 'shield' | 'cpu' }> = [
  { id: 'intentions', label: 'panelIntentions', icon: 'edit' },
  { id: 'actions', label: 'panelActions', icon: 'database' },
  { id: 'thoughts', label: 'panelThoughts', icon: 'spark' },
  { id: 'screen', label: 'panelScreen', icon: 'cpu' },
  { id: 'budget', label: 'panelBudget', icon: 'activity' },
  { id: 'guardian', label: 'panelGuardian', icon: 'shield' },
  { id: 'approvals', label: 'panelApprovals', icon: 'shield' },
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
  /** Approves or denies a command the gate is holding for a person. */
  onDecideApproval: (id: string, verb: 'approve' | 'deny') => void;
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
  /** Past messages read retroactively, oldest first. */
  history: AgentHistoryPage;
  /** True while a page of history is being fetched. */
  historyLoading: boolean;
  /** Asks for the page before the oldest message currently shown. */
  onLoadOlderHistory: () => void;
  /** What the timeline is showing instead of a page, when it shows a search. */
  historyQuery: string | null;
  /** Searches the past; an empty query restores the page view. */
  onSearchHistory: (query: string) => void;
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
  onDecideApproval,
  onRefresh,
  onOpenSettings,
  conversations,
  doors,
  closedChannels,
  conversation,
  onSelectConversation,
  history,
  historyLoading,
  onLoadOlderHistory,
  historyQuery,
  onSearchHistory,
}: AgentScreenProps) {
  const language = snapshot.settings.language;
  const t = useMemo(() => createTranslator(language), [language]);
  const [panel, setPanel] = useState<Panel>('intentions');
  const [draft, setDraft] = useState('');
  const now = Date.now();
  // The reader's own arrangements, read off the settings: whether the
  // instrument column stands beside the conversation, and how much room a
  // turn takes.
  const showSidebar = snapshot.settings.showSidebar;
  const compact = snapshot.settings.density === 'compact';

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
      <header className="shrink-0 border-b border-[var(--line)] px-4 py-3 sm:px-6" style={{ background: 'var(--glass)', backdropFilter: 'blur(14px)' }}>
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

      <div className={classNames(
        'mx-auto grid w-full max-w-[1600px] min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden px-4 pb-4 pt-4',
        showSidebar && 'lg:grid-cols-[minmax(0,1fr)_23rem]',
      )}>
        <TimelinePane
          closedChannels={closedChannels}
          conversation={current}
          conversations={people}
          doors={doors}
          draft={draft}
          history={history}
          historyLoading={historyLoading}
          historyQuery={historyQuery}
          language={language}
          onDecideApproval={onDecideApproval}
          onDraft={setDraft}
          onInterrupt={onInterrupt}
          onLoadOlderHistory={onLoadOlderHistory}
          onSearchHistory={onSearchHistory}
          onSelectChannel={selectChannel}
          onSelectConversation={onSelectConversation}
          onSubmit={submit}
          approvals={view.approvals}
          snapshot={snapshot}
          t={t}
        />

        {showSidebar ? (
          <aside className="hidden min-h-0 flex-col gap-3 lg:flex">
            {/* One line, scrollable rather than wrapped: a tab row that reflows
                changes which panel a reader is looking at when the window is
                resized, and a control that moves is a control that is missed. */}
            <div className="instrument-panel scrollbar-thin flex shrink-0 gap-1.5 overflow-x-auto rounded-2xl p-2" role="group">
              {PANELS.map((entry) => (
                <button
                  aria-pressed={panel === entry.id}
                  className={classNames(
                    'instrument-button inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border px-3 py-2 text-[0.72rem] font-bold transition',
                    panel === entry.id
                      ? 'accent-border bg-[var(--accent-soft)] accent-text shadow-sm'
                      : 'border-transparent bg-transparent text-[var(--ink-faint)] hover:bg-[var(--canvas-well)] hover:text-[var(--ink-muted)]',
                  )}
                  key={entry.id}
                  onClick={() => setPanel(entry.id)}
                  type="button"
                >
                  <Icon name={entry.icon} size={13} />
                  <span>{t(entry.label)}</span>
                </button>
              ))}
            </div>
            <section className="instrument-panel scrollbar-thin min-h-0 flex-1 overflow-y-auto rounded-3xl p-5">
              {panel === 'intentions' ? (
                <IntentionsPane language={language} now={now} onClose={onCloseIntention} t={t} view={view} />
              ) : null}
              {panel === 'actions' ? <ActionsPane language={language} onUndo={onUndo} t={t} view={view} /> : null}
              {panel === 'thoughts' ? <ThoughtsPane language={language} t={t} view={view} /> : null}
              {panel === 'screen' ? <BackgroundScreenPane language={language} t={t} /> : null}
              {panel === 'budget' ? <BudgetPane language={language} t={t} view={view} /> : null}
              {panel === 'guardian' ? <GuardianPane language={language} now={now} t={t} view={view} /> : null}
              {panel === 'approvals' ? (
                <ApprovalsPane onDecide={onDecideApproval} t={t} view={view} />
              ) : null}
            </section>
          </aside>
        ) : null}
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
    const title = flattenIntentions(view.intentions).find((node) => node.id === id)?.title;
    body = title ? t('intentionCloseQueued', { title }) : t('controlQueued', { action: id });
  }
  return (
    <p className="mx-auto mt-2 w-full max-w-[1600px] text-xs text-[var(--ok)]">
      <span className="mr-1.5">✓</span>
      {body}
    </p>
  );
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
  // Pause and resume are one decision read two ways, so the header carries one
  // button that changes with the state rather than both, one of them disabled.
  // Three live controls at rest — pause, stop, and the armed emergency — each
  // of which does something now, instead of a row of half-dead buttons.
  const dormant = control.paused || control.stopped;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {dormant ? (
        // The way back is the primary control when the agent is dormant: the
        // reader who paused it is the reader most likely to be unpausing it.
        <Button disabled={busy} icon="play" variant="primary" onClick={() => onControl('resume')} title={t('controlResume')}>
          {t('controlResume')}
        </Button>
      ) : (
        <Button disabled={busy} icon="pause" variant="outline" onClick={() => onControl('pause_all')} title={t('controlPauseAll')}>
          {t('controlPauseAll')}
        </Button>
      )}
      {/* Pause-actions is a different decision from pause-everything — the agent
          stays talkative and drops its tools — so it keeps its own button. It
          was the one lifecycle verb the terminal had and the browser did not. */}
      {!dormant ? (
        <Button
          disabled={busy}
          icon={control.pause_actions ? 'play' : 'pause'}
          variant={control.pause_actions ? 'primary' : 'outline'}
          onClick={() => onControl(control.pause_actions ? 'resume' : 'pause_actions')}
          title={control.pause_actions ? t('controlResume') : t('controlPauseActions')}
        >
          {control.pause_actions ? t('controlResume') : t('controlPauseActions')}
        </Button>
      ) : null}
      <Button
        disabled={busy || control.stopped}
        icon="stop"
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
  history, historyLoading, onLoadOlderHistory, historyQuery, onSearchHistory,
  approvals, onDecideApproval,
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
  history: AgentHistoryPage;
  historyLoading: boolean;
  onLoadOlderHistory: () => void;
  historyQuery: string | null;
  onSearchHistory: (query: string) => void;
  /** Commands the guardian is holding for the person's decision. */
  approvals: readonly AgentApproval[];
  /** Approves or denies one held command. */
  onDecideApproval: (id: string, verb: 'approve' | 'deny') => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const decide = (id: string, verb: 'approve' | 'deny') => {
    setBusy(id);
    try {
      onDecideApproval(id, verb);
    } finally {
      setBusy(null);
    }
  };
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
  /**
   * The past, when it has been asked for.
   *
   * The live timeline is the snapshot's last hundred rows across every door,
   * which is the conversation and not its history. Pages read retroactively are
   * drawn above it — same narrowing, same conversation key — and deduplicated
   * against the live rows by id, because the snapshot moves while a fetched page
   * does not and the same message must never be drawn twice.
   */
  const olderMessages = useMemo(
    () => toChatMessages(history.messages)
      .filter((message) => conversation && conversationKey(message) === conversation.id)
      .filter((message) => !messages.some((live) => live.id === message.id)),
    [history, conversation, messages],
  );
  // The search box: one query, remembered until it is cleared or the
  // conversation changes. A search result page replaces the "load older"
  // stack in the same pane, so the two roads into the past draw identically.
  const [searchDraft, setSearchDraft] = useState('');
  const search = () => onSearchHistory(searchDraft.trim());
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
    <section className="instrument-panel flex min-h-[22rem] min-w-0 flex-col overflow-hidden rounded-3xl lg:min-h-0" aria-label={t('panelTimeline')}>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-[var(--line)] px-5 py-3.5 sm:px-6">
        <SectionLabel icon="command">{t('panelTimeline')}</SectionLabel>
        <div className="flex items-center gap-2">
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              search();
            }}
          >
            <input
              aria-label={t('historySearchTitle')}
              className="w-36 rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)] px-2.5 py-1.5 text-xs text-[var(--ink)] outline-none transition placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)]"
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder={t('historySearchTitle')}
              value={searchDraft}
            />
            <Button icon="database" variant="outline" onClick={search}>
              {t('historySearchTitle')}
            </Button>
          </form>
          {historyQuery ? (
            <Button icon="close" variant="outline" onClick={() => { setSearchDraft(''); onSearchHistory(''); }} title={t('controlCancel')}>
              {t('historySearchTitle')}: {historyQuery}
            </Button>
          ) : null}
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
        contentClassName={snapshot.settings.density === 'compact'
          ? 'space-y-2 px-4 py-3 sm:px-6'
          : 'space-y-4 px-4 py-5 sm:px-6'}
        feed={feed}
        label={t('jumpToLatest')}
      >
        {(() => {
          // The control offers itself before any page has been fetched — that is
          // the only way to ask for the first one — and leaves once a fetch has
          // come back saying there is nothing older. A conversation with no
          // messages at all has no cursor to page from, so it offers nothing —
          // and a search result is a view, not a place in the scrollback, so the
          // paging button stands down while one is showing.
          const atEnd = (history.messages.length > 0 && !history.hasMore) || historyQuery !== null;
          const hasSomething = messages.length > 0 || olderMessages.length > 0;
          return conversation && hasSomething && !atEnd ? (
            <div className="flex justify-center pb-1">
              <Button
                disabled={historyLoading}
                icon="arrowDown"
                variant="quiet"
                onClick={onLoadOlderHistory}
              >
                {historyLoading ? t('historyLoading') : t('historyLoadOlder')}
              </Button>
            </div>
          ) : null;
        })()}
        {olderMessages.map((message) => {
          const isUser = message.role === 'user';
          const sender = isUser ? (message.author ?? conversation?.label ?? t('you')) : t('assistant');
          return (
            <MessageRow
              clock={formatClock(message.createdAt, language)}
              density={snapshot.settings.density}
              key={message.id}
              message={message}
              sender={sender}
              showTimestamps={snapshot.settings.showTimestamps}
              truncateSender
            />
          );
        })}
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
              <MessageRow
                clock={formatClock(message.createdAt, language)}
                density={snapshot.settings.density}
                key={message.id}
                message={message}
                sender={sender}
                showTimestamps={snapshot.settings.showTimestamps}
                truncateSender
              />
            );
          })
        )}
        {snapshot.pending ? (
          <PendingIndicator label={`${t('agentAwaiting')}…`} />
        ) : null}
      </ConversationFeed>

      {snapshot.lastError ? (
        <div className="flex shrink-0 items-start gap-2 border-t border-[var(--danger-line)] bg-[var(--danger-soft)] px-4 py-2.5 text-xs leading-5 text-[var(--danger)]" role="alert">
          <Icon className="mt-0.5 shrink-0" name="close" size={14} />
          <span>{snapshot.lastError}</span>
        </div>
      ) : null}

      {approvals.length > 0 ? (
        <div className="mb-2 flex shrink-0 items-start gap-2 rounded-xl border border-[var(--warn-line)] bg-[var(--warn-soft)] px-3 py-2.5 text-xs leading-5 text-[var(--warn)]" role="alert">
          <Icon className="mt-0.5 shrink-0" name="shield" size={14} />
          <span className="flex-1 min-w-0">
            <span className="font-semibold leading-5">{t('approvalsAwaiting', { count: approvals.length })}</span>
          </span>
          <div className="flex shrink-0 gap-1 flex-wrap">
            {approvals.map((approval) => (
              <div key={approval.id} className="flex items-center gap-1">
                <Button
                  disabled={busy === approval.id}
                  variant="primary"
                  className="text-[0.65rem] px-2 py-1.5"
                  onClick={() => decide(approval.id, 'approve')}
                >
                  {t('approvalApprove')}
                </Button>
                <Button
                  disabled={busy === approval.id}
                  variant="outline"
                  className="text-[0.65rem] px-2 py-1.5"
                  onClick={() => decide(approval.id, 'deny')}
                >
                  {t('approvalDeny')}
                </Button>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <Composer
        value={draft}
        onChange={onDraft}
        onSubmit={send}
        onInterrupt={onInterrupt}
        pending={snapshot.pending}
        enterToSend={snapshot.settings.enterToSend}
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
  // No heading of its own: the tab row above already names the open pane, and
  // a second copy of the same word is height spent saying nothing.
  return (
    <div>
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
                {/* A zero here means the route is priced at zero, and $0.00
                    reads like a malfunction or a rounding artifact. Saying
                    "free" is the fact; it is also the answer to the question
                    the pane exists for. */}
                <span className="font-mono text-[var(--ink)]">{row.total > 0 ? formatUsd(row.total, language) : t('budgetFree')}</span>
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

/**
 * Commands the guardian is holding for a person's decision.
 *
 * The terminal's twin reads the same snapshot field. Each row names the tool,
 * the reason the gate held it, and — expanded — the redacted arguments, so a
 * decision is made with the command in front of the reader rather than a tool
 * name alone. Approve runs it; deny journals it as rejected; both are one
 * click, and the next snapshot shows the list without the row.
 */
function ApprovalsPane({
  view, t, onDecide,
}: {
  view: AgentView;
  t: AgentT;
  onDecide: (id: string, verb: 'approve' | 'deny') => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const decide = (id: string, verb: 'approve' | 'deny') => {
    setBusy(id);
    try {
      onDecide(id, verb);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div>
      {view.approvals.length === 0 ? (
        <EmptyNote>{t('approvalsEmpty')}</EmptyNote>
      ) : (
        <>
          <p className="mb-3 text-[0.65rem] leading-4 text-[var(--ink-faint)]">{t('approvalsHint')}</p>
          <ul className="space-y-2">
            {view.approvals.map((approval) => {
              const expanded = open === approval.id;
              return (
                <li className="rounded-xl border border-[var(--line)] px-3 py-2" key={approval.id}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--warn)] signal-pulse" />
                        <span className="truncate font-mono text-xs text-[var(--ink)]">{approval.tool}</span>
                        {approval.safety_level ? (
                          <span className="shrink-0 text-[0.62rem] text-[var(--ink-faint)]">L{approval.safety_level}</span>
                        ) : null}
                      </div>
                      <p className="mt-0.5 text-[0.65rem] leading-4 text-[var(--ink-muted)]">{approval.reason}</p>
                      {approval.args ? (
                        <button
                          className="mt-1 text-[0.65rem] accent-text underline underline-offset-2"
                          onClick={() => setOpen(expanded ? null : approval.id)}
                          type="button"
                        >
                          {t('actionArgs')}
                        </button>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 gap-1.5">
                      <Button
                        disabled={busy === approval.id}
                        icon="play"
                        variant="primary"
                        onClick={() => decide(approval.id, 'approve')}
                        title={t('approvalApprove')}
                      >
                        {t('approvalApprove')}
                      </Button>
                      <Button
                        disabled={busy === approval.id}
                        icon="close"
                        variant="outline"
                        onClick={() => decide(approval.id, 'deny')}
                        title={t('approvalDeny')}
                      >
                        {t('approvalDeny')}
                      </Button>
                    </div>
                  </div>
                  {expanded && approval.args ? (
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-[var(--canvas-well)] p-2 text-[0.62rem] leading-4 text-[var(--ink-muted)]">
                      {JSON.stringify(approval.args, null, 2)}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
