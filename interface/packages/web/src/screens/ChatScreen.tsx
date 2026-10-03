import { useEffect, useMemo, useState } from 'react';
import { createTranslator, type ClientSnapshot } from '@project-phone/core';
import { Composer } from '../components/Composer';
import { ConversationFeed, useConversationFeed } from '../components/ConversationFeed';
import { Icon } from '../components/Icon';
import { PairingPanel, type PairingTone } from '../components/PairingPanel';
import { Button, IconButton, SectionLabel, StatusPill, classNames } from '../components/Primitives';

export interface CoordinationState {
  tone: PairingTone;
  configFile: string;
  credential: { present: boolean; source: 'none' | 'file' | 'environment'; hint: string };
  credentialSource: 'none' | 'file' | 'environment';
  viaBridge: boolean;
  lastSyncedAt: number | null;
}

interface ChatScreenProps {
  snapshot: ClientSnapshot;
  coordination: CoordinationState;
  onSend: (text: string) => void;
  onInterrupt: () => void;
  onClear: () => void;
  onOpenSettings: () => void;
}

type Translator = ReturnType<typeof createTranslator>;

function formatTime(timestamp: number, language: string): string {
  if (!Number.isFinite(timestamp)) return '--';
  try {
    return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' }).format(timestamp);
  } catch {
    return '--';
  }
}

function statusLabel(snapshot: ClientSnapshot, t: Translator): { status: 'online' | 'offline' | 'busy'; label: string } {
  if (snapshot.status === 'offline') return { status: 'offline', label: t('offline') };
  if (snapshot.status === 'thinking' || snapshot.status === 'connecting') return { status: 'busy', label: t('thinking') };
  return { status: 'online', label: t('connected') };
}

export function ChatScreen({ snapshot, coordination, onSend, onInterrupt, onClear, onOpenSettings }: ChatScreenProps) {
  const t = useMemo(() => createTranslator(snapshot.settings.language), [snapshot.settings.language]);
  const [draft, setDraft] = useState('');
  const [showError, setShowError] = useState(false);
  const feed = useConversationFeed();
  const currentStatus = statusLabel(snapshot, t);

  // A new turn is a reason to start following again, and to say so once rather
  // than leaving a stale failure hanging over a conversation that has moved on.
  useEffect(() => {
    if (snapshot.status === 'thinking') {
      feed.toLatest();
      setShowError(false);
    }
  }, [snapshot.status, feed.toLatest]);

  const submit = () => {
    const value = draft.trim();
    if (!value) return;
    setDraft('');
    // Sending says the newest line is the one being read, whatever the reader
    // was looking at a moment ago.
    feed.toLatest();
    onSend(value);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-[var(--line)] px-4 py-4 sm:px-7 lg:px-10">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--line-strong)] bg-[var(--canvas-raised)] text-[var(--accent)] shadow-emboss">
              <Icon name="terminal" size={19} />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold tracking-wide text-[var(--ink)]">{t('appName')}</h1>
              <p className="truncate text-xs text-[var(--ink-faint)]">{t('appTagline')}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusPill status={currentStatus.status} label={currentStatus.label} />
            <IconButton icon="settings" label={t('settings')} onClick={onOpenSettings} />
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-7xl min-h-0 flex-1 flex-col overflow-hidden px-4 pb-4 pt-5 sm:px-7 sm:pb-7 lg:px-10">
        <div className="mb-4 flex shrink-0 items-center justify-between gap-4">
          <div>
            <SectionLabel icon="activity">{t('liveChat')}</SectionLabel>
            <p className="text-sm text-[var(--ink-muted)]">{t('liveLine')}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button disabled={!snapshot.pending} icon="pause" variant="outline" onClick={onInterrupt} title={t('stop')}>{t('stop')}</Button>
            <Button icon="trash" variant="outline" onClick={onClear} title={t('clear')}>{t('clear')}</Button>
          </div>
        </div>

        {snapshot.lastError && (showError || !snapshot.pending) ? (
          <div className="mb-4 flex shrink-0 items-start gap-2 rounded-xl border border-[var(--danger-line)] bg-[var(--danger-soft)] px-3 py-2.5 text-xs leading-5 text-[var(--danger)]" role="alert">
            <Icon className="mt-0.5 shrink-0" name="close" size={14} />
            <span className="flex-1">{snapshot.lastError}</span>
            <button className="shrink-0 underline underline-offset-2" onClick={() => setShowError(false)} type="button">{t('dismiss')}</button>
          </div>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_286px]">
          <section className="instrument-panel flex min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl" aria-label={t('liveChat')}>
            <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 sm:px-5">
              <div className="flex items-center gap-2 text-xs text-[var(--ink-muted)]">
                <span className={classNames('h-2 w-2 rounded-full', snapshot.connected ? 'bg-[var(--ok-dot)]' : 'bg-[var(--ink-faint)]')} />
                <span>{snapshot.connected ? t('online') : t('offline')}</span>
                <span className="text-[var(--ink-faint)]">·</span>
                <span>{snapshot.messages.length === 1
                  ? t('messageCountOne', { count: snapshot.messages.length })
                  : t('messageCount', { count: snapshot.messages.length })}</span>
              </div>
              <div className="flex items-center gap-2 text-[0.68rem] text-[var(--ink-faint)]">
                <Icon name={coordination.tone === 'live' ? 'link' : 'lock'} size={13} />
                <span>{coordination.tone === 'live' ? t('syncBridgeLive') : t('syncBridgeLocal')}</span>
              </div>
            </div>

            <ConversationFeed
              contentClassName="space-y-5 px-4 py-5 sm:px-6"
              feed={feed}
              label={t('jumpToLatest')}
            >
              {snapshot.messages.length === 0 && !snapshot.streamingText ? (
                <div className="flex flex-1 flex-col items-center justify-center px-6 py-12 text-center">
                  <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-[var(--line-strong)] bg-[var(--canvas-raised)] text-[var(--accent)] shadow-emboss">
                    <Icon name="spark" size={28} />
                  </div>
                  <h2 className="text-lg font-semibold tracking-tight text-[var(--ink)]">{t('welcomeTitle')}</h2>
                  <p className="mt-2 max-w-sm text-sm leading-6 text-[var(--ink-muted)]">{t('welcomeBody')}</p>
                </div>
              ) : (
                snapshot.messages.map((message) => {
                  const isUser = message.role === 'user';
                  const isSystem = message.role === 'system';
                  return (
                    <div key={message.id} className={classNames('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
                      {!isUser ? (
                        <div className={classNames('mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)]', isSystem ? 'text-[var(--ink-faint)]' : 'text-[var(--ink-muted)]')}>
                          <Icon name={isSystem ? 'spark' : 'cpu'} size={15} />
                        </div>
                      ) : null}
                      <div className={classNames('max-w-[min(86%,680px)]', isUser ? 'items-end' : 'items-start')}>
                        <div className={classNames('mb-1 flex items-center gap-2 text-[0.65rem] text-[var(--ink-faint)]', isUser ? 'justify-end' : 'justify-start')}>
                          <span>{isSystem ? t('system') : isUser ? t('you') : t('assistant')}</span>
                          <span>{formatTime(message.createdAt, snapshot.settings.language)}</span>
                        </div>
                        <div className={classNames(
                          'whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6',
                          isUser
                            ? 'rounded-tr-md border border-[var(--accent-line)] bg-[var(--accent-soft)] text-[var(--ink)]'
                            : 'rounded-tl-md border border-[var(--line)] bg-[var(--canvas-well)] text-[var(--ink)]',
                        )}>
                          {message.text}
                        </div>
                      </div>
                      {isUser ? (
                        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)]">
                          <Icon name="home" size={15} />
                        </div>
                      ) : null}
                    </div>
                  );
                })
              )}
              {snapshot.streamingText ? (
                <div className="flex items-start gap-3 text-sm leading-6 text-[var(--ink-muted)]">
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--accent-line)] bg-[var(--canvas-raised)] text-[var(--accent)]">
                    <Icon name="spark" size={15} />
                  </div>
                  <div className="max-w-[min(86%,680px)] whitespace-pre-wrap rounded-2xl rounded-tl-md border border-[var(--line)] bg-[var(--canvas-well)]">
                    {snapshot.streamingText}
                  </div>
                </div>
              ) : snapshot.pending ? (
                <div className="flex items-center gap-3 text-xs text-[var(--ink-faint)]">
                  <div className="flex gap-1 rounded-lg border border-[var(--line)] bg-[var(--canvas-well)] px-3 py-2.5">
                    <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
                    <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:180ms]" />
                    <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:360ms]" />
                  </div>
                  <span>{t('thinking')}</span>
                </div>
              ) : null}
            </ConversationFeed>

            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              onInterrupt={onInterrupt}
              pending={snapshot.pending}
              placeholder={t('placeholder')}
              sendLabel={t('send')}
            >
              <div className="mt-2 flex items-center justify-between gap-3 px-1 text-[0.65rem] text-[var(--ink-faint)]">
                <span>{t('interruptHint')}</span>
                <span className="hidden sm:inline">{t('footerShortcuts')}</span>
              </div>
            </Composer>
          </section>

          <aside className="hidden min-h-0 flex-col gap-4 lg:flex">
            <PairingPanel
              configFile={coordination.configFile}
              credential={coordination.credential}
              credentialSource={coordination.credentialSource}
              language={snapshot.settings.language}
              lastSyncedAt={coordination.lastSyncedAt}
              model={snapshot.settings.model}
              provider={snapshot.settings.provider}
              tone={coordination.tone}
              viaBridge={coordination.viaBridge}
            />

            <section className="instrument-panel rounded-2xl p-4">
              <SectionLabel icon="cpu">{t('connection')}</SectionLabel>
              <div className="space-y-3 text-xs">
                <div className="flex items-center justify-between gap-3"><span className="text-[var(--ink-faint)]">{t('statusLabel')}</span><StatusPill status={currentStatus.status} label={currentStatus.label} /></div>
                <div className="flex items-center justify-between gap-3"><span className="text-[var(--ink-faint)]">{snapshot.messages.length === 1 ? t('messageCountOne', { count: snapshot.messages.length }) : t('messageCount', { count: snapshot.messages.length })}</span><span className="text-[var(--ink-muted)]">{snapshot.messages.length.toString().padStart(2, '0')}</span></div>
                <div className="flex items-center justify-between gap-3"><span className="text-[var(--ink-faint)]">{t('lastActivity')}</span><span className="text-[var(--ink-muted)]">{snapshot.lastActivityAt ? formatTime(snapshot.lastActivityAt, snapshot.settings.language) : '—'}</span></div>
              </div>
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
