import React, { useCallback, useRef, useState } from 'react';
import { Box, Text } from 'ink';
import { MAX_TERMINAL_TEXT_LENGTH } from '@project-phone/core';
import {
  displayWidth,
  truncateWidth,
  type Palette,
  type Tone,
} from './palette.js';
import { Divider, Hint, Notice, Spinner } from './tui-kit.js';
import { wrapText } from './render.js';
import type { Translator } from './tui-input.js';

type TranslationKey = Parameters<Translator>[0];

/**
 * The parts of the two full-screen terminals that are one part.
 *
 * `tui.tsx` (the direct chat surface) and `agent-tui.tsx` (the agent operator
 * surface) draw the same transcript/composer/error/debug/footer/help pieces for
 * the same reader, and the pieces are the same pieces: the only things that
 * differ between the two are labels, help text, and the agent's channel badge
 * in the composer. Shared so the two cannot drift — an error row that wrapped
 * to two lines in one and one line in the other would be two promises about
 * what a provider refusal looks like.
 */

export interface TranscriptScroll {
  /** The clamped scroll offset the window renders. */
  scroll: number;
  /** Scroll to an absolute line or transform the current one. */
  seek: (next: number | ((current: number) => number)) => void;
  /** Return to the newest line. */
  follow: () => void;
  /** Follow only when the reader has not parked the view elsewhere. */
  onSettled: () => void;
}

export function useTranscriptScroll(): TranscriptScroll {
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

  return { scroll, seek, follow, onSettled };
}

export function EmptyState({
  palette,
  t,
  width,
  titleKey,
  bodyKey,
}: {
  palette: Palette;
  t: Translator;
  width: number;
  titleKey: TranslationKey;
  bodyKey: TranslationKey;
}): React.ReactElement {
  return (
    <Box flexDirection="column" flexGrow={1} justifyContent="center" paddingX={1}>
      <Text bold color={palette.accent}>{t(titleKey)}</Text>
      <Text color={palette.inkMuted}>{truncateWidth(t(bodyKey), width)}</Text>
    </Box>
  );
}

export function PendingRow({ palette, t, labelKey }: { palette: Palette; t: Translator; labelKey: TranslationKey }): React.ReactElement {
  return (
    <Box>
      <Spinner palette={palette} />
      <Text color={palette.accent}>{`  ${t(labelKey)}…`}</Text>
    </Box>
  );
}

/**
 * Why the last turn produced no answer.
 *
 * The text is whatever the other side said, so a wrong key reads as a wrong key
 * rather than as a shrug. Hiding it behind a generic sentence is what made a
 * misconfiguration look like a working program.
 */
export function ErrorRow({
  palette,
  text,
  width,
}: {
  palette: Palette;
  text: string;
  width: number;
}): React.ReactElement {
  // Two lines, so the sentence the other side sent is not cut off at the first
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

export function DebugPanel({
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

export function HelpOverlay({
  palette,
  t,
  width,
  height,
  keyWidth,
  keys,
  commands,
  noteKey,
}: {
  palette: Palette;
  t: Translator;
  width: number;
  height: number;
  keyWidth: number;
  keys: Array<[string, string]>;
  commands: Array<[string, string]>;
  noteKey: TranslationKey;
}): React.ReactElement {
  const room = Math.max(20, width - keyWidth - 6);
  return (
    <Box borderColor={palette.accent} borderStyle="round" flexDirection="column" height={height} paddingX={2} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={palette.accent}>{`⌘ ${t('cliHelpTitle')}`}</Text>
        <Text color={palette.inkFaint}>{t('cliHelpClose')}</Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        {keys.map(([key, description]) => (
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
        {commands.map(([key, description]) => (
          <Box key={key}>
            <Box width={keyWidth}>
              <Text bold color={palette.accent}>{truncateWidth(key, keyWidth)}</Text>
            </Box>
            <Text color={palette.inkMuted}>{truncateWidth(description, room)}</Text>
          </Box>
        ))}
      </Box>
      <Box flexGrow={1} />
      <Text color={palette.inkFaint}>{t(noteKey)}</Text>
    </Box>
  );
}

export function Composer({
  cursor,
  input,
  limit,
  palette,
  scroll,
  t,
  width,
  badge,
}: {
  cursor: number;
  input: string;
  limit: number;
  palette: Palette;
  scroll: number;
  t: Translator;
  width: number;
  badge?: string;
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
  //
  // A door badge comes out of that budget when it is shown, and the text budget
  // shrinks to match — the badge is never allowed to push the draft off the
  // row, and the draft is what the reader is in the middle of.
  const room = Math.max(4, width - 7 - (badge ? displayWidth(badge) + 1 : 0));
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
        {badge ? <Text bold color={palette.accent}>{`${badge} `}</Text> : null}
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

export function Footer({
  palette,
  toast,
  width,
  hints,
}: {
  palette: Palette;
  toast: { text: string; tone: Tone } | null;
  width: number;
  hints: string[];
}): React.ReactElement {
  return (
    <Box flexDirection="column" width={width}>
      {toast ? (
        <Box>
          <Notice palette={palette} tone={toast.tone}>{truncateWidth(toast.text, width - 4)}</Notice>
        </Box>
      ) : (
        <Box>
          <Hint palette={palette}>{truncateWidth(hints.join('  ·  '), width)}</Hint>
        </Box>
      )}
    </Box>
  );
}
