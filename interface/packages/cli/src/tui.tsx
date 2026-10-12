import React, { useMemo, useState } from 'react';
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
  lineToneHex,
  truncateWidth,
  type ColorDepth,
  type Palette,
  type Tone,
} from './palette.js';
import { Divider, Meta, Panel, StatusPill } from './tui-kit.js';
import { maxScroll, transcriptLines, windowLines } from './transcript.js';
import { statusLabelKey, statusTone, wrapText } from './render.js';
import { runCommand, useComposerInput, type ComposerActions, type ComposerState, type Overlay, type Screen, type Translator } from './tui-input.js';
import { usePhoneSession } from './tui-session.js';
import { SettingsWizard } from './wizard.js';
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
  const { scroll, seek, follow, onSettled } = useTranscriptScroll();

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
        <HelpOverlay
          commands={HELP_COMMANDS}
          height={height - 2}
          keys={HELP_KEYS}
          keyWidth={18}
          noteKey="cliSyncNote"
          palette={palette}
          t={t}
          width={width - 2}
        />
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
              <EmptyState bodyKey="welcomeBody" palette={palette} t={t} titleKey="welcomeTitle" width={chatWidth - 2} />
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
              <PendingRow labelKey="thinking" palette={palette} t={t} />
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
      <Footer
        hints={['/help', '/settings', '/clear', ...(limit > 0 ? [t('cliScrollHint')] : []), 'Ctrl-Q']}
        palette={palette}
        toast={toast}
        width={width - 2}
      />
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
