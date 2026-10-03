import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import {
  chooseDiscoveredModel,
  SETUP_STEP_LABELS,
  SETUP_STEPS,
  commitDraftSettings,
  createTranslator,
  credentialsForProvider,
  discoverProviderModels,
  filterModels,
  languageLabel,
  LANGUAGES,
  providerDescriptionKey,
  providerFor,
  PROVIDER_DEFINITIONS,
  sanitizeDraftSettings,
  setupProblem,
  type Language,
  type ModelDiscoveryResult,
  type ProviderId,
  type Settings,
  type Theme,
} from '@project-phone/core';
import {
  ACCENT_PRESETS,
  accentStep,
  createPalette,
  displayWidth,
  nearestAccent,
  truncateWidth,
  type ColorDepth,
  type Palette,
} from './palette.js';
import { Divider, Hint, Meta, Notice, Spinner } from './tui-kit.js';

export type WizardStep = 1 | 2 | 3 | 4;

export interface WizardProps {
  settings: Settings;
  width: number;
  height: number;
  depth: ColorDepth;
  onSave: (settings: Settings) => void;
  onCancel: () => void;
}

/** Kept as the wizard's own name for the shared rule. */
export const sanitizeDraft = sanitizeDraftSettings;

export function SettingsWizard({ settings, width, height, depth, onSave, onCancel }: WizardProps): React.ReactElement {
  const t = createTranslator(settings.language);
  const [step, setStep] = useState<WizardStep>(1);
  const [draft, setDraft] = useState<Settings>({ ...settings });
  const [field, setField] = useState<'apiKey' | 'baseUrl'>('apiKey');
  const [models, setModels] = useState<string[]>([]);
  const [modelIndex, setModelIndex] = useState(0);
  const [query, setQuery] = useState('');
  const [discovering, setDiscovering] = useState(false);
  const [source, setSource] = useState<'remote' | 'offline'>('offline');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Which appearance row step 4 has focused: 0 language, 1 theme, 2 accent.
  const [row, setRow] = useState(0);
  const providerIds = useMemo(() => Object.keys(PROVIDER_DEFINITIONS) as ProviderId[], []);
  const [providerIndex, setProviderIndex] = useState(() => {
    const index = providerIds.indexOf(settings.provider);
    return index === -1 ? 0 : index;
  });
  const requestRef = useRef(0);

  /** The draft drives the palette, so accent and theme changes preview live. */
  const palette = useMemo(() => createPalette(draft.accent, draft.theme, depth), [draft.accent, draft.theme, depth]);
  const rt = createTranslator(draft.language);

  const patch = (values: Partial<Settings>) => {
    setDraft((current) => sanitizeDraft({ ...current, ...values }));
    setError('');
    setNotice('');
  };

  const runDiscovery = async (candidate: Settings) => {
    const serial = ++requestRef.current;
    setDiscovering(true);
    setError('');
    setNotice('');
    const result: ModelDiscoveryResult = await discoverProviderModels(candidate);
    if (serial !== requestRef.current) return;
    setModels(result.models);
    setSource(result.source);
    setDiscovering(false);
    setModelIndex(0);
    setQuery('');
    setDraft((current) => sanitizeDraftSettings({
      ...current,
      model: chooseDiscoveredModel(current.model, result.models),
    }));
    if (!result.models.length) setError(rt('noModels'));
  };

  useEffect(() => {
    if (step !== 3) return;
    void runDiscovery(draft);
  }, [step]);

  const filtered = useMemo(() => filterModels(models, query), [models, query]);

  /**
   * One rule decides whether the wizard may move on, and the same rule decides
   * whether it may save, so the last step cannot accept something the steps
   * before it would have refused.
   */
  const goNext = () => {
    setError('');
    const problem = setupProblem(step, draft);
    if (problem) {
      setError(rt(problem));
      return;
    }
    if (step === 4) {
      onSave(commitDraftSettings(draft));
      return;
    }
    setStep((step + 1) as WizardStep);
  };

  useInput((value, key) => {
    // Control chords are left to the shell so a quit key always leaves the
    // line from anywhere, and Escape is the way out of this screen.
    if (key.escape) {
      if (step === 1) {
        onCancel();
        return;
      }
      setStep((current) => (current - 1) as WizardStep);
      setError('');
      setNotice('');
      return;
    }
    if (key.ctrl && (value === 'r' || value === '\u0012')) {
      void runDiscovery(draft);
      return;
    }
    if (key.leftArrow) {
      if (step === 4) setRow((current) => (current + 2) % 3);
      else if (step > 1) setStep((current) => (current - 1) as WizardStep);
      return;
    }
    if (key.rightArrow) {
      if (step === 4) setRow((current) => (current + 1) % 3);
      return;
    }

    if (step === 1) {
      if (key.upArrow) {
        setProviderIndex((current) => (current - 1 + providerIds.length) % providerIds.length);
        return;
      }
      if (key.downArrow) {
        setProviderIndex((current) => (current + 1) % providerIds.length);
        return;
      }
      if (key.return) {
        const provider = providerIds[providerIndex] ?? 'openai';
        setModels([]);
        setSource('offline');
        patch({ provider, ...credentialsForProvider(provider) });
        setField('apiKey');
        setStep(2);
      }
      return;
    }

    if (step === 2) {
      if (key.tab) {
        setField((current) => (current === 'apiKey' ? 'baseUrl' : 'apiKey'));
        return;
      }
      if (key.backspace || key.delete) {
        patch(field === 'apiKey' ? { apiKey: draft.apiKey.slice(0, -1) } : { baseUrl: draft.baseUrl.slice(0, -1) });
        return;
      }
      if (key.return) {
        goNext();
        return;
      }
      if (value && !key.ctrl && !key.meta) {
        if (field === 'apiKey') patch({ apiKey: draft.apiKey + value });
        else patch({ baseUrl: draft.baseUrl + value });
      }
      return;
    }

    if (step === 3) {
      if (discovering) return;
      if (key.upArrow) {
        setModelIndex((current) => Math.max(0, current - 1));
        return;
      }
      if (key.downArrow) {
        setModelIndex((current) => Math.min(Math.max(0, filtered.length - 1), current + 1));
        return;
      }
      if (key.return) {
        const model = filtered[modelIndex];
        if (model) {
          setError('');
          patch({ model });
        } else {
          setError(rt('noModels'));
        }
        return;
      }
      if (key.backspace) {
        setQuery((current) => current.slice(0, -1));
        setModelIndex(0);
        return;
      }
      if (value === 'r' && !query) {
        void runDiscovery(draft);
        return;
      }
      if (value && !key.ctrl && !key.meta) {
        setQuery((current) => current + value);
        setModelIndex(0);
      }
      return;
    }

    if (step === 4 && (key.upArrow || key.downArrow)) {
      setRow((current) => (current + (key.upArrow ? 2 : 1)) % 3);
      return;
    }
    if (key.return) {
      goNext();
      return;
    }
    if (key.tab) {
      setRow((current) => (current + 1) % 3);
      return;
    }
    if (row === 0) {
      const index = LANGUAGES.indexOf(draft.language);
      const next = LANGUAGES[(index + 1) % LANGUAGES.length] ?? 'en';
      patch({ language: next });
      setNotice(rt('cliLanguageChanged', { language: languageLabel(next) }));
      return;
    }
    if (row === 1) {
      const next: Theme = draft.theme === 'dark' ? 'light' : 'dark';
      patch({ theme: next });
      setNotice(rt('cliThemeChanged', { theme: rt(next) }));
      return;
    }
    const next = accentStep(draft.accent, key.shift ? -1 : 1);
    patch({ accent: next });
    setNotice(rt('cliAccentChanged', { accent: next }));
  });

  // Said what the keys do, per step: on the credentials step Tab switches the
  // field and ← goes back, on the review step Enter saves, and the arrows only
  // move on the steps that move with them — a hint that advertises keys a step
  // does not answer is one nobody can act on.
  const hint = step === 1
    ? rt('cliUseArrows')
    : step === 2
      ? rt('wizardFieldHint')
      : step === 3
        ? `${rt('cliUseArrows')} · ${rt('cliSearchModels')} · r: ${rt('retry')}`
        : rt('wizardReviewHint');

  return (
    <Box flexDirection="column" height={height} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={palette.accent}>{`⚙ ${t('settings')}`}</Text>
        <Text color={palette.inkFaint}>{truncateWidth(t('providerSetup'), Math.max(8, width - 20))}</Text>
      </Box>
      <Box marginTop={1}>
        <StepRail palette={palette} step={step} language={settings.language} />
      </Box>
      <Box marginTop={1}>
        {step === 1 ? (
          <ProviderStep palette={palette} providerIds={providerIds} providerIndex={providerIndex} width={width} language={settings.language} />
        ) : null}
        {step === 2 ? (
          <CredentialsStep draft={draft} error={error} field={field} palette={palette} width={width} />
        ) : null}
        {step === 3 ? (
          <DiscoveryStep
            discovering={discovering}
            filtered={filtered}
            modelIndex={modelIndex}
            palette={palette}
            query={query}
            source={source}
            width={width}
            language={settings.language}
          />
        ) : null}
        {step === 4 ? <ReviewStep draft={draft} row={row} palette={palette} width={width} /> : null}
      </Box>
      {error ? (
        <Box marginTop={1}>
          <Notice palette={palette} tone="danger">{truncateWidth(error, width - 4)}</Notice>
        </Box>
      ) : null}
      {notice && !error ? (
        <Box marginTop={1}>
          <Notice palette={palette} tone="success">{truncateWidth(notice, width - 4)}</Notice>
        </Box>
      ) : null}
      <Box flexGrow={1} />
      <Divider palette={palette} width={Math.max(4, width - 2)} />
      <Box justifyContent="space-between">
        <Hint palette={palette}>{truncateWidth(hint, Math.max(8, width - 12))}</Hint>
        <Text color={palette.inkFaint}>{rt('cliStepOf', { current: step, total: 4 })}</Text>
      </Box>
    </Box>
  );
}

function StepRail({ palette, step, language }: { palette: Palette; step: WizardStep; language: Language }): React.ReactElement {
  const t = createTranslator(language);
  return (
    <Box gap={1}>
      {SETUP_STEPS.map((value, index) => (
        <React.Fragment key={value}>
          {index > 0 ? <Text color={palette.lineStrong}>{'›'}</Text> : null}
          <Text bold color={value <= step ? palette.accent : palette.inkFaint}>
            {`${value <= step ? '●' : '○'} ${value} ${t(SETUP_STEP_LABELS[value])}`}
          </Text>
        </React.Fragment>
      ))}
    </Box>
  );
}

function ProviderStep({
  palette,
  providerIds,
  providerIndex,
  width,
  language,
}: {
  palette: Palette;
  providerIds: ProviderId[];
  providerIndex: number;
  width: number;
  language: Language;
}): React.ReactElement {
  const t = createTranslator(language);
  return (
    <Box flexDirection="column">
      <Text color={palette.inkMuted}>{truncateWidth(t('providerDescription'), width - 4)}</Text>
      <Box flexDirection="column" marginTop={1}>
        {providerIds.map((provider, index) => {
          const definition = PROVIDER_DEFINITIONS[provider];
          const active = index === providerIndex;
          const room = Math.max(10, width - 6 - displayWidth(definition.label));
          return (
            <Box flexDirection="column" key={provider}>
              <Text color={active ? palette.accent : palette.inkMuted}>
                {`${active ? '▸' : ' '} ${definition.label}`}
              </Text>
              {active ? (
                <Text color={palette.inkFaint}>{`    ${truncateWidth(t(providerDescriptionKey(provider)), room)}`}</Text>
              ) : null}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

function CredentialsStep({
  draft,
  field,
  error,
  palette,
  width,
}: {
  draft: Settings;
  field: 'apiKey' | 'baseUrl';
  error: string;
  palette: Palette;
  width: number;
}): React.ReactElement {
  const t = createTranslator(draft.language);
  const requiresKey = providerFor(draft.provider).requiresApiKey;
  const keyText = draft.apiKey
    ? '•'.repeat(Math.min(draft.apiKey.length, 40))
    : requiresKey ? t('keyRequired') : t('apiKeyOptional');
  const labelWidth = Math.max(10, displayWidth(t('baseUrl')) + 2);
  return (
    <Box flexDirection="column">
      <Text color={palette.inkMuted}>{truncateWidth(t('endpointHint'), width - 4)}</Text>
      <Box marginTop={1} flexDirection="column">
        <Box>
          <Text color={field === 'apiKey' ? palette.accent : palette.inkFaint}>{`${field === 'apiKey' ? '▸' : ' '} ${t('apiKey')}`}</Text>
          <Text>{' '.repeat(Math.max(1, labelWidth - displayWidth(t('apiKey'))))}</Text>
          <Text color={draft.apiKey ? palette.ink : palette.inkFaint}>{keyText}</Text>
          {field === 'apiKey' ? <Text inverse>{' '}</Text> : null}
        </Box>
        <Box>
          <Text color={field === 'baseUrl' ? palette.accent : palette.inkFaint}>{`${field === 'baseUrl' ? '▸' : ' '} ${t('baseUrl')}`}</Text>
          <Text>{' '.repeat(labelWidth)}</Text>
          <Text color={palette.ink}>{truncateWidth(draft.baseUrl, Math.max(8, width - labelWidth - 6))}</Text>
          {field === 'baseUrl' ? <Text inverse>{' '}</Text> : null}
        </Box>
      </Box>
      {error ? (
        <Box marginTop={1}>
          <Notice palette={palette} tone="danger">{truncateWidth(error, width - 4)}</Notice>
        </Box>
      ) : null}
    </Box>
  );
}

function DiscoveryStep({
  discovering,
  filtered,
  modelIndex,
  palette,
  query,
  source,
  width,
  language,
}: {
  discovering: boolean;
  filtered: string[];
  modelIndex: number;
  palette: Palette;
  query: string;
  source: 'remote' | 'offline';
  width: number;
  language: Language;
}): React.ReactElement {
  const t = createTranslator(language);
  const room = Math.max(6, width - 8);
  return (
    <Box flexDirection="column">
      <Box justifyContent="space-between">
        <Text color={palette.inkFaint}>{t('endpointHint')}</Text>
        <Text color={source === 'remote' ? palette.success : palette.warning}>
          {source === 'remote' ? t('cliRemote') : t('cliOffline')}
        </Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        {discovering ? (
          <Box>
            <Spinner palette={palette} />
            <Text color={palette.accent}>{`  ${t('discovering')}`}</Text>
          </Box>
        ) : (
          <>
            <Text color={palette.inkFaint}>{`${t('searchModels')}  ${query}`}</Text>
            {filtered.length === 0 ? (
              <Text color={palette.inkFaint}>{t('noMatchingModels')}</Text>
            ) : (
              filtered.slice(0, Math.max(3, Math.min(12, width - 10))).map((model, index) => (
                <Text key={model} color={index === modelIndex ? palette.accent : palette.inkMuted}>
                  {`${index === modelIndex ? '▸ ' : '  '}${truncateWidth(model, room)}`}
                </Text>
              ))
            )}
          </>
        )}
      </Box>
    </Box>
  );
}

function ReviewStep({ draft, row, palette, width }: { draft: Settings; row: number; palette: Palette; width: number }): React.ReactElement {
  const t = createTranslator(draft.language);
  const selected = nearestAccent(draft.accent);
  const room = Math.max(8, width - 22);
  const cursor = (index: number) => (row === index ? '▸' : ' ');
  return (
    <Box flexDirection="column">
      <Text color={palette.inkMuted}>{t('modelReady')}</Text>
      <Box flexDirection="column" marginTop={1}>
        <Meta palette={palette} label={t('cliProvider')}>{providerFor(draft.provider).label}</Meta>
        <Meta palette={palette} label={t('cliModel')}>{truncateWidth(draft.model, room)}</Meta>
        <Meta palette={palette} label={t('cliEndpoint')}>{truncateWidth(draft.baseUrl, room)}</Meta>
        <Meta palette={palette} label={t('cliKey')}>
          {draft.apiKey ? '••••••••' : providerFor(draft.provider).requiresApiKey ? t('keyRequired') : t('apiKeyOptional')}
        </Meta>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        <Text color={row === 0 ? palette.accent : palette.inkFaint}>{`${cursor(0)} ${t('cliLanguage')}`}</Text>
        <Text color={palette.inkMuted}>{`    ${languageLabel(draft.language)}`}</Text>
        <Text color={row === 1 ? palette.accent : palette.inkFaint}>{`${cursor(1)} ${t('cliTheme')}`}</Text>
        <Text color={palette.inkMuted}>{`    ${t(draft.theme)}`}</Text>
        <Box marginTop={1}>
          <Text color={row === 2 ? palette.accent : palette.inkFaint}>{`${cursor(2)} ${t('accent')}`}</Text>
          <Box marginLeft={2}>
            {ACCENT_PRESETS.map((value, index) => (
              <Text backgroundColor={value} key={value}>
                {index === selected ? '██' : '▄▄'}
              </Text>
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
