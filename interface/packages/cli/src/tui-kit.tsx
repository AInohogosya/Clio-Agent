import React, { useEffect, useState } from 'react';
import { Box, Text } from 'ink';
import {
  ASCII_SPINNER,
  SPINNER_FRAMES,
  toneHex,
  type Palette,
  type Tone,
} from './palette.js';

export type { Tone } from './palette.js';
export { toneHex };

export function frames(palette: Palette): readonly string[] {
  return palette.depth === 0 ? ASCII_SPINNER : SPINNER_FRAMES;
}

export function Spinner({ palette, tone = 'accent' }: { palette: Palette; tone?: Tone }): React.ReactElement {
  const [index, setIndex] = useState(0);
  const options = frames(palette);
  useEffect(() => {
    const timer = setInterval(() => setIndex((current) => (current + 1) % options.length), 90);
    return () => clearInterval(timer);
  }, [options.length]);
  return <Text color={toneHex(palette, tone)}>{options[index % options.length]}</Text>;
}

export function Panel({
  palette,
  children,
  width,
  flexGrow,
  flexDirection = 'column',
  borderColor,
  paddingX = 1,
  title,
  badge,
  badgeTone = 'accent',
}: {
  palette: Palette;
  children?: React.ReactNode;
  width?: number;
  flexGrow?: number;
  flexDirection?: 'row' | 'column';
  borderColor?: string;
  paddingX?: number;
  title?: string;
  badge?: string;
  badgeTone?: Tone;
}): React.ReactElement {
  return (
    <Box
      borderColor={borderColor ?? palette.line}
      borderStyle="round"
      flexDirection={flexDirection}
      flexGrow={flexGrow}
      paddingX={paddingX}
      width={width}
    >
      {title || badge ? (
        <Box justifyContent="space-between" marginBottom={1}>
          {title ? (
            <Text bold color={palette.inkFaint}>
              {label(title)}
            </Text>
          ) : <Text> </Text>}
          {badge ? (
            <Text backgroundColor={palette.accentSoft} color={toneHex(palette, badgeTone)}>
              {` ${badge} `}
            </Text>
          ) : null}
        </Box>
      ) : null}
      {children}
    </Box>
  );
}

export function label(value: string): string {
  return value.toUpperCase();
}

export function StatusPill({
  palette,
  status,
  children,
}: {
  palette: Palette;
  status: Tone;
  children: string;
}): React.ReactElement {
  return (
    <Text color={toneHex(palette, status)}>
      <Text>{status === 'muted' ? '○' : '●'}</Text>
      {` ${children}`}
    </Text>
  );
}

export function Meta({ palette, label: text, tone = 'muted', children }: { palette: Palette; label: string; tone?: Tone; children: React.ReactNode }): React.ReactElement {
  return (
    <Box gap={1} justifyContent="space-between">
      <Text color={palette.inkFaint}>{text}</Text>
      <Box flexGrow={1} justifyContent="flex-end" overflow="hidden">
        <Text color={toneHex(palette, tone)} wrap="truncate">
          {children}
        </Text>
      </Box>
    </Box>
  );
}

export function Notice({ palette, tone = 'accent', children }: { palette: Palette; tone?: Tone; children: string }): React.ReactElement | null {
  if (!children) return null;
  const glyph = tone === 'danger' ? '✕' : tone === 'warning' ? '!' : tone === 'success' ? '✓' : '✦';
  return (
    <Text color={toneHex(palette, tone)}>
      {`${glyph} ${children}`}
    </Text>
  );
}

export function Hint({ palette, children }: { palette: Palette; children: string }): React.ReactElement {
  return <Text color={palette.inkFaint}>{children}</Text>;
}

export function Divider({ palette, width }: { palette: Palette; width?: number }): React.ReactElement {
  return (
    <Box marginY={0}>
      <Text color={palette.line}>{'─'.repeat(Math.max(0, width ?? 20))}</Text>
    </Box>
  );
}
