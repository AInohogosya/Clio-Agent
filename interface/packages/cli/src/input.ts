import { createInterface } from 'node:readline/promises';
import { sanitizeStoredText, MAX_PROMPT_LENGTH } from '@project-phone/core';
import { CliError } from './args.js';

const INTERACTIVE_TIMEOUT_MS = 180_000;
const PIPED_TIMEOUT_MS = 10_000;

function stopInput(): void {
  process.stdin.pause();
  const candidate = process.stdin as typeof process.stdin & { unref?: () => void };
  candidate.unref?.();
}

/**
 * Reads one line from an interactive terminal while enforcing a hard byte
 * budget, so a runaway paste cannot exhaust memory before the limit trips.
 */
export async function readLine(prompt: string, maxBytes: number): Promise<string> {
  const input = createInterface({ input: process.stdin, output: process.stderr });
  return new Promise<string>((resolve, reject) => {
    let settled = false;
    let totalBytes = 0;
    let onData: ((chunk: Buffer | string) => void) | null = null;
    const timerRef: { value: ReturnType<typeof setTimeout> | undefined } = { value: undefined };
    const cleanup = () => {
      if (timerRef.value !== undefined) clearTimeout(timerRef.value);
      if (onData) process.stdin.removeListener('data', onData);
      input.close();
    };
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      stopInput();
      reject(error);
    };
    timerRef.value = setTimeout(() => fail(new CliError('input_timeout')), INTERACTIVE_TIMEOUT_MS);
    onData = (chunk) => {
      totalBytes += typeof chunk === 'string' ? Buffer.byteLength(chunk) : chunk.byteLength;
      if (totalBytes > maxBytes) fail(new CliError('input_too_large'));
    };
    process.stdin.on('data', onData);
    void input.question(prompt).then(
      (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      },
      (error: unknown) => fail(error instanceof Error ? error : new CliError('input_failed')),
    );
  });
}

/** Reads the first line of piped input, then stops consuming the stream. */
export async function readPipedLine(maxBytes: number): Promise<string> {
  let value = '';
  let totalBytes = 0;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    process.stdin.destroy();
  }, PIPED_TIMEOUT_MS);
  try {
    for await (const chunk of process.stdin) {
      const bytes = typeof chunk === 'string' ? Buffer.from(chunk) : chunk;
      totalBytes += bytes.byteLength;
      if (totalBytes > maxBytes) {
        stopInput();
        throw new CliError('input_too_large');
      }
      value += bytes.toString('utf8');
      const lineEnd = value.search(/\r?\n/);
      if (lineEnd >= 0) {
        stopInput();
        return value.slice(0, lineEnd);
      }
      if (timedOut) throw new CliError('input_timeout');
    }
    if (timedOut) throw new CliError('input_timeout');
    return value;
  } finally {
    clearTimeout(timer);
  }
}

/** Reads every line of piped input, which is how a script feeds a session. */
export async function readPipedLines(maxBytes: number): Promise<string[]> {
  const lines: string[] = [];
  let totalBytes = 0;
  const timer = setTimeout(() => {
    process.stdin.destroy();
  }, PIPED_TIMEOUT_MS);
  try {
    let buffer = '';
    for await (const chunk of process.stdin) {
      const bytes = typeof chunk === 'string' ? Buffer.from(chunk) : chunk;
      totalBytes += bytes.byteLength;
      if (totalBytes > maxBytes) {
        stopInput();
        throw new CliError('input_too_large');
      }
      buffer += bytes.toString('utf8');
      let newline = buffer.indexOf('\n');
      while (newline !== -1) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        lines.push(sanitizeLine(line));
        newline = buffer.indexOf('\n');
      }
    }
    if (buffer.length > 0) lines.push(sanitizeLine(buffer));
    return lines.filter((line) => line.length > 0);
  } finally {
    clearTimeout(timer);
  }
}

function sanitizeLine(value: string): string {
  return sanitizeStoredText(value, MAX_PROMPT_LENGTH).trim();
}

export function cleanMessage(raw: string): string {
  const value = sanitizeStoredText(raw, MAX_PROMPT_LENGTH).trim();
  if (!value) throw new CliError('missing_message');
  return value;
}

export async function promptForMessage(): Promise<string> {
  const prompt = process.stdin.isTTY ? '❯ ' : '';
  const raw = process.stdin.isTTY
    ? await readLine(prompt, MAX_PROMPT_LENGTH)
    : await readPipedLine(MAX_PROMPT_LENGTH);
  return cleanMessage(raw);
}

export function isInteractive(): boolean {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY);
}
