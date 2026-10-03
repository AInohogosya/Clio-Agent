import React, { useCallback, useMemo, useRef, useState } from 'react';
import { Box, Text, useApp } from 'ink';
import {
  createTranslator,
  MAX_TERMINAL_TEXT_LENGTH,
  sanitizeTerminalText,
  type ClientSnapshot,
  type Settings,
} from '@project-phone/core';
import {
  createPalette,
  displayWidth,
  lineToneHex,
  truncateWidth,
  type ColorDepth,
  type Palette,
  type Tone,
} from './palette.js';
import { Divider, Hint, Meta, Notice, Panel, Spinner, StatusPill } from './tui-kit.js';
import { maxScroll, transcriptLines, windowLines } from './transcript.js';
import { statusLabelKey, statusTone, wrapText } from './render.js';
import { runCommand, useComposerInput, type ComposerActions, type ComposerState, type Overlay, type Screen, type Translator } from './tui-input.js';
import { usePhoneSession, type Toast } from './tui-session.js';
import { SettingsWizard } from './wizard.js';
import { hasEnvironmentApiKey, type PhoneConfig } from './config.js';

export type LaunchMode = 'chat' | 'setup';

export interface TuiProps {
  initialConfig: PhoneConfig;
  debug: boolean;
  mode: LaunchMode;
  width: number;
  height: number;
  depth: ColorDepth;
}

const MIN_CHAT_HEIGHT = 5;
const SIDEBAR_WIDTH = 32;
const WIDE_AT = 94;
/** Rows the composer, its hint and the error banner can take from the pane. */
const CHROME_ROWS = 6;

/**
 * The full-screen chat surface.
 *
 * This component is layout and wiring only. The state it draws from lives in
 * `usePhoneSession` (the client and the shared file) and the keyboard lives in
 * `tui-input.ts` (the keymap and the slash commands), so what remains here is
 * the arrangement of panes and the handful of calls between them.
 */
export function Tui({ initialConfig, debug, mode, width, height, depth }: TuiProps): React.ReactElement {
  const { exit } = useApp();
  const [screen, setScreen] = useState<Screen>(mode === 'setup' ? 'settings' : 'chat');
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [input, setInput] = useState('');
  const [cursor, setCursor] = useState(0);
  const [scroll, setScroll] = useState(0);
  // The scroll position lives in a ref as well as in state, because the keymap
  // needs the current value inside a callback whose identity must not change
  // every keystroke — a handler rebuilt on each render is a handler that can
  // miss the keystroke that arrived during the rebuild.
  const scrollRef = useRef(0);
  // Whether the reader chose to follow the conversation. Cleared when they
  // scroll back, restored when they return to the newest line — so arriving
  // tokens never move a view the reader has deliberately parked.
  const following = useRef(true);

  const follow = useCallback(() => {
    following.current = true;
    scrollRef.current = 0;
    setScroll(0);
  }, []);
  const seek = useCallback((next: number | ((current: number) => number)) => {
    const resolved = typeof next === 'function' ? next(scrollRef.current) : next;
    scrollRef.current = resolved;
    following.current = resolved === 0;
    setScroll(resolved);
  }, []);

  const onSettled = useCallback(() => {
    if (following.current) follow();
  }, [follow]);

  const session = usePhoneSession(initialConfig, debug, onSettled);
  const { client, snapshot, config, debugLines, toast, say } = session;

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

  const lines = useMemo(
    () => transcriptLines(snapshot.messages, { width: chatWidth - 2, language: snapshot.settings.language }),
    [snapshot.messages, chatWidth, snapshot.settings.language],
  );
  const visible = windowLines(lines, chatHeight, scroll);
  const limit = maxScroll(lines, chatHeight);

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
    submit: (raw: string) => {
      const message = sanitizeTerminalText(raw, MAX_TERMINAL_TEXT_LENGTH).trim();
      if (!message) return;
      setInput('');
      setCursor(0);
      if (runCommand(message, state, actions)) return;
      follow();
      void client.sendMessage(message);
    },
    clearConversation: session.clearConversation,
    interrupt: session.interrupt,
    quit: exit,
    patchSettings: (patch: Partial<Settings>) => {
      session.patchSettings(patch);
      say(t('cliSettingsSaved'), 'success');
    },
    say,
    t,
  };
  useComposerInput(state, actions);

  // Help replaces the view rather than floating over it, so nothing shows
  // through where a modal background would have to be opaque.
  if (overlay === 'help') {
    return (
      <Box flexDirection="column" height={height} paddingX={1} width={width}>
        <HelpOverlay palette={palette} t={t} width={width - 2} height={height - 2} />
      </Box>
    );
  }

  if (screen === 'settings') {
    return (
      <Box flexDirection="column" paddingX={1} width={width} height={height}>
        <SettingsWizard
          depth={depth}
          height={height - 2}
          onCancel={() => {
            if (mode === 'setup') exit();
            else setScreen('chat');
          }}
          onSave={(next) => {
            client.setSettings(next);
            setScreen('chat');
            say(t('cliSavedShared'), 'success');
            if (mode === 'setup') {
              setTimeout(exit, 420);
            }
          }}
          settings={snapshot.settings}
          width={width - 2}
        />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1} width={width} height={height}>
      <Header palette={palette} snapshot={snapshot} state={config} t={t} width={width - 2} />
      <Divider palette={palette} width={width - 2} />
      <Box flexDirection="row" flexGrow={1} marginTop={0}>
        <Box flexDirection="column" flexGrow={1} width={chatWidth}>
          <Box flexDirection="column" flexGrow={1}>
            {visible.length === 0 ? (
              <EmptyState palette={palette} t={t} width={chatWidth - 2} />
            ) : (
              visible.map((line, index) => (
                <Text
                  bold={line.bold}
                  color={lineToneHex(palette, line.tone)}
                  key={`${index}-${line.text.slice(0, 12)}`}
                >
                  {line.text || ' '}
                </Text>
              ))
            )}
            {snapshot.streamingText ? (
              <StreamingReply palette={palette} text={snapshot.streamingText} t={t} width={chatWidth - 2} />
            ) : snapshot.pending ? (
              <PendingRow palette={palette} t={t} />
            ) : null}
          </Box>
          {snapshot.lastError ? (
            <ErrorRow palette={palette} text={snapshot.lastError} width={chatWidth - 2} />
          ) : null}
        </Box>
        {sidebar ? <Sidebar palette={palette} snapshot={snapshot} state={config} t={t} width={sidebarWidth} /> : null}
      </Box>
      {debug ? <DebugPanel lines={debugLines} palette={palette} t={t} width={width - 2} /> : null}
      <Composer
        cursor={cursor}
        input={input}
        limit={limit}
        palette={palette}
        scroll={scroll}
        t={t}
        width={width - 2}
      />
      <Footer palette={palette} scrollable={limit > 0} t={t} width={width - 2} toast={toast} />
    </Box>
  );
}

const DEBUG_PANEL_ROWS = 8;

const HELP_KEYS: Array<[string, string]> = [
  ['Enter', 'Send the message.'],
  ['PgUp / PgDn', 'Scroll the transcript by a screen.'],
  ['↑ / ↓', 'Scroll one line.'],
  ['← / →', 'Move the caret in the composer.'],
  ['Esc', 'Close a panel, interrupt a reply, jump to the newest line, or open settings.'],
  ['Ctrl-S', 'Open the provider setup.'],
  ['Ctrl-L', 'Clear the conversation in both interfaces.'],
  ['Ctrl-K', 'Discard the composer.'],
  ['Ctrl-Q / Ctrl-C', 'Leave the line.'],
];

const HELP_COMMANDS: Array<[string, string]> = [
  ['/help', 'Show or hide this panel.'],
  ['/settings', 'Open the provider setup.'],
  ['/clear', 'Clear the shared transcript.'],
  ['/interrupt', 'Stop a reply in flight.'],
  ['/theme', 'Switch between dark and light.'],
  ['/lang', 'Cycle the interface language.'],
  ['/accent [#hex]', 'Cycle the accent, or set it with /accent #0EA5E9.'],
  ['/send <text>', 'Send a message without leaving the composer.'],
  ['/quit', 'Leave the line.'],
];

function HelpOverlay({ palette, t, width, height }: { palette: Palette; t: Translator; width: number; height: number }): React.ReactElement {
  const keyWidth = 18;
  const room = Math.max(20, width - keyWidth - 6);
  return (
    <Box borderColor={palette.accent} borderStyle="round" flexDirection="column" height={height} paddingX={2} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={palette.accent}>{`⌘ ${t('cliHelpTitle')}`}</Text>
        <Text color={palette.inkFaint}>{t('cliHelpClose')}</Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        {HELP_KEYS.map(([key, description]) => (
          <Box key={key}>
            <Box width={keyWidth}>
              <Text bold color={palette.inkMuted}>{truncateWidth(key, keyWidth)}</Text>
            </Box>
            <Text color={palette.inkFaint}>{truncateWidth(description, room)}</Text>
          </Box>
        ))}
      </Box>
      <Divider palette={palette} width={Math.max(4, width - 2)} />
      <Box flexDirection="column" marginTop={1}>
        {HELP_COMMANDS.map(([key, description]) => (
          <Box key={key}>
            <Box width={keyWidth}>
              <Text bold color={palette.accent}>{truncateWidth(key, keyWidth)}</Text>
            </Box>
            <Text color={palette.inkMuted}>{truncateWidth(description, room)}</Text>
          </Box>
        ))}
      </Box>
      <Box flexGrow={1} />
      <Text color={palette.inkFaint}>{t('cliSyncNote')}</Text>
    </Box>
  );
}

function Header({
  palette,
  snapshot,
  state,
  t,
  width,
}: {
  palette: Palette;
  snapshot: ClientSnapshot;
  state: PhoneConfig;
  t: Translator;
  width: number;
}): React.ReactElement {
  const tone: Tone = statusTone(snapshot.status);
  return (
    <Box justifyContent="space-between" width={width}>
      <Box>
        <Text bold color={palette.accent}>{`◉ ${t('appName')}`}</Text>
        <Text color={palette.inkFaint}>{`  ${t('liveLine')}`}</Text>
      </Box>
      <Box gap={2}>
        <Text color={palette.inkFaint}>{`rev ${state.revision}`}</Text>
        <StatusPill palette={palette} status={tone}>{t(statusLabelKey(snapshot.status))}</StatusPill>
      </Box>
    </Box>
  );
}

function EmptyState({ palette, t, width }: { palette: Palette; t: Translator; width: number }): React.ReactElement {
  return (
    <Box flexDirection="column" flexGrow={1} justifyContent="center" paddingX={1}>
      <Text bold color={palette.accent}>{t('welcomeTitle')}</Text>
      <Text color={palette.inkMuted}>{truncateWidth(t('welcomeBody'), width)}</Text>
    </Box>
  );
}

function PendingRow({ palette, t }: { palette: Palette; t: Translator }): React.ReactElement {
  return (
    <Box>
      <Spinner palette={palette} />
      <Text color={palette.accent}>{`  ${t('thinking')}…`}</Text>
    </Box>
  );
}

/**
 * The reply as the model writes it, one line at a time.
 *
 * Only the tail is shown: a long answer is already in the transcript the moment
 * it finishes, and a streaming pane that grows without bound pushes the whole
 * conversation off the top of the screen mid-answer.
 */
const STREAMING_LINES = 3;

function StreamingReply({
  palette,
  text,
  t,
  width,
}: {
  palette: Palette;
  text: string;
  t: Translator;
  width: number;
}): React.ReactElement {
  const rows = wrapText(text, Math.max(8, width - 2));
  const shown = rows.slice(-STREAMING_LINES);
  const hidden = rows.length - shown.length;
  return (
    <Box flexDirection="column">
      {hidden > 0 ? (
        <Text color={palette.inkFaint}>{`  ⋯ ${t('cliMoreLines', { count: hidden })}`}</Text>
      ) : null}
      <Box>
        <Text color={palette.accent}>{'◆ '}</Text>
        <Text color={palette.inkMuted}>{shown.join('\n')}</Text>
      </Box>
    </Box>
  );
}

/**
 * Why the last turn produced no answer.
 *
 * The text is whatever the provider said, so a wrong key reads as a wrong key
 * rather than as a shrug. Hiding it behind a generic sentence is what made a
 * misconfiguration look like a working program.
 */
function ErrorRow({
  palette,
  text,
  width,
}: {
  palette: Palette;
  text: string;
  width: number;
}): React.ReactElement {
  // Two lines, so the sentence the provider sent is not cut off at the first
  // line's worth of characters — that cut is where the useful half usually is.
  const rows = wrapText(text, Math.max(10, width - 2));
  return (
    <Box flexDirection="column">
      {rows.slice(0, 2).map((row, index) => (
        <Text color={palette.danger} key={index}>{index === 0 ? `✕ ${row}` : `  ${row}`}</Text>
      ))}
    </Box>
  );
}

function Sidebar({
  palette,
  snapshot,
  state,
  t,
  width,
}: {
  palette: Palette;
  snapshot: ClientSnapshot;
  state: PhoneConfig;
  t: Translator;
  width: number;
}): React.ReactElement {
  const live = state.transport === 'file';
  const paired = `${live ? '●' : '○'} ${live ? t('syncBadgeLive') : t('syncBadgeLocal')}`;
  const pairedTone: Tone = live ? 'success' : 'warning';
  return (
    <Box flexDirection="column" marginLeft={1} width={width}>
      <Panel palette={palette} title={t('connection')} width={width}>
        <Box flexDirection="column">
          <Meta palette={palette} label={t('statusLabel')}>
            <Text color={pairedTone}>{snapshot.connected ? t('connected') : t('offline')}</Text>
          </Meta>
          <Meta palette={palette} label={t('cliKey')}>
            {hasEnvironmentApiKey()
              ? t('syncKeyEnvironmentShort')
              : state.credential.present ? t('syncKeyOnFileShort') : t('syncKeyNone')}
          </Meta>
          <Meta palette={palette} label={t('syncRevision')}>{String(state.revision)}</Meta>
        </Box>
      </Panel>
      <Box marginTop={1}>
        <Panel badge={paired} badgeTone={pairedTone} palette={palette} title={t('syncTitle')} width={width}>
          <Box flexDirection="column">
            <Meta palette={palette} label={t('cliProvider')}>
              {truncateWidth(snapshot.settings.provider, Math.max(6, width - 18))}
            </Meta>
            <Meta palette={palette} label={t('cliMessages')}>{String(snapshot.messages.length).padStart(2, '0')}</Meta>
          </Box>
        </Panel>
      </Box>
      <Box marginTop={1}>
        <Panel palette={palette} title={t('cliModel')} width={width}>
          <Text color={palette.inkMuted}>
            {truncateWidth(snapshot.settings.model, width - 4)}
          </Text>
        </Panel>
      </Box>
    </Box>
  );
}

function DebugPanel({
  lines,
  palette,
  t,
  width,
}: {
  lines: string[];
  palette: Palette;
  t: Translator;
  width: number;
}): React.ReactElement {
  return (
    <Box borderColor={palette.line} borderStyle="round" flexDirection="column" paddingX={1} width={width}>
      <Text bold color={palette.accent}>{t('debug')}</Text>
      {lines.length === 0
        ? <Text color={palette.inkFaint}>{t('debugIdle')}</Text>
        : lines.map((line, index) => (
          <Text color={palette.inkFaint} key={`${index}-${line.slice(0, 10)}`}>
            {truncateWidth(line, width - 4)}
          </Text>
        ))}
    </Box>
  );
}

function Composer({
  cursor,
  input,
  limit,
  palette,
  scroll,
  t,
  width,
}: {
  cursor: number;
  input: string;
  limit: number;
  palette: Palette;
  scroll: number;
  t: Translator;
  width: number;
}): React.ReactElement {
  const position = Math.max(0, Math.min(cursor, input.length));
  const before = input.slice(0, position);
  const at = input.slice(position, position + 1);
  const after = input.slice(position + 1);
  // The row is the prompt (2 columns), the caret (1) and the text, so the
  // text's budget is the box's inner width minus three: a budget that ignores
  // them wraps the composer onto a second line on every keystroke past the
  // right edge, and a composer that grows while you type is one that pushes
  // the transcript away. The window scrolls to keep the caret in view, so
  // typing past the edge moves the text instead of pushing the caret off.
  const room = Math.max(4, width - 7);
  const lead = Math.max(0, displayWidth(before) - room + 1);
  const visibleBefore = lead > 0 ? `…${before.slice(lead)}` : before;
  const shownBefore = truncateWidth(visibleBefore, room);
  return (
    <Box
      borderColor={input ? palette.accent : palette.line}
      borderStyle="round"
      flexDirection="column"
      paddingX={1}
      width={width}
    >
      <Box>
        <Text bold color={palette.accent}>{'❯ '}</Text>
        {shownBefore ? <Text color={palette.ink}>{shownBefore}</Text> : null}
        <Text inverse>{at || ' '}</Text>
        {after ? <Text color={palette.ink}>{truncateWidth(after, Math.max(0, room - displayWidth(shownBefore)))}</Text> : null}
      </Box>
      <Box justifyContent="space-between">
        {scroll > 0 ? (
          <Text color={palette.accent}>{`▲ ${scroll}/${limit} ${t('cliScrollHint')}`}</Text>
        ) : (
          <Hint palette={palette}>{t('interruptHint')}</Hint>
        )}
        {/* The cap is enforced on code units, so the counter counts them too:
            a counter that counts columns would read "over" while a wide-char
            draft is still accepted, and the other way round for ASCII. */}
        <Text color={palette.inkFaint}>{`${input.length}/${MAX_TERMINAL_TEXT_LENGTH}`}</Text>
      </Box>
    </Box>
  );
}

function Footer({
  palette,
  scrollable,
  t,
  toast,
  width,
}: {
  palette: Palette;
  scrollable: boolean;
  t: Translator;
  toast: Toast | null;
  width: number;
}): React.ReactElement {
  const hints = [
    '/help',
    '/settings',
    '/clear',
    ...(scrollable ? [t('cliScrollHint')] : []),
    'Ctrl-Q',
  ].join('  ·  ');
  return (
    <Box flexDirection="column" width={width}>
      {toast ? (
        <Box>
          <Notice palette={palette} tone={toast.tone}>{truncateWidth(toast.text, width - 4)}</Notice>
        </Box>
      ) : (
        <Box>
          <Hint palette={palette}>{truncateWidth(hints, width)}</Hint>
        </Box>
      )}
    </Box>
  );
}
