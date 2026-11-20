import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import {
  ADVANCED_AGENT_CONFIG_CATEGORIES,
  chooseDiscoveredModel,
  commitDraftSettings,
  createTranslator,
  credentialsForProvider,
  discoverProviderModels,
  fieldsForCategory,
  fieldLabel,
  fieldPlaceholder,
  filterModels,
  filterProviders,
  languageLabel,
  LANGUAGES,
  providerDescriptionKey,
  providerFor,
  PROVIDER_DEFINITIONS,
  sanitizeDraftSettings,
  setupProblem,
  type AgentConfigCategory,
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
import { Divider, Hint, Meta, Notice, Spinner, SwitchGlyph } from './tui-kit.js';

export type WizardStep = 1 | 2 | 3 | 4;

export interface WizardProps {
  settings: Settings;
  width: number;
  height: number;
  depth: ColorDepth;
  onSave: (settings: Settings) => void;
  onCancel: () => void;
}

export const sanitizeDraft = sanitizeDraftSettings;

type TuiCategory = Extract<AgentConfigCategory, 'model' | 'budget' | 'deliberation' | 'solver' | 'rhythm' | 'memory' | 'permissions' | 'appearance'>;

// The sidebar offers the everyday categories first, then an "Advanced
// settings" fold holding the tuning categories — the same grouping the web
// screen paints, derived from the same list in core.
const TUI_BASIC_CATEGORIES: TuiCategory[] = ['model', 'appearance'];
const TUI_ADVANCED_CATEGORIES: TuiCategory[] = ADVANCED_AGENT_CONFIG_CATEGORIES as TuiCategory[];

type SidebarRow = { kind: 'category'; category: TuiCategory } | { kind: 'advancedHeader' };

const CATEGORY_KEY: Record<TuiCategory, 'catModel' | 'catBudget' | 'catDeliberation' | 'catSolver' | 'catRhythm' | 'catMemory' | 'catPermissions' | 'catAppearance'> = {
  model: 'catModel',
  budget: 'catBudget',
  deliberation: 'catDeliberation',
  solver: 'catSolver',
  rhythm: 'catRhythm',
  memory: 'catMemory',
  permissions: 'catPermissions',
  appearance: 'catAppearance',
};

export function SettingsWizard({ settings, width, height, depth, onSave, onCancel }: WizardProps): React.ReactElement {
  const t = createTranslator(settings.language);
  // `rowIndex` walks the sidebar rows as painted; the fold starts collapsed,
  // since each pane behind it is a wall of knobs.
  const [rowIndex, setRowIndex] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [focus, setFocus] = useState<'sidebar' | 'content'>('content');
  const [category, setCategory] = useState<TuiCategory>('model');

  const sidebarRows = useMemo<SidebarRow[]>(() => {
    const rows: SidebarRow[] = TUI_BASIC_CATEGORIES.map((cat) => ({ kind: 'category', category: cat }));
    rows.push({ kind: 'advancedHeader' });
    if (advancedOpen) {
      for (const cat of TUI_ADVANCED_CATEGORIES) rows.push({ kind: 'category', category: cat });
    }
    return rows;
  }, [advancedOpen]);

  const toggleAdvanced = () => {
    const open = !advancedOpen;
    setAdvancedOpen(open);
    if (open) return;
    // Folding while a tuning pane is open would leave the pane lit over a
    // sidebar with nothing lit in it, so folding returns to the first row.
    if (TUI_ADVANCED_CATEGORIES.includes(category)) {
      setCategory('model');
      setRowIndex(0);
    } else {
      setRowIndex((current) => Math.min(current, TUI_BASIC_CATEGORIES.length));
    }
  };

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [draft, setDraft] = useState<Settings>({ ...settings });
  const [field, setField] = useState<'apiKey' | 'baseUrl'>('apiKey');
  const [models, setModels] = useState<string[]>([]);
  const [modelIndex, setModelIndex] = useState(0);
  const [query, setQuery] = useState('');
  const [discovering, setDiscovering] = useState(false);
  const [source, setSource] = useState<'remote' | 'offline'>('offline');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [row, setRow] = useState(0);
  const providerIds = useMemo(() => Object.keys(PROVIDER_DEFINITIONS) as ProviderId[], []);
  const [providerQuery, setProviderQuery] = useState('');
  const visibleProviders = useMemo(() => filterProviders(providerIds, providerQuery), [providerIds, providerQuery]);
  const [providerIndex, setProviderIndex] = useState(() => {
    const index = providerIds.indexOf(settings.provider);
    return index === -1 ? 0 : index;
  });
  const requestRef = useRef(0);

  // Clio Agent config (budget, solver, …) — read from the bridge, edited here.
  const [agentConfig, setAgentConfig] = useState<Record<string, string | number | boolean | null> | null>(null);
  const [agentConfigLoading, setAgentConfigLoading] = useState(true);
  const [fieldIndex, setFieldIndex] = useState(0);
  const [editBuffer, setEditBuffer] = useState<string | null>(null);

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
    if (category !== 'model' || step !== 3) return;
    void runDiscovery(draft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, step]);

  const filtered = useMemo(() => filterModels(models, query), [models, query]);

  useEffect(() => {
    let cancelled = false;
    const base = (settings.agentUrl || 'http://127.0.0.1:8720').replace(/\/+$/, '');
    setAgentConfigLoading(true);
    (async () => {
      try {
        const res = await fetch(`${base}/api/agent-config`, { headers: { accept: 'application/json' } });
        if (!res.ok) return;
        const payload = (await res.json()) as { values?: Record<string, string | number | boolean | null> };
        if (!cancelled && payload.values) setAgentConfig(payload.values);
      } catch {
        /* offline bridge: fields show defaults */
      } finally {
        if (!cancelled) setAgentConfigLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [settings.agentUrl]);

  const agentFields = useMemo(
    () => fieldsForCategory(category).filter(() => category !== 'model' && category !== 'appearance'),
    [category],
  );

  const saveAgentConfig = async () => {
    if (!agentConfig) return;
    setError('');
    setNotice('');
    try {
      const base = (draft.agentUrl || settings.agentUrl).replace(/\/+$/, '');
      const res = await fetch(`${base}/api/agent-config`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ values: agentConfig }),
      });
      const payload = (await res.json().catch(() => ({}))) as { values?: Record<string, string | number | boolean | null>; error?: string };
      if (!res.ok) throw new Error(payload.error ?? `http_${res.status}`);
      if (payload.values) setAgentConfig(payload.values);
      setNotice(rt('agentConfigSaved'));
    } catch {
      setError(rt('agentConfigFailed'));
    }
  };

  const goNext = () => {
    setError('');
    const problem = setupProblem(step, draft);
    if (problem) {
      setError(rt(problem));
      return;
    }
    if (step === 3) {
      onSave(commitDraftSettings(draft));
      return;
    }
    setStep((step + 1) as 1 | 2 | 3);
  };

  const commitDraft = () => {
    onSave(commitDraftSettings(draft));
    if (agentConfig) void saveAgentConfig();
  };

  useInput((value, key) => {
    if (key.escape) {
      if (editBuffer !== null) { setEditBuffer(null); return; }
      if (focus === 'content') { setFocus('sidebar'); return; }
      onCancel();
      return;
    }
    if (key.tab) {
      setFocus((f) => (f === 'sidebar' ? 'content' : 'sidebar'));
      return;
    }
    // Sidebar navigation: category list on the right
    if (focus === 'sidebar') {
      if (key.upArrow) setRowIndex((r) => (r - 1 + sidebarRows.length) % sidebarRows.length);
      else if (key.downArrow) setRowIndex((r) => (r + 1) % sidebarRows.length);
      else if (key.return) {
        const row = sidebarRows[rowIndex];
        if (!row) return;
        if (row.kind === 'advancedHeader') toggleAdvanced();
        else { setCategory(row.category); setFocus('content'); }
      }
      else if (key.leftArrow || key.rightArrow) setFocus('content');
      return;
    }
    // Content pane
    if (key.leftArrow || key.rightArrow) {
      // ←/→ moves focus to categories; within model step 1 it used to go back —
      // now cycling categories is the OS-style move, Enter drives the form.
      if (category === 'model' && step > 1 && key.leftArrow) { setStep((s) => (s - 1) as 1 | 2 | 3); return; }
      setFocus('sidebar');
      return;
    }

    if (category === 'model') {
      if (key.ctrl && (value === 'r' || value === '\u0012')) { void runDiscovery(draft); return; }
      if (key.ctrl && value === 's') { commitDraft(); return; }
      if (step === 1) {
        if (key.upArrow) { if (visibleProviders.length) setProviderIndex((c) => (c - 1 + visibleProviders.length) % visibleProviders.length); return; }
        if (key.downArrow) { if (visibleProviders.length) setProviderIndex((c) => (c + 1) % visibleProviders.length); return; }
        if (key.return) {
          const provider = visibleProviders[providerIndex] ?? 'openai';
          setModels([]); setSource('offline'); setProviderQuery('');
          patch({ provider, ...credentialsForProvider(provider) });
          setField('apiKey'); setStep(2); return;
        }
        if (key.backspace || key.delete) { setProviderQuery((c) => c.slice(0, -1)); setProviderIndex(0); return; }
        if (value && !key.ctrl && !key.meta) { setProviderQuery((c) => c + value); setProviderIndex(0); }
        return;
      }
      if (step === 2) {
        if (key.tab) { setField((c) => (c === 'apiKey' ? 'baseUrl' : 'apiKey')); return; }
        if (key.backspace || key.delete) { patch(field === 'apiKey' ? { apiKey: draft.apiKey.slice(0, -1) } : { baseUrl: draft.baseUrl.slice(0, -1) }); return; }
        if (key.return) { goNext(); return; }
        if (value && !key.ctrl && !key.meta) {
          if (field === 'apiKey') patch({ apiKey: draft.apiKey + value });
          else patch({ baseUrl: draft.baseUrl + value });
        }
        return;
      }
      if (!discovering) {
        if (key.upArrow) { setModelIndex((c) => Math.max(0, c - 1)); return; }
        if (key.downArrow) { setModelIndex((c) => Math.min(Math.max(0, filtered.length - 1), c + 1)); return; }
        if (key.return) {
          const model = filtered[modelIndex];
          if (model) { setError(''); patch({ model }); setStep(2); setTimeout(() => setStep(3), 0); }
          else setError(rt('noModels'));
          return;
        }
        if (key.backspace) { setQuery((c) => c.slice(0, -1)); setModelIndex(0); return; }
        if (value === 'r' && !query) { void runDiscovery(draft); return; }
        if (value && !key.ctrl && !key.meta) { setQuery((c) => c + value); setModelIndex(0); }
      }
      return;
    }

    if (category === 'appearance') {
      const rows = 5;
      if (key.upArrow || key.downArrow) { setRow((c) => (c + (key.upArrow ? rows - 1 : 1)) % rows); return; }
      if (key.return || value === ' ') {
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
        if (row === 2) {
          const next = accentStep(draft.accent, key.shift ? -1 : 1);
          patch({ accent: next });
          setNotice(rt('cliAccentChanged', { accent: next }));
          return;
        }
        if (row === 3) {
          const next = draft.density === 'comfortable' ? 'compact' : 'comfortable';
          patch({ density: next });
          setNotice(rt('cliDensityChanged', { density: rt(next === 'compact' ? 'densityCompact' : 'densityComfortable') }));
          return;
        }
        const next = !draft.showTimestamps;
        patch({ showTimestamps: next });
        setNotice(rt('cliTimestampsChanged', { state: rt(next ? 'on' : 'off') }));
        return;
      }
      if (key.ctrl && value === 's') { commitDraft(); return; }
      if (key.return) { commitDraft(); return; }
      return;
    }

    // Budget / deliberation / solver / rhythm / memory / permissions: generic fields
    const fields = agentFields;
    if (editBuffer !== null) {
      if (key.return) {
        const f = fields[fieldIndex];
        if (f) {
          const trimmed = editBuffer.trim();
          if (f.type === 'number') {
            if (trimmed === '') { if (f.nullable) setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: null })); }
            else { const n = Number(trimmed); if (Number.isFinite(n)) setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: n })); else setError(rt('expectedNumber')); }
          } else if (f.type === 'select') {
            if (f.options?.includes(trimmed)) setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: trimmed }));
            else setError(rt('chooseOneOf', { options: (f.options ?? []).join(', ') }));
          } else {
            setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: trimmed }));
          }
        }
        setEditBuffer(null);
        return;
      }
      if (key.backspace || key.delete) { setEditBuffer((b) => (b ?? '').slice(0, -1)); return; }
      if (value && !key.ctrl && !key.meta) { setEditBuffer((b) => (b ?? '') + value); return; }
      return;
    }
    if (key.ctrl && value === 's') { void saveAgentConfig(); return; }
    if (key.upArrow) { setFieldIndex((c) => Math.max(0, c - 1)); return; }
    if (key.downArrow) { setFieldIndex((c) => Math.min(Math.max(0, fields.length - 1), c + 1)); return; }
    if (key.return || value === ' ') {
      const f = fields[fieldIndex];
      if (!f) return;
      if (f.type === 'boolean') {
        setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: !(c?.[f.id] ?? false) }));
        setNotice(rt('agentConfigRestart'));
        return;
      }
      if (f.type === 'number' && f.nullable) {
        const cur = agentConfig?.[f.id];
        if (cur === null || cur === undefined) setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: 0 }));
        setEditBuffer(cur === null || cur === undefined ? '0' : String(cur));
        return;
      }
      if (f.type === 'select') {
        const opts = f.options ?? [];
        const cur = String(agentConfig?.[f.id] ?? '');
        const next = opts[(opts.indexOf(cur) + 1) % Math.max(1, opts.length)] ?? cur;
        if (f.id === 'permissions.safety_level') setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: Number(next) }));
        else setAgentConfig((c) => ({ ...(c ?? {}), [f.id]: next }));
        setNotice(rt('agentConfigRestart'));
        return;
      }
      const cur = agentConfig?.[f.id];
      setEditBuffer(cur === null || cur === undefined ? '' : String(cur));
      return;
    }
  });

  const contentWidth = Math.max(20, width - 30);
  const sidebarWidth = 28;

  const hint = focus === 'sidebar'
    ? rt('cliUseArrows')
    : category === 'model'
      ? (step === 1 ? rt('cliUseArrows') : step === 2 ? rt('wizardFieldHint') : `${rt('cliUseArrows')} · ${rt('cliSearchModels')} · r: ${rt('retry')}`)
      : category === 'appearance'
        ? rt('wizardReviewHint')
        : `${rt('cliUseArrows')} · ${rt('save')} ${rt('kbdEnterSend')} · Ctrl+S ${rt('save')}`;

  const renderContent = () => {
    if (category === 'model') {
      return (
        <Box flexDirection="column">
          <Text color={palette.inkFaint}>{`${t('cliStepOf', { current: step, total: 3 })} · ${t('providerSetup')}`}</Text>
          <Box marginTop={1}>
            {step === 1 ? (
              <ProviderStep palette={palette} providerIds={visibleProviders} providerIndex={providerIndex} query={providerQuery} width={contentWidth} height={height} language={settings.language} />
            ) : null}
            {step === 2 ? (
              <CredentialsStep draft={draft} error={error} field={field} palette={palette} width={contentWidth} />
            ) : null}
            {step === 3 ? (
              <DiscoveryStep
                discovering={discovering}
                filtered={filtered}
                modelIndex={modelIndex}
                palette={palette}
                query={query}
                source={source}
                width={contentWidth}
                language={settings.language}
              />
            ) : null}
          </Box>
          {error ? <Box marginTop={1}><Notice palette={palette} tone="danger">{truncateWidth(error, contentWidth - 2)}</Notice></Box> : null}
          {notice && !error ? <Box marginTop={1}><Notice palette={palette} tone="success">{truncateWidth(notice, contentWidth - 2)}</Notice></Box> : null}
        </Box>
      );
    }
    if (category === 'appearance') {
      return (
        <Box flexDirection="column">
          <ReviewStep draft={draft} row={row} palette={palette} width={contentWidth} />
          {notice && !error ? <Box marginTop={1}><Notice palette={palette} tone="success">{truncateWidth(notice, contentWidth - 2)}</Notice></Box> : null}
        </Box>
      );
    }
    const fields = agentFields;
    return (
      <Box flexDirection="column">
        <Text color={palette.inkFaint}>{truncateWidth(t(CATEGORY_KEY[category]), Math.max(8, contentWidth))}</Text>
        <Text color={palette.inkMuted}>{truncateWidth(t('agentConfigRestart'), Math.max(8, contentWidth))}</Text>
        <Box marginTop={1} flexDirection="column">
          {agentConfigLoading ? <Text color={palette.inkFaint}>{t('connecting')}</Text> : fields.length === 0 ? <Text color={palette.inkFaint}>{t('agentConfigUnreachable')}</Text> : null}
          {fields.slice(0, Math.max(4, height - 12)).map((f, i) => {
            const active = i === fieldIndex;
            const raw = agentConfig?.[f.id];
            const isBool = f.type === 'boolean';
            const isNullable = f.type === 'number' && f.nullable;
            const labelText = fieldLabel(f, settings.language);
            const shown = editBuffer !== null && active ? editBuffer : raw === null ? (rt('off')) : isBool ? (raw ? rt('on') : rt('off')) : String(raw ?? '');
            return (
              <Box key={f.id} justifyContent="space-between">
                <Text color={active ? palette.accent : palette.inkFaint} bold={active}>{`${active ? '▸' : ' '} ${truncateWidth(labelText, Math.max(8, contentWidth - 24))}`}</Text>
                <Box gap={1}>
                  {isBool ? <SwitchGlyph palette={palette} on={Boolean(raw)} /> : null}
                  {isNullable ? <SwitchGlyph palette={palette} on={raw !== null} /> : null}
                  {!isBool && !isNullable ? <Text color={active ? palette.ink : palette.inkMuted}>{truncateWidth(shown, 18)}</Text> : <Text color={palette.inkFaint}>{truncateWidth(isNullable ? (raw === null ? '' : String(raw)) : shown, isNullable ? 12 : 0)}</Text>}
                </Box>
              </Box>
            );
          })}
          {editBuffer !== null ? <Text color={palette.inkFaint}>{`▸ ${editBuffer}▌`}</Text> : null}
        </Box>
        {error ? <Box marginTop={1}><Notice palette={palette} tone="danger">{truncateWidth(error, contentWidth - 2)}</Notice></Box> : null}
        {notice && !error ? <Box marginTop={1}><Notice palette={palette} tone="success">{truncateWidth(notice, contentWidth - 2)}</Notice></Box> : null}
      </Box>
    );
  };

  return (
    <Box flexDirection="column" height={height} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={palette.accent}>{`⚙ ${t('settings')}`}</Text>
        <Text color={palette.inkFaint}>{truncateWidth(t('providerSetup'), Math.max(8, width - 20))}</Text>
      </Box>
      <Box marginTop={1} flexDirection="row">
        <Box flexDirection="column" flexGrow={1} width={contentWidth}>
          {renderContent()}
        </Box>
        <Box flexDirection="column" marginLeft={1} width={sidebarWidth}>
          <Text bold color={focus === 'sidebar' ? palette.accent : palette.inkFaint}>{t('settingsCategory')}</Text>
          <Box marginTop={1} flexDirection="column">
            {sidebarRows.map((row, i) => {
              if (row.kind === 'advancedHeader') {
                const selected = i === rowIndex;
                return (
                  <Text key="catAdvanced" color={selected ? palette.accent : palette.inkMuted} bold={selected}>
                    {`${advancedOpen ? '▾' : '▸'} ${t('catAdvanced')}`}
                  </Text>
                );
              }
              const active = i === rowIndex;
              return (
                <Text key={row.category} color={active ? palette.accent : palette.inkMuted} bold={active}>
                  {`${active ? '▸' : ' '} ${t(CATEGORY_KEY[row.category])}`}
                </Text>
              );
            })}
          </Box>
          <Box marginTop={1}>
            <Text color={palette.inkFaint}>{`Ctrl+S ${t('save')}`}</Text>
          </Box>
          <Box marginTop={1}>
            <Text color={palette.inkFaint}>Esc {t('close')}</Text>
          </Box>
        </Box>
      </Box>
      <Box flexGrow={1} />
      <Divider palette={palette} width={Math.max(4, width - 2)} />
      <Box justifyContent="space-between">
        <Hint palette={palette}>{truncateWidth(hint, Math.max(8, width - 12))}</Hint>
        <Text color={palette.inkFaint}>{`${rowIndex + 1}/${sidebarRows.length}`}</Text>
      </Box>
    </Box>
  );
}

function ProviderStep({
  palette,
  providerIds,
  providerIndex,
  query,
  width,
  height,
  language,
}: {
  palette: Palette;
  providerIds: ProviderId[];
  providerIndex: number;
  query: string;
  width: number;
  height: number;
  language: Language;
}): React.ReactElement {
  const t = createTranslator(language);
  const rows = Math.max(3, height - 12);
  const start = Math.max(0, Math.min(providerIndex - rows + 1, providerIds.length - rows));
  const shown = providerIds.slice(start, start + rows);
  return (
    <Box flexDirection="column">
      <Text color={palette.inkMuted}>{truncateWidth(t('providerDescription'), width - 4)}</Text>
      <Text color={palette.inkFaint}>{`${t('searchProviders')}  ${query}`}</Text>
      {providerIds.length === 0 ? (
        <Text color={palette.inkFaint}>{t('providerNoMatches', { query })}</Text>
      ) : null}
      <Box flexDirection="column" marginTop={1}>
        {shown.map((provider, offset) => {
          const index = start + offset;
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
        <Box marginTop={1}>
          <Text color={row === 3 ? palette.accent : palette.inkFaint}>{`${cursor(3)} ${t('density')}`}</Text>
          <Text color={palette.inkMuted}>{`    ${t(draft.density === 'compact' ? 'densityCompact' : 'densityComfortable')}`}</Text>
        </Box>
        <Box marginTop={1}>
          <Text color={row === 4 ? palette.accent : palette.inkFaint}>{`${cursor(4)} ${t('showTimestamps')}`}</Text>
          <Text color={palette.inkMuted}>{`    ${draft.showTimestamps ? t('on') : t('off')}`}</Text>
        </Box>
      </Box>
    </Box>
  );
}
