import type { ReactNode } from 'react';
import { createTranslator, type Language } from '@project-phone/core';
import { providerFor } from '@project-phone/core';
import { Icon, type IconName } from '../components/Icon';
import { classNames } from '../components/Primitives';

export type PairingTone = 'live' | 'local' | 'blocked';

export interface PairingPanelProps {
  tone: PairingTone;
  configFile: string;
  credential: { present: boolean; source: 'none' | 'file' | 'environment'; hint: string };
  credentialSource: 'none' | 'file' | 'environment';
  viaBridge: boolean;
  lastSyncedAt: number | null;
  language: Language;
  provider: string;
  model: string;
}

const TONE_STYLES: Record<PairingTone, { dot: string; text: string; labelKey: 'syncBridgeLive' | 'syncBridgeLocal' | 'syncKeyNone' }> = {
  live: { dot: 'bg-[var(--ok-dot)]', text: 'text-[var(--ok)]', labelKey: 'syncBridgeLive' },
  local: { dot: 'bg-[var(--warn-dot)]', text: 'text-[var(--warn)]', labelKey: 'syncBridgeLocal' },
  blocked: { dot: 'bg-[var(--danger-dot)]', text: 'text-[var(--danger)]', labelKey: 'syncBridgeLocal' },
};

function formatSync(timestamp: number | null, language: Language): string {
  if (!timestamp) return '—';
  try {
    return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(timestamp);
  } catch {
    return '—';
  }
}

/**
 * Makes the pairing between this page and the terminal visible: which store is
 * authoritative, where it lives, and how the credential is being used.
 */
export function PairingPanel({
  tone,
  configFile,
  credential,
  credentialSource,
  viaBridge,
  lastSyncedAt,
  language,
  provider,
  model,
}: PairingPanelProps): ReactNode {
  const t = createTranslator(language);
  const style = TONE_STYLES[tone];
  // A local endpoint needs no key, so "no key" must not read as "not set up".
  const keyless = !providerFor(provider as never).requiresApiKey;
  const credentialLabel = keyless
    ? t('syncKeyNotNeeded')
    : credential.present
      ? credentialSource === 'environment' ? t('syncKeyEnvironment') : t('syncKeyOnFile')
      : t('syncKeyNone');

  return (
    <section className="instrument-panel rounded-2xl p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <SectionLabel icon="link">{t('syncTitle')}</SectionLabel>
          <p className={classNames('text-xs', style.text)}>
            {tone === 'live' ? t('syncPaired') : tone === 'blocked' ? t('syncBlocked') : t('syncUnpaired')}
          </p>
        </div>
        <span className="mt-1 flex h-2.5 w-2.5 shrink-0 items-center">
          <span className={classNames('h-2.5 w-2.5 rounded-full', style.dot, tone === 'live' && 'signal-pulse')} />
        </span>
      </div>
      <div className="instrument-well rounded-xl p-3">
        <dl className="space-y-2 text-[0.68rem]">
          <Row label={t('statusLabel')} value={t(style.labelKey)} tone={style.text} />
          <Row label={t('cliProvider')} value={providerFor(provider as never).label} />
          <Row label={t('cliModel')} value={model || '—'} mono />
          <Row label={t('cliConfigFile')} value={configFile || '—'} mono />
          <Row label={t('cliUpdatedAt')} value={formatSync(lastSyncedAt, language)} />
        </dl>
      </div>
      <div className="mt-4 space-y-2 text-[0.68rem]">
        <div className="flex items-start gap-2">
          <Icon className="mt-0.5 shrink-0 text-[var(--accent)]" name="key" size={13} />
          <span className="text-[var(--ink-muted)]">
            {credentialLabel}
            {credential.hint ? <span className="ml-1 font-mono text-[var(--ink-faint)]">{credential.hint}</span> : null}
          </span>
        </div>
        <div className="flex items-start gap-2">
          <Icon className="mt-0.5 shrink-0 text-[var(--ink-faint)]" name="shield" size={13} />
          <span className="text-[var(--ink-faint)]">
            {viaBridge ? t('syncViaBridge') : t('syncViaSession')}
          </span>
        </div>
      </div>
    </section>
  );
}

function SectionLabel({ children, icon }: { children: ReactNode; icon: IconName }): ReactNode {
  return (
    <div className="mb-1.5 flex items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--ink-faint)]">
      <Icon name={icon} size={14} />
      <span>{children}</span>
    </div>
  );
}

function Row({ label, value, mono, tone }: { label: string; value: string; mono?: boolean; tone?: string }): ReactNode {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-[var(--ink-faint)]">{label}</dt>
      <dd className={classNames('truncate text-right text-[var(--ink-muted)]', mono && 'font-mono text-[0.62rem]', tone)} title={value}>
        {value}
      </dd>
    </div>
  );
}
