import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Text, useApp } from 'ink';
import {
  controlSummary,
  conversationKey,
  createSettings,
  createTranslator,
  flattenIntentions,
  humanizeState,
  MAX_TERMINAL_TEXT_LENGTH,
  sanitizeTerminalText,
  stateTone,
  type AgentControlAction,
  type AgentConversation,
  type AgentDoor,
  type AgentLinkStatus,
  type AgentView,
  type ClientSnapshot,
  type Settings,
} from '@project-phone/core';
import {
  createPalette,
  lineToneHex,
  toneHex,
  type ColorDepth,
  type Palette,
} from './palette.js';
import { Divider, Notice, StatusPill } from './tui-kit.js';
import { maxScroll, transcriptLines, windowLines } from './transcript.js';
import { statusLabelKey, statusTone } from './render.js';
import {
  PANEL_SCREENS,
  resolveByPrefix,
  runCommand,
  useComposerInput,
  type ComposerActions,
  type ComposerState,
  type Overlay,
  type Screen,
  type Translator,
} from './tui-input.js';
import { useAgentSession } from './agent-session.js';
import { channelBadge } from './channels.js';
import {
  Composer,
  DebugPanel,
  EmptyState,
  ErrorRow,
  Footer,
  HelpOverlay,
  PendingRow,
  useTranscriptScroll,
} from './tui-shared.js';
import {
  ActionsPanel,
  AgentSettingsPanel,
  BudgetPanel,
  ChannelsPanel,
  CycleStrip,
  CycleStripLine,
  GuardianPanel,
  IntentionsPanel,
  panelHeightFor,
  PresencePanel,
  ThoughtsPanel,
  linkLabel,
} from './agent-panels.js';
import type { PhoneConfig } from './config.js';

export interface AgentTuiProps {
  initialConfig: PhoneConfig;
  debug: boolean;
  width: number;
  height: number;
  depth: ColorDepth;
}

const MIN_CHAT_HEIGHT = 5;
/** The instrument column: the web's aside is 23rem, and this is its terminal width. */
const PANEL_WIDTH = 42;
const WIDE_AT = 100;
/** Rows the composer — or its blocked note — and the error banner can take. */
const CHAT_CHROME_ROWS = 7;
const DEBUG_PANEL_ROWS = 8;

const HELP_KEYS: Array<[string, string]> = [
  ['Enter', 'Send the message. The agent decides whether to answer.'],
  ['Tab / Ctrl-P', 'Step through the agent panels and back to the line.'],
  ['PgUp / PgDn', 'Scroll the transcript — or the open panel — by a screen.'],
  ['↑ / ↓', 'Scroll one line.'],
  ['Esc', 'Close a panel, stop waiting, jump to the newest line, or open settings.'],
  ['Ctrl-S', 'Open the setup.'],
  ['Ctrl-K', 'Discard the composer.'],
  ['Ctrl-Q / Ctrl-C', 'Leave the line.'],
];

const HELP_COMMANDS: Array<[string, string]> = [
  ['/help', 'Show or hide this panel.'],
  ['/settings', 'The interface setup, and how to repoint it.'],
  ['/link <url>', 'Point this terminal at a different agent.'],
  ['/name [<name>]', 'What the agent is called, or what to call it.'],
  ['/person <name>', 'The name the agent knows you by.'],
  ['/model [<provider> <model>]', 'What the agent thinks with, or what it should.'],
  ['/channel [<n>|<name>|<door:person>]', 'Which door to talk through, or which conversation to open.'],
  ['/channels', 'Every door the agent has open, and who each can reach.'],
  ['/presence', 'What the agent is doing right now.'],
  ['/intentions', 'What it intends, as a tree.'],
  ['/actions', 'What it has done, and what can be undone.'],
  ['/thoughts', 'What it has been thinking.'],
  ['/budget', 'What it has spent, against its caps.'],
  ['/guardian', 'Trash, snapshots and the audit chain.'],
  ['/hold | /resume', 'Pause everything, or start it again.'],
  ['/preview on | off', 'Let the agent talk only, or give it its tools back.'],
  ['/stop | /emergency', 'Stop the agent; the emergency stop arms first.'],
  ['/undo [<id>]', 'Queue an undo — bare opens the journal, where the ids are.'],
  ['/cancel [<id>]', 'Cancel an intention — bare opens the board.'],
  ['/interrupt', 'Stop waiting for a reply.'],
  ['/theme', 'Switch between dark and light.'],
  ['/lang', 'Cycle the interface language.'],
  ['/quit', 'Leave the line.'],
];

/**
 * The terminal's agent interface.
 *
 * The same conversation, the same turn rules and the same panels as the browser,
 * arranged for a terminal: the transcript and one instrument panel side by side
 * where there is room — the way the browser's screen is built — and one at a time
 * when there is not. The panels are the whole data set and they scroll, so a
 * terminal is a place where the whole record can be read, not a summary of it.
 *
 * It is a layout over `useAgentSession` and the keyboard map in `tui-input.ts` —
 * the decisions live there and in the panels, not here.
 */
export function AgentTui({ initialConfig, debug, width, height, depth }: AgentTuiProps): React.ReactElement {
  const { exit } = useApp();
  const [screen, setScreenRaw] = useState<Screen>('chat');
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [input, setInput] = useState('');
  const [cursor, setCursor] = useState(0);
  // The emergency stop is armed first and fired second, as the browser's
  // two-step button arms it. Only `/emergency confirm` — or a fresh arm —
  // clears it, so a stray keystroke cannot fire it and walking away cannot
  // leave it silently fireable.
  const [armed, setArmed] = useState(false);
  // Every panel shares one place in the scrollback, reset when the reader
  // moves to another panel: arriving at a panel means arriving at its top.
  const [panelScroll, setPanelScroll] = useState(0);
  const { scroll, seek, follow, onSettled } = useTranscriptScroll();

  const session = useAgentSession(initialConfig, debug, onSettled);
  const { client, snapshot, view, link, streaming, config, debugLines, toast, say } = session;
  // The conversation the transcript is narrowed to, and the door replies go out
  // of. Read off the session rather than kept here, so the two can never disagree.
  const { channel, conversationKey: currentKey, conversations } = session;

  const t = useMemo(() => createTranslator(snapshot.settings.language), [snapshot.settings.language]);
  const palette = useMemo(
    () => createPalette(snapshot.settings.accent, snapshot.settings.theme, depth),
    [snapshot.settings.accent, snapshot.settings.theme, depth],
  );

  const wide = width >= WIDE_AT;
  const panelWidth = wide ? PANEL_WIDTH : width;
  const chatWidth = wide ? Math.max(20, width - PANEL_WIDTH - 1) : width;
  const debugRows = debug ? DEBUG_PANEL_ROWS : 0;

  // The chrome is counted, not guessed: a header, its divider, the cycle strip
  // when there is one, the footer — and, on the conversation, the composer (or
  // the note that says why there is none), the arming banner when it stands,
  // and whatever the last turn had to say about itself.
  const hasCycles = view.presence.recentCycles.length > 0;
  const topRows = 2 + (hasCycles ? 1 : 0);
  const bottomRows = 1 + debugRows + (armed ? 1 : 0);
  const panelBodyHeight = panelHeightFor(height - topRows - bottomRows);
  const chatHeight = Math.max(
    MIN_CHAT_HEIGHT,
    height - topRows - bottomRows - CHAT_CHROME_ROWS - (snapshot.lastError ? 1 : 0),
  );

  // The transcript, narrowed to the conversation this terminal is reading —
  // `{door}:{address}`, the same unit the browser's pane keeps.
  //
  // The same control the browser's people list is, and the same double duty: it
  // decides what is drawn *and* where the next reply goes, which is why it lives
  // beside the composer rather than in a settings screen. Narrowing by the door
  // alone would draw several people's arrivals on one door as one stream, which
  // is the cluttered transcript a browser keeps out by narrowing to the pair.
  //
  // Derived rather than stored, because the conversation is state the session
  // already owns and a second copy of it would be one more thing to keep in
  // step. The local line is the reader's own conversation, and showing an empty
  // one to a reader who has just typed something would look like the agent had
  // lost it — their own turn is filed there the moment Enter is pressed.
  const localDoor = channel === 'web' || channel === 'cli';
  const shown = useMemo(
    () => snapshot.messages.filter((message) => conversationKey(message) === currentKey),
    [currentKey, snapshot.messages],
  );

  const lines = useMemo(
    () => transcriptLines(shown, { width: chatWidth - 2, language: snapshot.settings.language }),
    [shown, chatWidth, snapshot.settings.language],
  );
  const visible = windowLines(lines, chatHeight, scroll);
  const limit = maxScroll(lines, chatHeight);

  // A different conversation is a different place in the scrollback, so it opens
  // where that one ends — the same re-anchor the browser's pane keeps. Without
  // this the reader would keep the place they were in another conversation's
  // scrollbar, which is how you end up looking at the oldest message of a
  // conversation that has never scrolled.
  useEffect(() => {
    follow();
  }, [currentKey, follow]);

  const setScreen = useCallback((next: Screen) => {
    setScreenRaw(next);
    setPanelScroll(0);
  }, []);

  const cyclePanel = useCallback((backwards: boolean) => {
    const order: Screen[] = ['chat', ...PANEL_SCREENS];
    const at = order.indexOf(screen);
    if (at === -1) return;
    setScreen(backwards ? order[(at - 1 + order.length) % order.length] : order[(at + 1) % order.length]);
    follow();
  }, [follow, screen, setScreen]);

  const state: ComposerState = {
    input,
    cursor,
    scroll,
    limit,
    chatHeight,
    scrollable: lines.length > chatHeight,
    pending: snapshot.pending,
    screen,
    overlay,
    settings: snapshot.settings,
    armed,
  };

  /**
   * Why the composer is disabled, or `null` when it is not.
   *
   * The same two rules the browser's composer keeps: a conversation on a door
   * the agent no longer has can be read but not written to, and a messaging app
   * with nobody on it has no address for a reply to go to. Saying which of the
   * two it is — in the place the reader is about to type — is the difference
   * between a blocked send and a mystery.
   */
  const nobodyToAnswer = !localDoor && !currentKey.split(':')[1];
  const blockedReason = session.closedChannels.includes(channel)
    ? t('channelClosed')
    : nobodyToAnswer
      ? t('channelNobody')
      : null;

  const actions: ComposerActions = {
    setInput,
    setCursor,
    setScroll: seek,
    setScreen,
    setOverlay,
    submit: (raw: string) => {
      const message = sanitizeTerminalText(raw, MAX_TERMINAL_TEXT_LENGTH).trim();
      if (!message) return;
      setInput('');
      setCursor(0);
      if (runCommand(message, state, actions)) return;
      follow();
      void client.sendMessage(message);
    },
    clearConversation: () => {
      // The transcript is the agent's store. Clearing the local copy would only be
      // undone by the next event, so the honest thing is to decline and say why.
      say(t('agentDeclined'), 'warning');
    },
    interrupt: session.interrupt,
    quit: exit,
    patchSettings: session.patchSettings,
    say,
    agentControl: (action: AgentControlAction) => session.control(action),
    agentUndo: (id: string) => {
      // Bare `/undo` opens the journal: the ids live there, and a reader without
      // one should not have to guess. A typed id may be the short form the panel
      // shows — resolved by prefix, and only when exactly one action answers.
      if (!id) {
        setScreen('actions');
        say(t('cliUndoHint'), 'accent');
        return;
      }
      const found = resolveByPrefix(view.actions.map((entry) => ({ id: entry.id, tool: entry.tool })), id);
      if (!found) {
        say(t('cliAgentNoUndo'), 'warning');
        return;
      }
      session.undoAction(found.id, found.tool);
    },
    agentCancel: (id: string) => {
      if (!id) {
        setScreen('intentions');
        say(t('cliCancelHint'), 'accent');
        return;
      }
      const found = resolveByPrefix(
        flattenIntentions(view.intentions).map((node) => ({ id: node.id, tool: node.title || node.id })),
        id,
      );
      if (!found) {
        say(t('agentRefused'), 'warning');
        return;
      }
      session.closeIntention(found.id, found.tool);
    },
    agentAddress: (field, value) => {
      // The kernel owns the rule and the normaliser: a bad address falls back to
      // the default rather than being stored, and the reader is told what it
      // became instead of being left to discover it on the next reconnect.
      const next = createSettings({ ...client.getSnapshot().settings, ...(field === 'link' ? { agentUrl: value } : { agentPerson: value }) });
      const wanted = field === 'link' ? value : value.trim();
      const got = field === 'link' ? next.agentUrl : next.agentPerson;
      session.patchSettings(field === 'link' ? { agentUrl: value } : { agentPerson: value });
      say(
        got === wanted
          ? t('cliSettingsSaved')
          : t('cliInvalidValue', { field: field === 'link' ? 'agentUrl' : 'agentPerson' }),
        got === wanted ? 'success' : 'warning',
      );
    },
    agentBaseModel: (argument) => {
      void session.setBaseModel(argument, say);
    },
    agentName: (argument) => {
      void session.setAgentName(argument, say);
    },
    agentChannel: (argument) => session.setChannel(argument),
    agentPanelScroll: (delta: number) => {
      setPanelScroll((current) => Math.max(0, current + delta));
    },
    setArmed,
    t,
  };
  useComposerInput(state, actions);

  // Help replaces the view rather than floating over it, so nothing shows through
  // where a modal background would have to be opaque.
  if (overlay === 'help') {
    return (
      <Box flexDirection="column" height={height} paddingX={1} width={width}>
        <HelpOverlay
          commands={HELP_COMMANDS}
          height={height - 2}
          keys={HELP_KEYS}
          keyWidth={26}
          noteKey="agentSendHint"
          palette={palette}
          t={t}
          width={width - 2}
        />
      </Box>
    );
  }

  // On a wide terminal the instrument column is always there — the conversation
  // and one panel, side by side, the way the browser arranges them. On a narrow
  // one a panel takes the whole pane, as it always did.
  const panel = renderPanel({
    screen,
    view,
    palette,
    t,
    settings: snapshot.settings,
    width: panelWidth - 2,
    link,
    streaming,
    revision: config.revision,
    doors: session.doors,
    channel: session.channel,
    closedChannels: session.closedChannels,
    personId: snapshot.settings.agentPerson,
    conversations,
    currentConversation: currentKey,
    scroll: panelScroll,
    bodyHeight: panelBodyHeight,
  });

  return (
    <Box flexDirection="column" paddingX={1} width={width} height={height}>
      <AgentHeader
        link={link}
        palette={palette}
        screen={screen}
        snapshot={snapshot}
        streaming={streaming}
        t={t}
        view={view}
        width={width - 2}
      />
      {hasCycles ? (
        <Box marginTop={0}>
          <CycleStripLine palette={palette} view={view} />
        </Box>
      ) : null}
      <Divider palette={palette} width={width - 2} />
      <Box flexDirection="row" flexGrow={1} marginTop={0}>
        <Box flexDirection="column" flexGrow={1} width={chatWidth}>
          {(!wide && panel) ?? null}
          {(!wide && panel) ? (
            panel
          ) : (
            <Box flexDirection="column" flexGrow={1}>
              {visible.length === 0 ? (
                <EmptyState
                  bodyKey={nobodyToAnswer ? 'channelNobody' : 'agentSendHint'}
                  palette={palette}
                  t={t}
                  titleKey="agentTitle"
                  width={chatWidth - 2}
                />
              ) : (
                visible.map((line, index) => (
                  <Text bold={line.bold} color={lineToneHex(palette, line.tone)} key={`${index}-${line.text.slice(0, 12)}`}>
                    {line.text || ' '}
                  </Text>
                ))
              )}
              {snapshot.pending ? <PendingRow labelKey="agentAwaiting" palette={palette} t={t} /> : null}
            </Box>
          )}
          {snapshot.lastError ? (
            <ErrorRow palette={palette} text={snapshot.lastError} width={chatWidth - 2} />
          ) : null}
        </Box>
        {wide ? (
          <Box flexDirection="column" marginLeft={1} width={PANEL_WIDTH}>
            {panel ?? (
              <AgentSidebar
                bodyHeight={panelBodyHeight}
                config={config}
                link={link}
                palette={palette}
                snapshot={snapshot}
                streaming={streaming}
                t={t}
                view={view}
                width={PANEL_WIDTH}
              />
            )}
          </Box>
        ) : null}
      </Box>
      {debug ? <DebugPanel lines={debugLines} palette={palette} t={t} width={width - 2} /> : null}
      {screen === 'chat' && blockedReason ? (
        <Box borderColor={palette.danger} borderStyle="round" flexDirection="column" paddingX={1} width={width - 2}>
          <Notice palette={palette} tone="warning">{blockedReason}</Notice>
          <Text color={palette.inkFaint}>{'/channels'}</Text>
        </Box>
      ) : (
        <Composer
          badge={channel === 'web' || channel === 'cli' ? undefined : channelBadge(channel)}
          cursor={cursor}
          input={input}
          limit={limit}
          palette={palette}
          scroll={scroll}
          t={t}
          width={width - 2}
        />
      )}
      {armed ? (
        <Box>
          <Notice palette={palette} tone="danger">{t('controlArmEmergency')}</Notice>
        </Box>
      ) : null}
      <Footer
        hints={['/help', screen === 'chat' ? '/presence' : 'Tab', ...((limit > 0 && screen === 'chat') ? [t('cliScrollHint')] : []), 'Ctrl-Q']}
        palette={palette}
        toast={toast}
        width={width - 2}
      />
    </Box>
  );
}

/**
 * One agent panel, or `null` for the conversation.
 */
function renderPanel({
  screen,
  view,
  palette,
  t,
  settings,
  width,
  link,
  streaming,
  revision,
  doors,
  channel,
  closedChannels,
  personId,
  conversations,
  currentConversation,
  scroll,
  bodyHeight,
}: {
  screen: Screen;
  view: AgentView;
  palette: Palette;
  t: Translator;
  settings: Settings;
  width: number;
  link: AgentLinkStatus;
  streaming: boolean;
  revision: number;
  doors: readonly AgentDoor[];
  channel: string;
  closedChannels: readonly string[];
  personId: string;
  conversations: readonly AgentConversation[];
  currentConversation: string;
  scroll: number;
  bodyHeight: number;
}): React.ReactElement | null {
  const language = settings.language;
  const now = Date.now();
  switch (screen) {
    case 'presence':
      return (
        <Box flexDirection="column" flexGrow={1}>
          <PresencePanel language={language} link={link} palette={palette} streaming={streaming} t={t} view={view} width={width} />
          <Box marginTop={1}><CycleStrip palette={palette} t={t} view={view} width={width} /></Box>
          <Box marginTop={1}>
            {/* Presence answers "what is it doing", so the thought stream here is
                the recent tail; the whole record is the thoughts panel. */}
            <ThoughtsPanel height={8} language={language} palette={palette} t={t} view={view} width={width} />
          </Box>
        </Box>
      );
    case 'intentions':
      return <IntentionsPanel height={bodyHeight} language={language} now={now} palette={palette} scroll={scroll} t={t} view={view} width={width} />;
    case 'actions':
      return <ActionsPanel height={bodyHeight} language={language} palette={palette} scroll={scroll} t={t} view={view} width={width} />;
    case 'thoughts':
      return <ThoughtsPanel height={bodyHeight} language={language} palette={palette} scroll={scroll} t={t} view={view} width={width} />;
    case 'budget':
      return <BudgetPanel height={bodyHeight} language={language} palette={palette} scroll={scroll} t={t} view={view} width={width} />;
    case 'guardian':
      return <GuardianPanel height={bodyHeight} language={language} now={now} palette={palette} scroll={scroll} t={t} view={view} width={width} />;
    case 'channels':
      return (
        <ChannelsPanel
          closed={closedChannels}
          conversations={conversations}
          current={channel}
          currentConversation={currentConversation}
          doors={doors}
          height={bodyHeight}
          palette={palette}
          personId={personId}
          scroll={scroll}
          t={t}
          width={width}
        />
      );
    case 'settings':
      return (
        <AgentSettingsPanel
          catalogue={view.catalogue}
          control={view.control}
          identity={view.identity}
          model={view.model}
          palette={palette}
          revision={revision}
          settings={settings}
          t={t}
          width={width}
        />
      );
    default:
      return null;
  }
}

/** The one entry whose id starts with `wanted`, or `null` when none or several do. */
function AgentHeader({
  view, link, streaming, snapshot, screen, palette, t, width,
}: {
  view: AgentView;
  link: AgentLinkStatus;
  streaming: boolean;
  snapshot: ClientSnapshot;
  screen: Screen;
  palette: Palette;
  t: Translator;
  width: number;
}) {
  const summary = controlSummary(view.control, t);
  const tone = stateTone(view.presence.state);
  const panelName: Partial<Record<Screen, string>> = {
    presence: t('presenceLabel'),
    intentions: t('panelIntentions'),
    actions: t('panelActions'),
    thoughts: t('panelThoughts'),
    budget: t('panelBudget'),
    guardian: t('panelGuardian'),
    channels: t('panelChannels'),
  };
  return (
    <Box justifyContent="space-between" width={width}>
      <Box>
        <Text bold color={palette.accent}>{`◉ ${t('agentTitle')}`}</Text>
        <Text color={toneHex(palette, tone)}>{` ${humanizeState(view.presence.state)}`}</Text>
        {view.presence.focus?.title ? (
          <Text color={palette.inkFaint}>{` · ${view.presence.focus.title}`}</Text>
        ) : null}
        {screen !== 'chat' ? <Text color={palette.accent}>{`  ${panelName[screen] ?? ''}`}</Text> : null}
      </Box>
      <Box gap={2}>
        <Text color={summary.tone === 'success' ? palette.inkFaint : summary.tone === 'danger' ? palette.danger : palette.warning}>
          {summary.text}
        </Text>
        <Text color={palette.inkFaint}>{linkLabel(link, streaming, t)}</Text>
        <StatusPill palette={palette} status={statusTone(snapshot.status)}>{t(statusLabelKey(snapshot.status))}</StatusPill>
      </Box>
    </Box>
  );
}

function AgentSidebar({
  view, link, streaming, snapshot, config, palette, t, width, bodyHeight,
}: {
  view: AgentView;
  link: AgentLinkStatus;
  streaming: boolean;
  snapshot: ClientSnapshot;
  config: PhoneConfig;
  palette: Palette;
  t: Translator;
  width: number;
  bodyHeight: number;
}) {
  const language = snapshot.settings.language;
  return (
    <Box flexDirection="column" width={width}>
      <PresencePanel language={language} link={link} palette={palette} streaming={streaming} t={t} view={view} width={width - 2} />
      <Box marginTop={1}>
        <BudgetPanel
          height={Math.max(4, bodyHeight - 8)}
          language={language}
          palette={palette}
          t={t}
          view={view}
          width={width - 2}
        />
      </Box>
      <Box flexDirection="column" marginTop={1}>
        <Text bold color={palette.inkFaint}>{t('cliAgentLink').toUpperCase()}</Text>
        <Text color={palette.inkMuted} wrap="truncate">{snapshot.settings.agentUrl}</Text>
        <Text color={palette.inkFaint} wrap="truncate">
          {`${t('agentPerson')}: ${snapshot.settings.agentPerson}`}
        </Text>
        <Text color={palette.inkFaint} wrap="truncate">{`${t('syncRevision')}: ${config.revision}`}</Text>
      </Box>
    </Box>
  );
}
