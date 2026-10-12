import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createTranslator,
  DuplexClient,
  publicSettingsEqual,
  sanitizeTerminalText,
  type ClientSnapshot,
  type Settings,
} from '@project-phone/core';
import { saveConfig, watchConfig, type PhoneConfig } from './config.js';
import type { Tone } from './palette.js';
import {
  appendDebug,
  DEBUG_LINE_LENGTH,
  PERSIST_DEBOUNCE_MS,
  sameHistory,
  useToast,
  type SessionToast as Toast,
} from './session-shared.js';

/**
 * The state half of the terminal interface: one client, the snapshot it
 * publishes, and the two ways the shared file changes underneath us.
 *
 * Two rules decide what is written, and they are the reason this lives apart
 * from the layout:
 *
 *   - a write only happens when something actually changed, so an idle session
 *     does not rewrite the file it is reading;
 *   - a revision the terminal itself wrote is never read back as somebody else's
 *     change, so saving cannot trigger the reload it just caused.
 */

export type { SessionToast as Toast } from './session-shared.js';

export interface PhoneSession {
  client: DuplexClient;
  snapshot: ClientSnapshot;
  /** The shared state as this process last read or wrote it. */
  config: PhoneConfig;
  debugLines: string[];
  toast: Toast | null;
  say: (text: string, tone?: Tone) => void;
  clearConversation: () => void;
  interrupt: () => void;
  patchSettings: (patch: Partial<Settings>) => void;
}

export function usePhoneSession(
  initialConfig: PhoneConfig,
  debug: boolean,
  onSettled: () => void,
): PhoneSession {
  const clientRef = useRef<DuplexClient | null>(null);
  if (clientRef.current === null) {
    clientRef.current = new DuplexClient({
      settings: initialConfig.settings,
      initialMessages: initialConfig.messages,
    });
  }
  const client = clientRef.current;

  const [snapshot, setSnapshot] = useState<ClientSnapshot>(() => client.getSnapshot());
  const [config, setConfig] = useState<PhoneConfig>(initialConfig);
  const [debugLines, setDebugLines] = useState<string[]>([]);
  const { toast, say } = useToast();

  const configRef = useRef<PhoneConfig>(initialConfig);
  const lastWriteRef = useRef<number>(-1);

  const t = useMemo(() => createTranslator(snapshot.settings.language), [snapshot.settings.language]);

  useEffect(() => {
    const unsubscribe = client.subscribe((event) => {
      if (event.type === 'snapshot') {
        setSnapshot(event.snapshot);
        // A finished turn is the newest thing in the pane, so the view follows
        // it — unless the reader has scrolled away, which is the caller's
        // `onSettled` to make, not this one's.
        if (event.snapshot.pending === false) onSettled();
      }
      if (!debug) return;
      if (event.type === 'message') {
        setDebugLines((lines) => appendDebug(lines, `sent    ${event.message.role}`));
      }
      if (event.type === 'delta') {
        setDebugLines((lines) => appendDebug(lines, `token   ${event.text.length}`));
      }
      if (event.type === 'status') {
        setDebugLines((lines) => appendDebug(lines, `status  ${event.status}`));
      }
      if (event.type === 'error') {
        const detail = sanitizeTerminalText(event.message, DEBUG_LINE_LENGTH);
        setDebugLines((lines) => appendDebug(lines, `error   ${detail}`));
      }
    });
    client.connect();
    return () => {
      unsubscribe();
      client.disconnect();
    };
  }, [client, debug, onSettled]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const current = configRef.current;
      const next: PhoneConfig = {
        ...current,
        settings: snapshot.settings,
        messages: snapshot.messages,
      };
      const settingsChanged = !publicSettingsEqual(current.settings, next.settings)
        || current.settings.apiKey.trim() !== next.settings.apiKey.trim();
      if (!settingsChanged && sameHistory(current.messages, next.messages)) return;
      const saved = saveConfig(next, { origin: 'cli' });
      lastWriteRef.current = saved.revision;
      configRef.current = saved;
      setConfig(saved);
    }, PERSIST_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [snapshot.messages, snapshot.settings]);

  // Adopt changes the browser (or another phone process) made to the file.
  useEffect(() => {
    return watchConfig((remote) => {
      if (remote.revision === lastWriteRef.current) return;
      const previous = configRef.current;
      // A lower revision from the terminal itself is a write of ours still in
      // flight, not an edit to undo.
      if (remote.revision <= previous.revision && remote.origin === 'cli') return;
      configRef.current = remote;
      setConfig(remote);
      if (!publicSettingsEqual(previous.settings, remote.settings)) {
        client.setSettings(remote.settings);
        say(t('cliReloaded'), 'success');
      }
      if (!sameHistory(previous.messages, remote.messages)) {
        client.adoptHistory(remote.messages);
      }
    });
  }, [client, say, t]);

  const patchSettings = useCallback((patch: Partial<Settings>) => {
    client.setSettings({ ...client.getSnapshot().settings, ...patch });
  }, [client]);

  const clearConversation = useCallback(() => {
    client.clearConversation();
    say(t('cliCleared'), 'success');
  }, [client, say, t]);

  const interrupt = useCallback(() => {
    client.interrupt();
    say(t('cliInterruptDone'), 'warning');
  }, [client, say, t]);

  return {
    client,
    snapshot,
    config,
    debugLines,
    toast,
    say,
    clearConversation,
    interrupt,
    patchSettings,
  };
}

export { appendDebug, sameHistory };

