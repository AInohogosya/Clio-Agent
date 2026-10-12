import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Text, useApp } from 'ink';
import {
  controlSummary,
  conversationKey,
  createSettings,
  createTranslator,
  flattenIntentions,
  MAX_TERMINAL_TEXT_LENGTH,
  sanitizeTerminalText,
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
  type ColorDepth,
  type Palette,
} from './palette.js';
import { Divider, StatusPill } from './tui-kit.js';
import { maxScroll, transcriptLines, windowLines } from './transcript.js';
import { statusLabelKey, statusTone } from './render.js';
import {
  PANEL_SCREENS,
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
  GuardianPanel,
  IntentionsPanel,
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
const SIDEBAR_WIDTH = 34;
const WIDE_AT = 100;
/** Rows the composer, its hint and the error banner can take from the pane. */
const CHROME_ROWS = 6;
const DEBUG_PANEL_ROWS = 8;

const HELP_KEYS: Array<[string, string]> = [
  ['Enter', 'Send the message. The agent decides whether to answer.'],
  ['Tab / Ctrl-P', 'Step through the agent panels and back to the line.'],
  ['PgUp / PgDn', 'Scroll the transcript by a screen.'],
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
  ['/channel [<name>|<door:person>]', 'Which door to talk through, or which conversation to open.'],
  ['/channels', 'Every door the agent has open, and who each can reach.'],
  ['/presence', 'What the agent is doing right now.'],
  ['/intentions', 'What it intends, as a tree.'],
  ['/actions', 'What it has done, and what can be undone.'],
  ['/budget', 'What it has spent, against its caps.'],
  ['/guardian', 'Trash, snapshots and the audit chain.'],
  ['/hold | /resume', 'Pause everything, or start it again.'],
  ['/preview on | off', 'Let the agent talk only, or give it its tools back.'],
  ['/stop | /emergency', 'Stop the agent, or stop it and raise the flag.'],
  ['/undo <action-id>', 'Queue an undo for a journalled action.'],
  ['/cancel <intention-id>', 'Ask the agent to cancel an intention.'],
  ['/interrupt', 'Stop waiting for a reply.'],
  ['/theme', 'Switch between dark and light.'],
  ['/lang', 'Cycle the interface language.'],
  ['/quit', 'Leave the line.'],
];

/**
 * The terminal's agent interface.
 *
 * The same conversation, the same turn rules and the same panels as the browser,
 * arranged for a terminal: the transcript down the middle, presence and the
 * budget in a sidebar when there is room, and one panel at a time when there is
 * not. It is a layout over `useAgentSession` and the keyboard map in
 * `tui-input.ts` — the decisions live there and in the panels, not here.
 */
export function AgentTui({ initialConfig, debug, width, height, depth }: AgentTuiProps): React.ReactElement {
  const { exit } = useApp();
  const [screen, setScreen] = useState<Screen>('chat');
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [input, setInput] = useState('');
  const [cursor, setCursor] = useState(0);
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

  const sidebar = width >= WIDE_AT;
  const sidebarWidth = sidebar ? SIDEBAR_WIDTH : 0;
  const chatWidth = Math.max(20, width - sidebarWidth - (sidebar ? 1 : 0));
  const debugRows = debug ? DEBUG_PANEL_ROWS : 0;
  const chatHeight = Math.max(MIN_CHAT_HEIGHT, height - CHROME_ROWS - debugRows);

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

  const cyclePanel = useCallback((backwards: boolean) => {
    const order: Screen[] = ['chat', ...PANEL_SCREENS];
    const at = order.indexOf(screen);
    if (at === -1) return;
    setScreen(backwards ? order[(at - 1 + order.length) % order.length] : order[(at + 1) % order.length]);
    follow();
  }, [follow, screen]);

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
  };

  const actions: ComposerActions = {
    setInput,
    setCursor,
    setScroll: seek,
    setScreen,
    setOverlay,
    cyclePanel,
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
      const action = view.actions.find((entry) => entry.id === id);
      session.undoAction(id, action?.tool ?? id);
    },
    agentCancel: (id: string) => {
      const node = flattenIntentions(view.intentions).find((entry) => entry.id === id);
      session.closeIntention(id, node?.title ?? id);
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

  const panel = renderPanel(
    screen,
    view,
    palette,
    t,
    snapshot.settings,
    width - 2,
    session.link,
    streaming,
    config.revision,
    session.doors,
    session.channel,
    session.closedChannels,
    snapshot.settings.agentPerson,
    conversations,
    currentKey,
  );

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
      <Divider palette={palette} width={width - 2} />
      <Box flexDirection="row" flexGrow={1} marginTop={0}>
        <Box flexDirection="column" flexGrow={1} width={chatWidth}>
          {panel ?? (
            <Box flexDirection="column" flexGrow={1}>
              {visible.length === 0 ? (
                <EmptyState bodyKey="agentSendHint" palette={palette} t={t} titleKey="agentTitle" width={chatWidth - 2} />
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
        {sidebar && !panel ? (
          <AgentSidebar
            config={config}
            link={link}
            palette={palette}
            snapshot={snapshot}
            streaming={streaming}
            t={t}
            view={view}
            width={sidebarWidth}
          />
        ) : null}
      </Box>
      {debug ? <DebugPanel lines={debugLines} palette={palette} t={t} width={width - 2} /> : null}
      {screen === 'chat' ? (
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
 * One agent panel, filling the pane, or `null` for the conversation.
 *
 * The composer is hidden on a panel: a panel is read, and a keystroke that landed
 * in a composer the reader cannot see would be a message they never wrote.
 */
function renderPanel(
  screen: Screen,
  view: AgentView,
  palette: Palette,
  t: Translator,
  settings: Settings,
  width: number,
  link: AgentLinkStatus,
  streaming: boolean,
  revision: number,
  doors: readonly AgentDoor[],
  channel: string,
  closedChannels: readonly string[],
  personId: string,
  conversations: readonly AgentConversation[],
  currentConversation: string,
): React.ReactElement | null {
  const language = settings.language;
  const now = Date.now();
  switch (screen) {
    case 'presence':
      return (
        <Box flexDirection="column" flexGrow={1}>
          <PresencePanel language={language} link={link} palette={palette} streaming={streaming} t={t} view={view} width={width} />
          <Box marginTop={1}><CycleStrip palette={palette} t={t} view={view} width={width} /></Box>
          <Box marginTop={1}><ThoughtsPanel language={language} palette={palette} t={t} view={view} width={width} /></Box>
        </Box>
      );
    case 'intentions':
      return <IntentionsPanel language={language} now={now} palette={palette} t={t} view={view} width={width} />;
    case 'actions':
      return <ActionsPanel language={language} palette={palette} t={t} view={view} width={width} />;
    case 'budget':
      return <BudgetPanel language={language} palette={palette} t={t} view={view} width={width} />;
    case 'guardian':
      return <GuardianPanel language={language} now={now} palette={palette} t={t} view={view} width={width} />;
    case 'channels':
      return (
        <ChannelsPanel
          closed={closedChannels}
          conversations={conversations}
          current={channel}
          currentConversation={currentConversation}
          doors={doors}
          palette={palette}
          personId={personId}
          t={t}
          width={width}
        />
      );
    case 'settings':
      return (
        <AgentSettingsPanel
          catalogue={view.catalogue}
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
  const panelName: Partial<Record<Screen, string>> = {
    presence: t('presenceLabel'),
    intentions: t('panelIntentions'),
    actions: t('panelActions'),
    budget: t('panelBudget'),
    guardian: t('panelGuardian'),
    channels: t('panelChannels'),
  };
  return (
    <Box justifyContent="space-between" width={width}>
      <Box>
        <Text bold color={palette.accent}>{`◉ ${t('agentTitle')}`}</Text>
        {screen !== 'chat' ? <Text color={palette.accent}>{`  ${panelName[screen] ?? ''}`}</Text> : null}
        <Text color={palette.inkFaint}>{`  ${linkLabel(link, streaming, t)}`}</Text>
      </Box>
      <Box gap={2}>
        <Text color={summary.tone === 'success' ? palette.inkFaint : summary.tone === 'danger' ? palette.danger : palette.warning}>
          {summary.text}
        </Text>
        <StatusPill palette={palette} status={statusTone(snapshot.status)}>{t(statusLabelKey(snapshot.status))}</StatusPill>
      </Box>
    </Box>
  );
}

function AgentSidebar({
  view, link, streaming, snapshot, config, palette, t, width,
}: {
  view: AgentView;
  link: AgentLinkStatus;
  streaming: boolean;
  snapshot: ClientSnapshot;
  config: PhoneConfig;
  palette: Palette;
  t: Translator;
  width: number;
}) {
  const language = snapshot.settings.language;
  return (
    <Box flexDirection="column" marginLeft={1} width={width}>
      <PresencePanel language={language} link={link} palette={palette} streaming={streaming} t={t} view={view} width={width} />
      <Box marginTop={1}>
        <BudgetPanel language={language} palette={palette} t={t} view={view} width={width} />
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
