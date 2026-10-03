import { channelLabel, type ChatMessage, type Language } from '@project-phone/core';
import { displayWidth, type LineTone } from './palette.js';
import { formatTime, messageVoice, wrapText } from './render.js';

export interface TranscriptLine {
  text: string;
  tone: LineTone;
  bold?: boolean;
}

export type { LineTone } from './palette.js';

/**
 * The full-screen transcript: a conversation flattened into styled lines so the
 * pane can window by row instead of by message, which keeps scrolling correct
 * once a message wraps over several lines.
 */
export function transcriptLines(
  messages: ChatMessage[],
  options: { width: number; language: Language; gutter?: number },
): TranscriptLine[] {
  const { width, language } = options;
  const gutter = options.gutter ?? 3;
  const lines: TranscriptLine[] = [];
  const body = Math.max(8, width - gutter);

  messages.forEach((message, index) => {
    if (index > 0) lines.push({ text: '', tone: 'faint' });
    const voice = messageVoice(message, language);
    const time = formatTime(message.createdAt, language);
    // The door, when it is not the local line and not the same one as the turn
    // above it. Shown only on a change, because a terminal is narrow and a header
    // that repeats itself is where the width goes.
    const previous = messages[index - 1];
    const channel = typeof message.channel === 'string' ? message.channel.trim() : '';
    const showChannel = channel
      && channel !== 'web'
      && (!previous || previous.channel !== channel);
    const via = showChannel ? channelLabel(channel) : '';
    const header = via ? `${voice.glyph} ${voice.author} · ${via}` : `${voice.glyph} ${voice.author}`;
    // Measured in display columns: a wide-char author (`助手`) is wider than its
    // code units, and a room computed from `length` would print the timestamp
    // where the header itself overflows the pane.
    const timeRoom = Math.max(0, body - displayWidth(header) - 2);
    lines.push({
      text: `${header}  ${timeRoom > 0 ? time : ''}`.trimEnd(),
      tone: voice.tone,
      bold: true,
    });
    for (const line of wrapText(message.text, body)) {
      lines.push({ text: line, tone: voice.isUser ? 'ink' : 'muted' });
    }
  });

  return lines;
}

/** The last `height` lines of the transcript, with the newest at the bottom. */
export function windowLines(lines: TranscriptLine[], height: number, scroll: number): TranscriptLine[] {
  const rows = Math.max(1, height);
  const end = Math.max(0, lines.length - Math.max(0, scroll));
  return lines.slice(Math.max(0, end - rows), end);
}

/** How far the transcript can be scrolled back, in rows. */
export function maxScroll(lines: TranscriptLine[], height: number): number {
  return Math.max(0, lines.length - Math.max(1, height));
}
