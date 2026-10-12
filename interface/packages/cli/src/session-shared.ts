import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '@project-phone/core';
import type { Tone } from './palette.js';

/**
 * The parts of the two session hooks that are one part.
 *
 * `usePhoneSession` (direct chat) and `useAgentSession` (agent operator) both
 * keep a toast, compare histories, and bound a debug panel: the same state
 * with the same rules, so they are written once. What is genuinely different
 * between the two — what is persisted, what the subscribe effects draw, which
 * client records a turn — stays in each hook, where the difference is visible.
 */

export const PERSIST_DEBOUNCE_MS = 220;
const TOAST_MS = 3_200;
export const DEBUG_LINE_LIMIT = 6;
export const DEBUG_LINE_LENGTH = 90;

export interface SessionToast {
  text: string;
  tone: Tone;
  token: number;
}

export function useToast(): {
  toast: SessionToast | null;
  say: (text: string, tone?: Tone) => void;
  dismiss: () => void;
} {
  const [toast, setToast] = useState<SessionToast | null>(null);
  const toastToken = useRef(0);
  const mounted = useRef(true);

  const say = useCallback((text: string, tone: Tone = 'accent') => {
    toastToken.current += 1;
    setToast({ text, tone, token: toastToken.current });
  }, []);

  const dismiss = useCallback(() => setToast(null), []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => {
      if (mounted.current) setToast(null);
    }, TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  return { toast, say, dismiss };
}

/** Two histories are the same when the newest entry of each is the same entry. */
export function sameHistory(a: ChatMessage[], b: ChatMessage[]): boolean {
  if (a.length !== b.length) return false;
  return a.at(-1)?.id === b.at(-1)?.id;
}

/** The panel keeps the most recent lines only; a long session must not grow it. */
export function appendDebug(lines: string[], line: string): string[] {
  return [...lines.slice(-(DEBUG_LINE_LIMIT - 1)), line];
}
