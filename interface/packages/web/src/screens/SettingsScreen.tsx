import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  agentDraftProblem,
  baseModelFailureKey,
  channelFailureKey,
  channelLabel,
  chooseDiscoveredModel,
  commitDraftSettings,
  createSettings,
  createTranslator,
  credentialsForProvider,
  discoveryProblem,
  filterModels,
  isProviderId,
  languageLabel,
  identityFailureKey,
  MAX_AGENT_NAME_LENGTH,
  MAX_API_KEY_LENGTH,
  MAX_CREDENTIAL_LENGTH,
  MAX_ENDPOINT_LENGTH,
  MAX_PERSON_LENGTH,
  probeSignature,
  providerFor,
  PROVIDER_DEFINITIONS,
  sanitizeDraftSettings,
  savedApiKey,
  setupProblem,
  shouldProbeCatalog,
  AgentRequestError,
  type AgentBaseModel,
  type AgentChannelSetup,
  type AgentDoorSetup,
  type AgentDoorWrite,
  type AgentIdentity,
  type CredentialStatus,
  type EnvApiKey,
  type InterfaceId,
  type Language,
  type ModelDiscoveryResult,
  type ProviderId,
  type SavedApiKey,
  type Settings,
} from '@project-phone/core';
import { Icon, type IconName } from '../components/Icon';
import { Button, FieldLabel, Input, SectionLabel, Select, Textarea, classNames } from '../components/Primitives';
import { useWheelScroll } from '../hooks/useWheelScroll';

interface SettingsScreenProps {
  settings: Settings;
  onUpdate: (settings: Settings) => void;
  onClose: () => void;
  /**
   * The shared file's own credential, for the field in `direct` mode.
   *
   * A status and never a value — the same rule the agent's base-model key follows.
   * It is here because in direct mode the key that matters belongs to this file,
   * and the field above it would otherwise be the one control on the page whose
   * state it cannot describe.
   */
  credential?: CredentialStatus;
  /**
   * Model discovery, asked of whichever party is holding the credential.
   *
   * `options.useEnv` spends the provider's environment variable instead of a typed
   * key, which is the only way a person who keeps their keys in their shell can
   * be served without their key ever reaching this page.
   */
  discover: (
    candidate: Settings,
    signal?: AbortSignal,
    options?: { useEnv?: boolean },
  ) => Promise<ModelDiscoveryResult>;
  /** The agent's own base model, or `null` when the agent cannot be asked. */
  readBaseModel?: () => Promise<AgentBaseModel | null>;
  /**
   * What the agent has been named, or `null` when the agent cannot be asked.
   *
   * `null` and "nobody has named it" are different answers, and both are reachable:
   * the first is a service that is not answering, the second is an answer saying the
   * name is empty. A form that collapsed them would show an empty field over a
   * machine it had never reached, and offering to save into that is how a person
   * ends up reading a refusal as though their agent were broken.
   */
  readIdentity?: () => Promise<AgentIdentity | null>;
  /**
   * Which providers have a key in the agent process's environment. Names and
   * hints only: a page cannot read an environment, and it must not be handed one.
   */
  readEnvApiKeys?: () => Promise<EnvApiKey[]>;
  saveBaseModel?: (input: {
    provider: string;
    model: string;
    baseUrl: string;
    apiKey?: string;
    clearKey?: boolean;
    useEnv?: boolean;
  }) => Promise<AgentBaseModel>;
  /** Names the agent, or takes the name back when given an empty string. */
  saveIdentity?: (selfName: string) => Promise<AgentIdentity>;
  clearIdentity?: () => Promise<AgentIdentity>;
  /**
   * The doors the agent can be reached on, and how each one is configured.
   *
   * `null` is "could not be asked", which is a different answer from a machine
   * with no doors — the second is a setup with an empty list. A form that
   * collapsed them would show a field for a Telegram token over a bridge it had
   * never reached, and offer to save into it.
   */
  readDoors?: () => Promise<AgentChannelSetup | null>;
  saveDoor?: (write: AgentDoorWrite) => Promise<AgentChannelSetup>;
  /**
   * Whether the agent is in preview mode, or `null` when it cannot be asked.
   *
   * A mode of the agent rather than a setting of this page, so it is read from the
   * agent and written to it — the same footing as the name and the doors, and for
   * the same reason: a copy kept in the settings file the terminal also writes
   * would be one the two surfaces disagreed about while the agent obeyed neither.
   *
   * `null` is "could not be asked", which is a different answer from `false` and
   * the reason the box is disabled rather than merely showing as unticked: a
   * checkbox reading "off" over an agent that is in preview mode is a control
   * that says the machine is safe when it is not.
   */
  preview?: boolean | null;
  /**
   * Turns preview mode on or off, resolving once the agent has been told.
   *
   * Not a save-button field: this is the one thing on the screen that is expected
   * to take effect at once, and a person who ticks a box called "preview mode" and
   * then has to find a Save button to make the agent stop writing files has been
   * told the opposite of what happened.
   */
  setPreview?: (on: boolean) => Promise<void>;
}

const PROVIDER_ICONS: Partial<Record<ProviderId, IconName>> = {
  openrouter: 'globe',
  ollama: 'server',
  lmstudio: 'server',
};

function providerIcon(provider: ProviderId): IconName {
  return PROVIDER_ICONS[provider] ?? 'cpu';
}

const isValidAccent = (value: string): boolean => /^#[0-9a-f]{6}$/i.test(value);

const INTERFACES: Array<{ id: InterfaceId; label: 'interfaceAgent' | 'interfaceDirect'; hint: 'interfaceAgentHint' | 'interfaceDirectHint'; icon: IconName }> = [
  { id: 'agent', label: 'interfaceAgent', hint: 'interfaceAgentHint', icon: 'spark' },
  { id: 'direct', label: 'interfaceDirect', hint: 'interfaceDirectHint', icon: 'cpu' },
];

/**
 * The providers on offer, as a choice of who to talk to.
 *
 * Shared rather than written twice, because the two modes offer the same list
 * for different reasons and a form that drifted between them would be a form
 * that offered a different set of providers depending on which tab it was on.
 */
function ProviderGrid({
  selected,
  onChoose,
  t,
}: {
  selected: ProviderId;
  onChoose: (provider: ProviderId) => void;
  t: (key: 'apiKeyRequiredShort' | 'apiKeyNoneShort') => string;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {(Object.keys(PROVIDER_DEFINITIONS) as ProviderId[]).map((provider) => {
        const active = selected === provider;
        return (
          <button
            aria-pressed={active}
            className={classNames(
              'instrument-panel flex items-center gap-3 rounded-xl p-3 text-left',
              active ? 'accent-border ring-1 ring-[var(--accent-halo)]' : 'hover:border-[var(--line-strong)]',
            )}
            key={provider}
            onClick={() => onChoose(provider)}
            type="button"
          >
            <Icon className={active ? 'accent-text shrink-0' : 'shrink-0 text-[var(--ink-muted)]'} name={providerIcon(provider)} size={16} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-[var(--ink)]">{PROVIDER_DEFINITIONS[provider].label}</span>
              <span className="block truncate text-[0.66rem] text-[var(--ink-faint)]">
                {providerFor(provider).requiresApiKey ? t('apiKeyRequiredShort') : t('apiKeyNoneShort')}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * Which model, out of everything that provider offers.
 *
 * One component for both interfaces, and that is the point of the request it
 * answers: in `agent` mode this used to be a bare text field with three guessed
 * ids behind it, so there was no way to find out what a provider actually had.
 * Fetching a catalogue needs a credential, each interface's holder is a different
 * process, and neither of them should have to hand it to the page — so both ask
 * the party that has it, and both get the same form back.
 *
 */
function ModelSection({
  draft, filtered, modelQuery, discovering, source, onChoose, onQuery, onDiscover,
}: {
  draft: Settings;
  filtered: string[];
  modelQuery: string;
  discovering: boolean;
  source: 'remote' | 'offline';
  onChoose: (model: string) => void;
  onQuery: (value: string) => void;
  onDiscover: () => void;
}) {
  const t = useMemo(() => createTranslator(draft.language), [draft.language]);
  // The chosen model stays in the list whatever the search says, so filtering
  // cannot quietly change what is configured — a `<Select>` that dropped the
  // current value would show the first match instead, and saving would then
  // write a model nobody picked.
  const options = [...new Set([draft.model, ...filtered])].filter(Boolean);
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-3">
        <SectionLabel icon="search">{t('model')}</SectionLabel>
        <div className="flex items-center gap-2">
          {/*
            Kept, although leaving the key field now fetches the list on its own.
            Asking is a thing a person may still want to do: to repeat a probe
            whose answer has gone stale, to re-read a provider after a key was
            rotated somewhere this page cannot see, or to fetch a catalogue at all
            on an install where there is no key to fetch it with. A button that
            only exists because it is the only way is a button that gets deleted
            by whoever reads the code next, and then there is no retry at all.
          */}
          <Button disabled={discovering} icon="search" onClick={onDiscover} variant="outline">
            {discovering ? t('discovering') : t('discover')}
          </Button>
        </div>
      </div>
      <Select onChange={(event) => onChoose(event.target.value)} value={draft.model}>
        <option value="">{t('modelPlaceholder')}</option>
        {options.map((model) => <option key={model} value={model}>{model}</option>)}
      </Select>
      <div className="mt-2 flex items-center gap-2">
        <div className="relative flex-1">
          <Icon className="pointer-events-none absolute left-3 top-2.5 text-[var(--ink-faint)]" name="search" size={15} />
          <Input className="pl-10" onChange={(event) => onQuery(event.target.value)} placeholder={t('searchModels')} value={modelQuery} />
        </div>
        <span
          className={classNames(
            'shrink-0 text-[0.66rem] uppercase tracking-[0.14em]',
            source === 'remote' ? 'text-[var(--ok)]' : 'text-[var(--ink-faint)]',
          )}
          title={source === 'offline' ? t('offlineFallback') : t('discovered')}
        >
          {source === 'remote' ? t('cliRemote') : t('cliOffline')}
        </span>
      </div>
      <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('modelIdHint')}</p>
    </section>
  );
}

/**
 * Provider, credential and endpoint — the three fields that decide where a
 * request goes.
 *
 * One component for both interfaces because both need all three and only differ in
 * who reads the answer.
 */
function CredentialSection({
  draft,
  patch,
  onApiKey,
  onCommitApiKey,
  onChooseProvider,
  onToggleEnvKey,
  envKey,
  envActive,
  fileEnv,
  savedKey,
  onForgetKey,
  forgettingKey,
}: {
  draft: Settings;
  patch: (values: Partial<Settings>) => void;
  /** The key field, which also gives the environment choice back when used. */
  onApiKey: (value: string) => void;
  /**
   * The key field was left, so the key is as finished as a secret gets.
   *
   * The moment that asks for is when the models list is fetched, which is why it
   * is a separate prop and not part of `onApiKey`: typing fires on every
   * character and none of them is a credential anybody could spend.
   */
  onCommitApiKey: () => void;
  onChooseProvider: (provider: ProviderId) => void;
  onToggleEnvKey: () => void;
  /** The provider's environment credential, or `null` when it has none. */
  envKey: EnvApiKey | null;
  envActive: boolean;
  /** The variable the agent's stored base model already reads, if any. */
  fileEnv: string;
  /** The key already saved on whichever machine is answering, if there is one. */
  savedKey: SavedApiKey;
  /** Offered only where this page owns the write; `null` where it does not. */
  onForgetKey: (() => void) | null;
  forgettingKey: boolean;
}) {
  const t = useMemo(() => createTranslator(draft.language), [draft.language]);
  const requiresKey = providerFor(draft.provider).requiresApiKey;
  return (
    <>
      <section>
        <SectionLabel icon="cpu">{t('provider')}</SectionLabel>
        <ProviderGrid onChoose={onChooseProvider} selected={draft.provider} t={t} />
      </section>
      <section className="space-y-4">
        <div>
          <FieldLabel hint={requiresKey ? undefined : t('apiKeyOptional')}>{t('apiKey')}</FieldLabel>
          {/* The mask is the *placeholder*, never the value. A field whose value
              were the mask would post those dots to the agent as a key, and a
              field whose value were the real key would put the credential in this
              page's memory — which is the one thing the whole arrangement exists
              to avoid. As a placeholder it says what is saved, stays out of every
              save, and disappears the moment anybody types. */}
          <div className="flex items-center gap-2">
            <div className="relative min-w-0 flex-1">
              <Icon className="pointer-events-none absolute left-3 top-3 text-[var(--ink-faint)]" name="key" size={17} />
              <Input
                autoComplete="off"
                className="pl-10"
                maxLength={MAX_API_KEY_LENGTH}
                onBlur={onCommitApiKey}
                onChange={(event) => onApiKey(event.target.value)}
                placeholder={savedKey.mask || t('apiKeyPlaceholder', { provider: providerFor(draft.provider).label })}
                type="password"
                value={draft.apiKey}
              />
            </div>
            {onForgetKey ? (
              <Button disabled={forgettingKey} onClick={onForgetKey} variant="quiet">
                {t('apiKeyForget')}
              </Button>
            ) : null}
          </div>
          {/*
            The third answer, and the only one that is not a value in the box.

            It is offered only when this process really has a key for the vendor
            that is selected, because a button that is always there is a button
            that fails on press. Pressing it empties the field, and typing in the
            field takes the choice back: the two are the same decision, so letting
            both stand would leave the form asserting two things at once about
            which credential the next request will use.
          */}
          {envKey ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button
                aria-pressed={envActive}
                icon={envActive ? 'check' : 'key'}
                onClick={onToggleEnvKey}
                variant="outline"
              >
                {t('useEnvKey')}
              </Button>
              <span className="text-[0.68rem] leading-5 text-[var(--ink-faint)]">
                {envActive
                  ? t('envKeyUsing', { variable: envKey.variable })
                  : t('envKeyDetected', { variable: envKey.variable })}
              </span>
            </div>
          ) : null}
          {/*
            Where the saved key is, which is the one thing an empty field cannot say
            about itself. Three lines can be true at once and none of them
            contradicts another: this is a fact about the file, the two below are
            one about the variable it names and one about the draft's choice.
          */}
          {savedKey.present ? (
            <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
              {t('apiKeyOnFile', { hint: savedKey.mask })}
            </p>
          ) : null}
          {fileEnv ? (
            <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
              {t('envKeyFromFile', { variable: fileEnv })}
            </p>
          ) : null}
        </div>
        <div>
          <FieldLabel>{t('baseUrl')}</FieldLabel>
          <div className="relative">
            <Icon className="pointer-events-none absolute left-3 top-3 text-[var(--ink-faint)]" name="server" size={17} />
            <Input
              className="pl-10"
              maxLength={MAX_ENDPOINT_LENGTH}
              onChange={(event) => patch({ baseUrl: event.target.value })}
              placeholder={t('baseUrlPlaceholder')}
              spellCheck={false}
              value={draft.baseUrl}
            />
          </div>
          <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('endpointHint')}</p>
        </div>
      </section>
    </>
  );
}

/**
 * One door, and everything a person needs in order to open it.
 *
 * Every door is here, not only the ones already open: the ones worth setting up
 * are exactly the ones that are shut, so a section built from the open doors could
 * not answer the question it exists for.
 *
 * A credential is a password field that is empty on purpose. The value is never
 * read back — a page that could read a bot token would be a page that could post
 * it somewhere — so an empty field means "keep the one on file", the same
 * convention the base model's key field uses, and a separate button is how one is
 * removed. What the section does report is *where* the credential the agent will
 * use is: on this file, or in the environment variable beside it, which is the fact
 * a form has to get right or it will show a pasted token as the live one.
 */
function DoorCard({
  door,
  language,
  saving,
  onSave,
}: {
  door: AgentDoorSetup;
  language: Language;
  saving: boolean;
  onSave: (write: AgentDoorWrite) => Promise<void>;
}) {
  const t = useMemo(() => createTranslator(language), [language]);
  const [open, setOpen] = useState(door.enabled);
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [admitted, setAdmitted] = useState(() => door.allowed.join('\n'));
  const [openToAll, setOpenToAll] = useState(door.acceptFromAnyone);
  const [address, setAddress] = useState(door.address);
  const receiver = door.host ? `http://${door.host}:${door.port ?? 8730}${door.path ?? ''}` : '';

  return (
    <div className="instrument-panel rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-medium text-[var(--ink)]">
          <Icon name="link" size={15} />
          {channelLabel(door.id)}
        </span>
        <button
          aria-pressed={open}
          className={classNames(
            'rounded-lg border px-2.5 py-1 text-xs font-medium transition',
            open
              ? 'accent-border bg-[var(--accent-soft)] accent-text'
              : 'border-[var(--line)] text-[var(--ink-muted)] hover:border-[var(--line-strong)]',
          )}
          onClick={() => setOpen((current) => !current)}
          type="button"
        >
          {t('doorOpen')}
        </button>
      </div>

      <div className="mt-3 space-y-3">
        {door.credentials.map((credential) => (
          <div key={credential.key}>
            <FieldLabel hint={credential.env || undefined}>{credential.label}</FieldLabel>
            <div className="flex items-center gap-2">
              <Input
                autoComplete="off"
                className="font-mono"
                maxLength={MAX_CREDENTIAL_LENGTH}
                onChange={(event) => setTyped((current) => ({ ...current, [credential.key]: event.target.value }))}
                placeholder="••••••••"
                spellCheck={false}
                type="password"
                value={typed[credential.key] ?? ''}
              />
              {credential.present ? (
                <Button
                  onClick={() => {
                    // The field goes empty with it: a form still showing the text
                    // of a credential that was just removed is a form asserting
                    // two things at once about which secret the next save sends.
                    setTyped((current) => {
                      const { [credential.key]: _gone, ...rest } = current;
                      return rest;
                    });
                    void onSave({ id: door.id, credentials: { [credential.key]: null } });
                  }}
                  variant="quiet"
                >
                  {t('doorForget')}
                </Button>
              ) : null}
            </div>
            <p className="mt-1.5 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
              {credential.envPresent
                ? t('doorKeyFromEnv', { variable: credential.env })
                : credential.present
                  ? t('doorKeyOnFile', { hint: credential.hint })
                  : t('doorKeyNone')}
            </p>
          </div>
        ))}

        {door.allowlist ? (
          <div>
            {/* A real checkbox, for the reason the preview-mode one is: this is one
                on/off setting whose whole meaning is the tick. It sits above the
                allowlist rather than inside it because the two are answers to
                different questions — "who do I know" and "do I answer strangers
                too" — and putting one inside the other makes it read as a comment
                on the list rather than as its own decision. */}
            <label className="flex items-start gap-2.5">
              <input
                checked={openToAll}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                onChange={(event) => setOpenToAll(event.target.checked)}
                type="checkbox"
              />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-[var(--ink)]">
                  {t('doorOpenToAll')}
                </span>
                <span className="mt-0.5 block text-[0.68rem] leading-5 text-[var(--ink-faint)]">
                  {t('doorOpenToAllHint')}
                </span>
                {openToAll ? (
                  <span className="mt-0.5 block text-[0.68rem] leading-5 text-[var(--accent-text)]">
                    {t('doorOpenToAllWarn')}
                  </span>
                ) : null}
              </span>
            </label>

            <div className="mt-3">
              <FieldLabel hint={door.allowlist}>{t('doorAdmit')}</FieldLabel>
              {/* A textarea because the list arrives one id per line: a list pasted
                  out of a chat app or a spreadsheet column comes back as one long
                  entry in a single-line field, and a person whose paste turned into
                  an id the door does not recognise has a form that looks broken
                  rather than a sentence about what it wanted. */}
              <Textarea
                disabled={openToAll}
                onChange={(event) => setAdmitted(event.target.value)}
                placeholder="819012345678"
                spellCheck={false}
                value={admitted}
              />
              <p className="mt-1.5 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
                {/* Silent while the door is open to everyone, because there is
                    nothing wrong with it and a warning shown next to a setting
                    nobody changed trains people to ignore this colour. */}
                {open && !door.admits && !openToAll ? t('doorAdmitsNobody') : t('doorAdmitHint')}
              </p>
            </div>
          </div>
        ) : null}

        {door.prefix ? (
          <div>
            <FieldLabel>{t('doorAddress')}</FieldLabel>
            <Input
              className="font-mono"
              onChange={(event) => setAddress(event.target.value)}
              placeholder={`${door.prefix}…`}
              spellCheck={false}
              value={address}
            />
            <p className="mt-1.5 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('doorAddressHint')}</p>
          </div>
        ) : null}

        {door.needsWebhook ? (
          <p className="text-[0.68rem] leading-5 text-[var(--ink-faint)]">
            {door.webhookOn
              ? t('doorPush', { endpoint: receiver })
              : t('doorReceiverHint', { endpoint: receiver })}
          </p>
        ) : null}
        {door.id === 'webhook' && receiver ? (
          <p className="text-[0.68rem] leading-5 text-[var(--ink-faint)]">
            {t('doorReceiverHint', { endpoint: receiver })}
          </p>
        ) : null}
      </div>

      <div className="mt-4 flex justify-end">
        <Button disabled={saving} icon="check" onClick={() => void onSave({
          id: door.id,
          enabled: open,
          ...(door.credentials.length
            ? { credentials: Object.fromEntries(
              door.credentials
                .filter((credential) => (typed[credential.key] ?? '').trim())
                .map((credential) => [credential.key, (typed[credential.key] ?? '').trim()]),
            ) }
            : {}),
          ...(door.allowlist
            ? {
              allowed: splitIds(admitted),
              acceptFromAnyone: openToAll,
            }
            : {}),
          ...(door.prefix ? { address: address.trim() || null } : {}),
        })} variant="primary">
          {t('save')}
        </Button>
      </div>
    </div>
  );
}

/**
 * Preview mode: the agent may talk and may not act.
 *
 * A real checkbox, and not one of the `aria-pressed` buttons the rest of this file
 * uses for its toggles. Those are for choosing between named options, where the
 * label is the value; this is one on/off setting whose whole meaning is the tick,
 * and a checkbox is the control that says so — it is reachable by keyboard without
 * anything here having to reimplement arrow keys, and it is read as a tick by a
 * screen reader rather than as a button that happens to be styled like one.
 *
 * The label is part of the control, so the whole row is the hit target and there is
 * no "did I click the right bit" to answer. And `null` — an agent that could not be
 * asked — renders as a disabled box rather than an unticked one, because an
 * unticked box is a claim that the agent is free to act, and on a machine that
 * could not be reached nothing here knows that.
 */
function PreviewModeCard({
  language,
  preview,
  saving,
  onToggle,
}: {
  language: Language;
  preview: boolean | null;
  saving: boolean;
  onToggle: (next: boolean) => void;
}) {
  const t = useMemo(() => createTranslator(language), [language]);
  const on = preview === true;
  const unreachable = preview === null;
  const describedBy = unreachable ? 'preview-mode-state' : undefined;

  return (
    <div className="instrument-panel rounded-xl p-4">
      <div className="flex items-start gap-3">
        <input
          aria-describedby={describedBy}
          checked={on}
          className="preview-toggle mt-0.5 h-4 w-4 shrink-0 cursor-pointer disabled:cursor-not-allowed"
          disabled={unreachable || saving}
          id="preview-mode"
          onChange={(event) => onToggle(event.target.checked)}
          type="checkbox"
        />
        <div className="min-w-0">
          <label
            className={classNames(
              'flex items-center gap-2 text-sm font-medium',
              unreachable ? 'text-[var(--ink-muted)]' : 'cursor-pointer text-[var(--ink)]',
            )}
            htmlFor="preview-mode"
          >
            <Icon name="shield" size={15} />
            {t('previewMode')}
          </label>
          <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
            {unreachable ? t('previewModeUnreachable') : t('previewModeHint')}
          </p>
          <p
            className={classNames(
              'mt-2 text-[0.68rem] font-medium',
              on ? 'accent-text' : 'text-[var(--ink-muted)]',
            )}
            id="preview-mode-state"
          >
            {saving
              ? t('previewModeSaving')
              : unreachable
                ? t('previewModeUnreachable')
                : on
                  ? t('previewModeOn')
                  : t('previewModeOff')}
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Ids as a person types them, as the list a door admits.
 *
 * Split on newlines, commas and spaces, because a list of chat ids pasted out of
 * a chat is separated by whichever of those the thing it was copied from used —
 * and a single entry of `819012345678, 819012345679` is an id no door recognises,
 * which is a door that admits nobody and says it is open.
 */
function splitIds(value: string): string[] {
  return value.split(/[\s,]+/).map((entry) => entry.trim()).filter(Boolean);
}

/**
 * Every setting, on one page.
 *
 * This replaces a four-step wizard that asked a person to press Next three
 * times to reach the thing they came for, and that kept the language and theme
 * controls behind the model step. The validation rules are still the shared
 * `setupProblem`, `agentProblem` and `baseModelProblem`, so the terminal and the
 * page still refuse the same drafts; only the order and the amount of clicking
 * changed.
 *
 * The provider, key, endpoint and model fields appear in *both* modes, because
 * in both modes somebody has to be able to say which model this is talking to.
 * What differs is who reads the answer:
 *
 *   - `direct` — this surface makes the calls, so the settings are this
 *     surface's own and are saved with everything else.
 *   - `agent`  — the agent makes its own calls through its gateway, and the
 *     catalogue it routes over lives in the agent's configuration with provider
 *     keys in the environment. A person with one key and no environment set up
 *     has no way to express that there, so the agent also accepts a single
 *     *base model* written to its own home and preferred over the catalogue.
 *     This form writes that; the key never comes back out, which is why the
 *     field can be left empty to mean "keep the one on file" — and why the mask
 *     of the saved key is in the field's *placeholder* rather than its value, so
 *     a restart shows a configured provider as configured instead of as a blank.
 *
 * Hiding these fields in agent mode — which is what this used to do — left an
 * agent with no way to be given a model at all, and a gateway error on every
 * question was the only symptom. The messaging doors below are the same mistake
 * one level out: a bot token existed only as the name of an environment variable,
 * so a person who had installed the agent was told it could be reached on Telegram
 * and had nowhere at all to put the token.
 */
export function SettingsScreen({
  settings,
  onUpdate,
  onClose,
  discover,
  readBaseModel,
  readEnvApiKeys,
  readIdentity,
  saveBaseModel,
  saveIdentity,
  clearIdentity,
  readDoors,
  saveDoor,
  preview = null,
  setPreview,
  credential = { present: false, source: 'none', hint: '' },
}: SettingsScreenProps) {
  const t = useMemo(() => createTranslator(settings.language), [settings.language]);
  const [draft, setDraft] = useState<Settings>(() => ({ ...settings }));
  const [models, setModels] = useState<string[]>(() => (settings.model ? [settings.model] : []));
  const [source, setSource] = useState<'remote' | 'offline'>('offline');
  const [discovering, setDiscovering] = useState(false);
  const [modelQuery, setModelQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const requestSerial = useRef(0);
  const abort = useRef<AbortController | null>(null);
  // The credential the last discovery run actually asked with, or `null` while
  // nothing has been asked. It is what lets a run that nobody requested be
  // compared against the last one instead of against the clock: the answer to
  // "may this page go and ask?" is not "is it a while since the last time", it is
  // "would this be a different question".
  const probedWith = useRef<string | null>(null);

  // The agent's own model, which is not in the shared settings: it belongs to the
  // agent's configuration and to nobody else's.
  const [base, setBase] = useState<AgentBaseModel | null>(null);
  const [baseSaving, setBaseSaving] = useState(false);
  // Whether the write that takes the saved key back is in flight. Held apart from
  // `baseSaving` because it is a different write with a different consequence:
  // that one replaces the model, this one leaves it exactly as it is and takes
  // away the credential underneath it.
  const [baseKeyForgetting, setBaseKeyForgetting] = useState(false);
  // Whether the person has touched a base-model field. It is what makes a base
  // model required rather than optional: an install that has never been asked
  // about one is not made to supply a key to save its language.
  const [baseTouched, setBaseTouched] = useState(false);
  // Which providers have a key in the agent process's environment, and which one
  // this draft has chosen to spend. The provider is remembered rather than a bare
  // flag so that changing provider drops the choice on its own — a key that
  // belongs to OpenAI is not a key for Groq, and nothing else in this form
  // carries a credential across a provider change either.
  const [envKeys, setEnvKeys] = useState<EnvApiKey[]>([]);
  const [envProvider, setEnvProvider] = useState<ProviderId | null>(null);
  // The name the agent has been given, kept out of `draft` for the same reason the
  // base model is: it belongs to the agent's configuration and to nobody else's, so
  // this form reads it from the agent rather than out of the shared settings file the
  // terminal also writes.
  //
  // `null` is "could not be asked", which is a different thing from an empty name and
  // is the reason the field is disabled rather than editable in that state: there is
  // nowhere to write, and a field that accepts an edit it cannot save is a lie.
  const [identity, setIdentity] = useState<AgentIdentity | null>(null);
  const [name, setName] = useState('');
  const [nameSeeded, setNameSeeded] = useState(false);
  // The doors, kept out of `draft` for the reason the base model is: a bot token
  // belongs to the agent's own configuration and to nobody else's. Each card
  // saves itself, so there is one write per door and a failure names the door that
  // failed rather than the form.
  const [doors, setDoors] = useState<AgentChannelSetup | null>(null);
  const [doorSaving, setDoorSaving] = useState<string | null>(null);
  // Whether the write that turns preview mode on or off is in flight. The value
  // itself is not held here: it belongs to the agent, and the view this screen is
  // given comes back with the answer after the write, so a second copy would be a
  // second thing to fall out of step with the machine.
  const [previewSaving, setPreviewSaving] = useState(false);
  // What the service in front of us can do, and whether it answered at all.
  const seeded = useRef(false);
  // The body of this page, which is the only thing on the screen that scrolls.
  // The ref is what lets a wheel over the pinned header still turn the page; see
  // `useWheelScroll`.
  const body = useRef<HTMLDivElement>(null);
  const wheelToBody = useWheelScroll(body);

  const agentMode = draft.interface === 'agent';
  const envKey = useMemo(
    () => envKeys.find((entry) => entry.provider === draft.provider && entry.present) ?? null,
    [draft.provider, envKeys],
  );
  const envActive = envKey !== null && envProvider === draft.provider;
  /**
   * The variable the agent's stored base model already reads, if it reads one.
   *
   * Shown under the field whether or not the button is there, because a
   * hand-edited file and a shell set up before this page existed both end up here,
   * and an empty key field next to a working agent was the confusing part.
   */
  const fileEnv = base?.keyEnv ?? '';
  /**
   * The key already saved, and whether this page may take it back.
   *
   * Asked of `savedApiKey` rather than read off `base` here, because which file the
   * key lives in is a function of the mode and not of this component: in agent mode
   * it is the agent's own base-model file, in direct mode the shared settings file.
   * Answering it in two places is how the browser ends up describing the shared
   * file's key beside a model the agent has no credential for.
   *
   * `base` is `null` until the agent answers, so on the way in this says nothing is
   * saved — which is why the field's placeholder only starts claiming a key once
   * the agent has been asked, rather than flashing an empty mask and then the
   * real one on every open.
   */
  const savedKey: SavedApiKey = useMemo(
    () => savedApiKey(
      agentMode,
      // The two reports name the same two facts differently — the agent's file
      // says `keyPresent`, the shared file's status says `present` — and the rule
      // is written against one spelling of each so it can be tested and reused.
      base === null ? null : { present: base.keyPresent, hint: base.keyHint },
      credential,
    ),
    [agentMode, base, credential],
  );

  // Adopt a change made elsewhere, but never over an edit in progress.
  useEffect(() => {
    if (dirty) return;
    setDraft((current) => (sameSettings(current, settings) ? current : { ...settings }));
    setModels((current) => (current.includes(settings.model) || !settings.model ? current : [settings.model, ...current]));
  }, [dirty, settings]);

  useEffect(() => () => abort.current?.abort(), []);

  /**
   * Which keys are already in the agent's environment.
   *
   * Read once, on the way in, because the environment of a running process does
   * not change under a page: the person who exports a key does it in a shell and
   * restarts, and this is a form, not a watcher. An empty answer is the ordinary
   * one and costs nothing — no button, and the rest of the form as it was.
   */
  useEffect(() => {
    if (!readEnvApiKeys) return;
    let live = true;
    void readEnvApiKeys()
      .then((found) => {
        if (live) setEnvKeys(found);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [readEnvApiKeys]);

  // Ask the agent what it is using, and let that answer the form.
  //
  // Seeding the draft from it is the whole point: without it the fields show a
  // fresh install's default — a provider and a model nobody chose, and an empty
  // key that looks like a missing one — while the agent is running something
  // else entirely. Only once, and never over an edit, because a form that
  // rewrote itself under the cursor would be worse than one that starts wrong.
  useEffect(() => {
    if (!agentMode || !readBaseModel) return;
    let live = true;
    void readBaseModel()
      .then((found) => {
        if (!live) return;
        setBase(found);
        if (!found?.configured || seeded.current) return;
        seeded.current = true;
        setDraft((current) => {
          const provider = isProviderId(found.provider) ? found.provider : current.provider;
          const next = sanitizeDraftSettings({
            ...current,
            provider,
            baseUrl: found.baseUrl || PROVIDER_DEFINITIONS[provider].defaultBaseUrl,
            model: found.model || PROVIDER_DEFINITIONS[provider].defaultModel,
            // Deliberately left empty: the key is on the agent's side and this
            // page has never held it. `fileEnv` is what says so underneath, when
            // the agent reads one out of the environment rather than from a file.
            apiKey: '',
          });
          setModels((currentModels) => (
            currentModels.includes(next.model) ? currentModels : [next.model, ...currentModels]
          ));
          return next;
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [agentMode, readBaseModel]);

  /**
   * What the agent has been named.
   *
   * Read on the way in, and the field shows the answer rather than its own default:
   * a form that opens empty over an agent somebody has already named invites a
   * second name, and the person only finds out which one is live when the two
   * surfaces disagree. Seeding happens once and never over an edit — a form that
   * rewrote itself under the cursor would be worse than one that starts wrong.
   */
  useEffect(() => {
    if (!agentMode || !readIdentity) return;
    let live = true;
    void readIdentity()
      .then((found) => {
        if (!live) return;
        setIdentity(found);
        if (nameSeeded) return;
        setNameSeeded(true);
        setName(found?.selfName ?? '');
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [agentMode, nameSeeded, readIdentity]);

  /**
   * Which doors the agent has, and what each one still needs.
   *
   * Read on the way in and never again: the doors are opened by the agent's own
   * processes, so an answer that changed under the form would be one process
   * having restarted — and the honest answer to that is "restart the agent",
   * which is what the notice after a save says.
   */
  useEffect(() => {
    if (!agentMode || !readDoors) return;
    let live = true;
    void readDoors()
      .then((found) => {
        if (live) setDoors(found);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [agentMode, readDoors]);

  /**
   * A name typed into the field.
   *
   * A separate updater rather than `patch` for the same reason `patchBase` exists:
   * the name is not in the shared settings, so an edit here must not mark the draft
   * dirty or set an error about something this form does not own.
   */
  const patchName = (value: string) => {
    setName(value);
    setDirty(true);
    setError(null);
    setNotice(null);
  };

  const patch = (values: Partial<Settings>) => {
    setDraft((current) => sanitizeDraftSettings({ ...current, ...values }));
    setDirty(true);
    setError(null);
    setNotice(null);
  };

  /**
   * A patch that counts as editing the base model.
   *
   * Separate from `patch` only in what it records. Appearance and the agent's own
   * address are not the base model, and a person who has never been asked about
   * one must not be held to its rules — that is what made this page refuse to
   * save a language change on a fresh install.
   */
  const patchBase = (values: Partial<Settings>) => {
    patch(values);
    if (agentMode) setBaseTouched(true);
  };

  /**
   * A key typed into the field, which takes the environment back.
   *
   * The two are one decision — which credential the next request spends — so the
   * one that was made last is the one that stands. Without this a form could sit
   * there saying "using OPENAI_API_KEY" above a key somebody had just pasted, and
   * the reader would have no way to tell which of the two was about to be sent.
   */
  const patchApiKey = (value: string) => {
    setEnvProvider(null);
    patchBase({ apiKey: value });
  };

  const toggleEnvKey = () => {
    if (!envKey) return;
    // Pressing it empties the field, because the point of the button is a base
    // model that needs no key in this page at all, and leaving a pasted key
    // beside a button that says otherwise would be a form contradicting itself.
    setEnvProvider((current) => (current === draft.provider ? null : draft.provider));
    patchBase({ apiKey: '' });
  };

  const runDiscovery = useCallback(async (candidate: Settings, useEnv: boolean) => {
    const serial = ++requestSerial.current;
    // Written before the first await, so a second run started while this one is
    // in flight compares against this credential rather than against the one
    // before it. Every run records, however it was asked for.
    probedWith.current = probeSignature(candidate, useEnv);
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setDiscovering(true);
    setError(null);
    setNotice(null);
    try {
      const result = await discover(candidate, controller.signal, { useEnv });
      if (serial !== requestSerial.current) return;
      setModels(result.models);
      setSource(result.source);
      setDraft((current) => {
        // A catalogue that came from a failed probe is a list of guesses, and a
        // guess must not overwrite a model somebody already chose. Only a real
        // answer, or an empty field with nothing to lose, may move it.
        const model = result.source === 'remote' || !current.model
          ? chooseDiscoveredModel(current.model, result.models)
          : current.model;
        return model === current.model ? current : sanitizeDraftSettings({ ...current, model });
      });
      if (result.source === 'remote') {
        // Which credential the vendor was asked with is worth saying: a list that
        // arrived on the environment's key looks identical to one that arrived on
        // a key just typed, and the difference decides what happens when the
        // variable is rotated.
        setNotice(result.keySource === 'environment'
          ? t('discoveredWithEnv', { variable: envKey?.variable ?? '' })
          : t('discovered'));
        return;
      }
      setNotice(result.models.length ? t('offlineFallback') : null);
      // A fallback list with no reason is the state that reads as "nothing
      // happened": a key that was refused, a provider that is down, an endpoint
      // that will not answer and a service that is not there all produce the same
      // list, and their fixes are nothing alike. So the reason is said in the error
      // line, and the label beside the list still says plainly that it is a guess.
      const problem = discoveryProblem(result, providerFor(candidate.provider).label);
      if (problem) setError(t(problem.key, problem.values));
    } catch {
      // A rejected discovery used to leave the button spinning and say nothing at
      // all, because the caller had no way to catch it. Whatever went wrong, the
      // reader is told it was a failure and not a silence.
      if (serial === requestSerial.current) {
        setDiscovering(false);
        setError(t('discoverUnreachable'));
      }
    } finally {
      if (serial === requestSerial.current) setDiscovering(false);
    }
  }, [discover, envKey?.variable, t]);

  /**
   * The key field was left, so the models go and get themselves.
   *
   * A key is a secret, which means there is nothing to watch while it is being
   * typed and nothing in a half of one worth spending. Leaving the field is the
   * end of the edit, and it is where a person who has just pasted a key and
   * tabbed on expects to find the list that key can reach — so the list arrives
   * here rather than on a second trip to a button further down the page. The
   * button is not replaced, because asking is still a thing a person may want to
   * do; it is just no longer the only way the list ever appears.
   *
   * Two things it must not do, and both are quiet when they go wrong. It must not
   * ask about a credential that was already asked about — tabbing past the field,
   * or opening the page and pressing into it out of curiosity, would otherwise
   * spend a request per visit and could put a fresh failure where a good list
   * already was. And it must not ask with nothing to ask with: `shouldProbeCatalog`
   * is what refuses that, so a fresh install cannot collect an error about
   * credentials on a form nobody has typed anything into.
   */
  const discoverOnKeyBlur = () => {
    if (probedWith.current === probeSignature(draft, envActive)) return;
    if (!shouldProbeCatalog(draft, { useEnv: envActive, keyOnFile: savedKey.present })) return;
    void runDiscovery(draft, envActive);
  };

  /** The chosen model, changed without touching anything else in the draft. */
  const setModel = (model: string) => {
    patchBase({ model });
    setModelQuery('');
  };

  const chooseProvider = (provider: ProviderId) => {
    // A provider is a change of identity: its endpoint, model and key belong to
    // it, so nothing is carried across from the one being left behind.
    const definition = PROVIDER_DEFINITIONS[provider];
    setModels([definition.defaultModel]);
    setSource('offline');
    setModelQuery('');
    setEnvProvider(null);
    patchBase({ provider, ...credentialsForProvider(provider) });
  };

  const filteredModels = useMemo(() => filterModels(models, modelQuery).slice(0, 200), [models, modelQuery]);

  /**
   * The one reason this draft cannot be saved, or `null`.
   *
   * Asked of `agentDraftProblem` rather than assembled here, because the answer
   * is a policy — a base model is optional until there is one — and a policy
   * written inside a component is a policy the terminal cannot share. A form that
   * disables its own save button with no explanation is the worst of both: the
   * reader is stuck and told nothing, so the reason is rendered next to the
   * button instead of only appearing after a press that can no longer happen.
   *
   * A key in the environment counts as a key on file, for the same reason a
   * stored one does: the field is empty on purpose, and refusing to save an empty
   * field that is empty on purpose is refusing the only thing that machine can do.
   */
  const problem = useMemo(() => (
    agentMode
      ? agentDraftProblem(
        draft,
        base && { ...base, keyPresent: base.keyPresent || envActive },
        baseTouched,
      )
      : setupProblem(4, draft, envActive)
  ), [agentMode, base, baseTouched, draft, envActive]);
  const canSave = problem === null;

  const writeBaseModel = useCallback(async () => {
    if (!agentMode || !saveBaseModel) return null;
    if (!dirty) return null;
    // The agent could not be asked, so there is nowhere to write. This one is not
    // a failure the agent reported: it is the form saying it never got an answer
    // to begin with, and the error line says so in those words rather than
    // blaming the save it never made. "Saved" over a write that never happened is
    // the one thing this block must not say.
    if (base === null) throw new AgentRequestError('agent_offline', 'not_configured');
    const written = await saveBaseModel({
      provider: draft.provider,
      model: draft.model,
      baseUrl: draft.baseUrl,
      ...(draft.apiKey.trim() ? { apiKey: draft.apiKey.trim() } : {}),
      // Only when the field is empty: a key that was typed is the one to use, and
      // a draft carrying both is a person who changed their mind twice.
      ...(envActive && !draft.apiKey.trim() ? { useEnv: true } : {}),
    });
    setBase(written);
    setBaseTouched(false);
    return written;
  }, [agentMode, base, draft, envActive, saveBaseModel]);

  /**
   * Writes the name, or takes it back, and reports which in one line.
   *
   * Keyed to the value rather than to the save button, which is what keeps a person
   * who came here to change the theme from also being told they renamed their agent.
   * An unchanged name writes nothing and says nothing, which is the same answer as the
   * one below it gives for a name nobody has chosen: there is nothing to report.
   *
   * An emptied field means "no name" rather than "a name that is empty", because a
   * name is the one thing on this screen with a natural absence: there is a state
   * before anybody has named the agent, and removing the file is how that state is
   * written.
   *
   * `identity === null` means the agent could not be asked, so there is nowhere to
   * write; the field is disabled in that state for the same reason, and this returns
   * without claiming anything rather than throwing about a save it never made.
   */
  const writeAgentName = async (): Promise<string | null> => {
    if (!agentMode || !saveIdentity || identity === null) return null;
    const wanted = name.trim();
    if (wanted === identity.selfName) return null;
    if (wanted) {
      const written = await saveIdentity(wanted);
      setIdentity(written);
      return `${t('agentNameSaved', { name: wanted })} ${t('agentNameRestart')}`;
    }
    const cleared = await clearIdentity?.();
    if (!cleared) return null;
    setIdentity(cleared);
    return `${t('agentNameCleared')} ${t('agentNameRestart')}`;
  };

  /**
   * What one refused write reads as.
   *
   * Which sentence comes first depends on which half failed, and the two are not
   * interchangeable: a form that could not be *read* has nowhere to write and is fixed
   * by starting the service, while a write that was refused or went unanswered is
   * fixed by whatever the service said or by finding out why it is silent. Reporting
   * the read failure for a write that was attempted is advice about the wrong thing,
   * and it is what made a save that had genuinely been refused read as a machine that
   * was not running.
   */
  const failureReason = (failure: unknown, kind: 'base' | 'name'): string => {
    const unreadable = failure instanceof AgentRequestError && failure.detail === 'not_configured';
    if (unreadable) return kind === 'name' ? t('agentNameUnreachable') : t('agentBaseModelUnreachable');
    return t(kind === 'name' ? identityFailureKey(failure) : baseModelFailureKey(failure));
  };

  /**
   * Takes the saved key back, leaving the model it belonged to exactly as it was.
   *
   * Its own write rather than an edit to the draft, because a key nobody can read
   * back cannot be removed by editing the field: an emptied field means *keep what
   * is there*, which is what makes a Save over it a no-op. A separate verb is the
   * only thing that can say "not this one" — the same reason the door cards have a
   * Remove button beside each of their credential fields.
   *
   * The provider, model and endpoint come from `base` and not from the draft,
   * because a person removes a key from a form that may be half-finished: a draft
   * with a model name typed into it is refused by the agent, and refusing to forget
   * a key because the *model* is unfinished would be a rule about the wrong field.
   *
   * `useEnv` rides along when the draft has chosen the variable, so the file ends
   * up naming what the form says will be spent. Without it the write would leave
   * both halves empty — which the agent reads as no credential at all — while the
   * form still said "using OPENAI_API_KEY", and the two would be describing
   * different machines.
   *
   * A refusal is reported, not swallowed. The one that will actually happen is
   * `key_required`: a provider that needs a key cannot be left without one, so the
   * agent says so and the field keeps its key. That is the honest answer — the
   * alternative is a button that appears to work and leaves the agent unable to
   * reach the model it is configured with.
   */
  const forgetBaseModelKey = async (): Promise<void> => {
    if (!agentMode || !saveBaseModel || base === null || !base.keyPresent) return;
    setBaseKeyForgetting(true);
    setError(null);
    setNotice(null);
    try {
      const written = await saveBaseModel({
        provider: base.provider,
        model: base.model,
        baseUrl: base.baseUrl,
        clearKey: true,
        ...(envActive ? { useEnv: true } : {}),
      });
      setBase(written);
      // The field goes empty with it. A form still holding the text of a key that
      // was just removed is a form asserting two things at once about which
      // credential the next save writes.
      if (draft.apiKey) patchBase({ apiKey: '' });
      setNotice(t('apiKeyRemoved'));
    } catch (failure) {
      setError(failureReason(failure, 'base'));
    } finally {
      setBaseKeyForgetting(false);
    }
  };

  /**
   * Writes one door, and says what it takes for the change to take effect.
   *
   * The notice is not decoration. A running agent re-reads this file every couple of
   * seconds and reconciles the doors it is actually holding, so a token pasted into
   * a screen is a token the bot is polling within a few seconds. What the notice has
   * to carry is therefore the opposite of what it used to: that nothing else is
   * required of the reader, because the one thing a save used to demand — a restart
   * of an agent that was in the middle of something else — is the inconvenience this
   * path was changed to remove.
   */
  const writeDoor = async (write: AgentDoorWrite): Promise<void> => {
    if (!agentMode || !saveDoor || doors === null) return;
    setDoorSaving(write.id);
    setError(null);
    setNotice(null);
    try {
      const written = await saveDoor(write);
      setDoors(written);
      setNotice(t('doorSaved'));
    } catch (failure) {
      const unreadable = failure instanceof AgentRequestError && failure.detail === 'not_configured';
      // The prefix goes with the key: an address refusal that says only "that is
      // not an address" leaves the reader to work out what an address looks like
      // on the door they are holding.
      const prefix = doors.doors.find((door) => door.id === write.id)?.prefix ?? '';
      setError(unreadable ? t('doorUnreachable') : t(channelFailureKey(failure), { prefix }));
    } finally {
      setDoorSaving(null);
    }
  };

  /**
   * Turns preview mode on or off, and says which way it went.
   *
   * Keyed to the box rather than to the save button, and it stays that way even
   * though everything else here is saved together. This is the one setting whose
   * whole point is what the agent is doing *now*: held behind a Save button, a
   * tick reads as "the agent will stop touching things" while it goes on writing
   * them until somebody remembers to press it, which is the opposite of what a
   * box labelled "preview mode" is asking for.
   *
   * It does not pretend to have worked. `preview === null` is an agent that could
   * not be asked, so there is nowhere to write and the box is disabled; a refusal
   * from the agent leaves the line saying the change did not happen rather than
   * leaving the reader to infer it from a box that sprang back.
   */
  const writePreview = async (next: boolean): Promise<void> => {
    if (!agentMode || !setPreview || preview === null) return;
    setPreviewSaving(true);
    setError(null);
    setNotice(null);
    try {
      await setPreview(next);
      setNotice(t(next ? 'previewModeSavedOn' : 'previewModeSavedOff'));
    } catch {
      setError(t('previewModeFailed'));
    } finally {
      setPreviewSaving(false);
    }
  };

  const save = async () => {
    if (problem !== null) {
      setError(t(problem));
      return;
    }
    // The two modes write to two different places, and both are wanted: the shared
    // settings file is what the terminal reads and what direct mode uses, while
    // the base model and the name belong to the agent's own configuration and
    // nowhere else.
    onUpdate(commitDraftSettings(draft));
    setDirty(false);
    if (!agentMode || (!saveBaseModel && !saveIdentity)) {
      setNotice(t('cliSavedShared'));
      return;
    }
    setBaseSaving(true);
    setNotice(null);

    // The agent's two halves, reported as one line, because the screen has room for
    // one and a summary that named only the half that worked would leave the other
    // looking like it had. Each keeps its own refusal — a model with no key and a
    // name that is not a name are not the same problem, and the fixes are not alike —
    // and the first one is the one that leads, because a save that failed at all
    // failed at the thing that stops the agent answering.
    //
    // Said plainly, because the settings file *was* written either way: a reader told
    // only "saved" would go on believing an agent that had never heard of either.
    let said = '';
    let failed: { error: unknown; kind: 'base' | 'name' } | null = null;
    try {
      if (await writeBaseModel()) said = t('agentBaseModelSaved');
    } catch (error) {
      failed = { error, kind: 'base' };
    }
    try {
      const renamed = await writeAgentName();
      if (renamed) said = said ? `${said} ${renamed}` : renamed;
    } catch (error) {
      failed ??= { error, kind: 'name' };
    }

    if (failed) {
      // "Nothing was changed" is only true when no half of the save landed, so it is
      // said only then.
      setError(failed && said
        ? `${failureReason(failed.error, failed.kind)} ${said}`
        : `${failureReason(failed.error, failed.kind)} ${t('agentBaseModelSavingFailed')}`);
    } else {
      setNotice(said || t('cliSavedShared'));
    }
    setBaseSaving(false);
  };

  const close = () => {
    if (canSave && dirty) onUpdate(commitDraftSettings(draft));
    onClose();
  };

  return (
    // The wheel handler belongs on the whole screen rather than on the body
    // below, because the gap it fills is exactly the part the body does not
    // cover: the pinned header, and any margin outside a centred column.
    <div className="flex min-h-0 flex-1 flex-col" onWheel={wheelToBody}>
      <header className="shrink-0 border-b border-[var(--line)] px-4 py-4 sm:px-7 lg:px-10">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--line-strong)] bg-[var(--canvas-raised)] text-[var(--accent)] shadow-emboss"><Icon name="settings" size={19} /></div>
            <div>
              <h1 className="text-sm font-semibold tracking-wide text-[var(--ink)]">{t('settings')}</h1>
              <p className="text-xs text-[var(--ink-faint)]">
                {draft.interface === 'agent' ? t('agentTitle') : t('providerSetup')}
              </p>
            </div>
          </div>
          <Button icon="close" onClick={close} variant="outline">{t('close')}</Button>
        </div>
      </header>

      {/* The reading column, and the only thing on the screen that scrolls. It is
          narrower than the screen on purpose, so the header above stays put
          while a long form scrolls under it — which means it also leaves two
          kinds of ground that do not scroll with it: the header, and the margin
          either side. The wheel handler on the screen above is what covers
          those two; see `useWheelScroll`. */}
      <div className="mx-auto w-full max-w-3xl min-h-0 flex-1 overflow-y-auto px-4 pb-8 pt-5 sm:px-7 lg:px-10" ref={body}>
        <div className="instrument-panel space-y-6 rounded-2xl p-5 sm:p-7">
          <section>
            <SectionLabel icon="link">{t('interfaceLabel')}</SectionLabel>
            <div className="grid gap-2 sm:grid-cols-2">
              {INTERFACES.map((entry) => {
                const selected = draft.interface === entry.id;
                return (
                  <button
                    aria-pressed={selected}
                    className={classNames(
                      'instrument-panel flex items-start gap-3 rounded-xl p-3 text-left',
                      selected ? 'accent-border ring-1 ring-[var(--accent-halo)]' : 'hover:border-[var(--line-strong)]',
                    )}
                    key={entry.id}
                    onClick={() => patch({ interface: entry.id })}
                    type="button"
                  >
                    <Icon className={selected ? 'accent-text mt-0.5 shrink-0' : 'mt-0.5 shrink-0 text-[var(--ink-muted)]'} name={entry.icon} size={16} />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-[var(--ink)]">{t(entry.label)}</span>
                      <span className="mt-0.5 block text-[0.66rem] leading-4 text-[var(--ink-faint)]">{t(entry.hint)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          {agentMode ? (
            <section className="space-y-4">
              {/* First on the screen, above even the agent's name, and that is the whole
                  argument for putting it there rather than with the other agent-owned
                  settings further down. Everything else on this page describes the
                  agent — what it is called, which model it thinks with, which doors
                  it has — and none of it changes what the agent is allowed to do
                  while you are reading it. This one does, it takes effect the moment
                  it is pressed rather than on Save, and it is the thing to reach for
                  when you have just started an agent on a machine and want to watch
                  it before letting it touch anything. A control like that belongs
                  where it is seen without scrolling, beside the choice of who you
                  are even talking to.

                  Inside the agent's block, so in `direct` mode it is not offered at
                  all: this page then talks to a model provider and holds no tools,
                  and a box promising to switch them off would be a control over
                  nothing — the one setting here that could not be made true, which
                  is why it is left out rather than shown and quietly inert. */}
              {setPreview ? (
                <PreviewModeCard
                  language={draft.language}
                  onToggle={(next) => void writePreview(next)}
                  preview={preview}
                  saving={previewSaving}
                />
              ) : null}
              {/* What it is called, where it is, and who you are to it. */}
              <div>
                <FieldLabel>{t('agentName')}</FieldLabel>
                <div className="relative">
                  <Icon className="pointer-events-none absolute left-3 top-3 text-[var(--ink-faint)]" name="spark" size={17} />
                  <Input
                    className="pl-10"
                    // Disabled rather than merely ignored when the agent cannot be
                    // asked: a field that accepts an edit this form has nowhere to
                    // save is a promise the save button cannot keep.
                    disabled={!saveIdentity || identity === null}
                    maxLength={MAX_AGENT_NAME_LENGTH}
                    onChange={(event) => patchName(event.target.value)}
                    placeholder={t('agentNamePlaceholder')}
                    value={name}
                  />
                </div>
                <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('agentNameHint')}</p>
              </div>
              <div>
                <FieldLabel hint="loopback">{t('agentEndpoint')}</FieldLabel>
                <div className="relative">
                  <Icon className="pointer-events-none absolute left-3 top-3 text-[var(--ink-faint)]" name="server" size={17} />
                  <Input
                    className="pl-10 font-mono"
                    maxLength={MAX_ENDPOINT_LENGTH}
                    onChange={(event) => patch({ agentUrl: event.target.value })}
                    placeholder={createSettings().agentUrl}
                    spellCheck={false}
                    value={draft.agentUrl}
                  />
                </div>
                <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('agentEndpointHint')}</p>
              </div>
              <div>
                <FieldLabel>{t('agentPerson')}</FieldLabel>
                <div className="relative">
                  <Icon className="pointer-events-none absolute left-3 top-3 text-[var(--ink-faint)]" name="home" size={17} />
                  <Input
                    className="pl-10"
                    maxLength={MAX_PERSON_LENGTH}
                    onChange={(event) => patch({ agentPerson: event.target.value })}
                    placeholder="owner"
                    value={draft.agentPerson}
                  />
                </div>
                <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">{t('agentPersonHint')}</p>
              </div>
            </section>
          ) : null}

          {/* The provider, key, endpoint and model block is the same component
              tree in both modes, and it is the only one. A form that showed a
              different set of controls depending on which interface was in use
              was a form whose controls could not be compared, and the differences
              were all mine: a status line, a "forget key" button, a note about
              which agent was behind it. None of them were asked for, and a
              section that looks different is a section nobody trusts. */}
          <CredentialSection
            draft={draft}
            envActive={envActive}
            envKey={envKey}
            fileEnv={fileEnv}
            // Offered only where this page owns the write. In direct mode the
            // shared file is cleared by `phone config unset apiKey` and by
            // nothing else — see `savedApiKey`.
            onForgetKey={savedKey.removable ? () => void forgetBaseModelKey() : null}
            forgettingKey={baseKeyForgetting}
            onApiKey={patchApiKey}
            onCommitApiKey={discoverOnKeyBlur}
            onChooseProvider={chooseProvider}
            onToggleEnvKey={toggleEnvKey}
            patch={patchBase}
            savedKey={savedKey}
          />

          <ModelSection
            discovering={discovering}
            draft={draft}
            filtered={filteredModels}
            modelQuery={modelQuery}
            onChoose={setModel}
            onDiscover={() => void runDiscovery(draft, envActive)}
            onQuery={setModelQuery}
            source={source}
          />

          {/*
            The doors, and the only section on this page that writes a credential
            the agent reads with a bot token rather than with an API key.

            In the agent's own block because that is where they belong: a door is
            the deployment's, and a credential for it does not belong in a
            settings file the browser and the terminal share. Nothing here is held
            after the save either — the page is told which credentials exist, never
            what any of them says, which is the only way a form can offer a field
            for a secret it does not keep.
          */}
          {agentMode ? (
            <section className="space-y-4 border-t border-[var(--line)] pt-5">
              <div>
                <SectionLabel icon="link">{t('doorSection')}</SectionLabel>
                <p className="mt-2 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
                  {doors === null && readDoors ? t('doorUnreachable') : t('doorSectionHint')}
                </p>
                {doors?.file ? (
                  <p className="mt-1 text-[0.68rem] leading-5 text-[var(--ink-faint)]">
                    {t('doorSavedIn', { file: doors.file })}
                  </p>
                ) : null}
              </div>
              {doors?.doors.map((door) => (
                <DoorCard
                  door={door}
                  key={door.id}
                  language={draft.language}
                  onSave={writeDoor}
                  saving={doorSaving === door.id}
                />
              ))}
            </section>
          ) : null}

          <section className="space-y-4 border-t border-[var(--line)] pt-5">
            <SectionLabel icon="palette">{t('appearance')}</SectionLabel>
            <div>
              <FieldLabel>{t('language')}</FieldLabel>
              <div className="grid grid-cols-3 gap-2">
                {(['en', 'ja', 'zh'] as const).map((language) => (
                  <button
                    aria-pressed={draft.language === language}
                    className={classNames(
                      'rounded-lg border px-2 py-2 text-xs font-medium transition',
                      draft.language === language
                        ? 'accent-border bg-[var(--accent-soft)] accent-text'
                        : 'border-[var(--line)] text-[var(--ink-muted)] hover:border-[var(--line-strong)]',
                    )}
                    key={language}
                    onClick={() => patch({ language })}
                    type="button"
                  >
                    {languageLabel(language)}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <FieldLabel>{t('theme')}</FieldLabel>
              <div className="grid grid-cols-2 gap-2">
                <button aria-pressed={draft.theme === 'dark'} className={classNames('flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium', draft.theme === 'dark' ? 'accent-border bg-[var(--accent-soft)] accent-text' : 'border-[var(--line)] text-[var(--ink-muted)]')} onClick={() => patch({ theme: 'dark' })} type="button"><Icon name="moon" size={15} />{t('dark')}</button>
                <button aria-pressed={draft.theme === 'light'} className={classNames('flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium', draft.theme === 'light' ? 'accent-border bg-[var(--accent-soft)] accent-text' : 'border-[var(--line)] text-[var(--ink-muted)]')} onClick={() => patch({ theme: 'light' })} type="button"><Icon name="sun" size={15} />{t('light')}</button>
              </div>
            </div>
            <div>
              <FieldLabel hint={draft.accent}>{t('accent')}</FieldLabel>
              <div className="flex items-center gap-3">
                <input
                  aria-label={t('accent')}
                  className="h-10 w-14 cursor-pointer rounded-lg border border-[var(--line)] bg-[var(--canvas-well)]"
                  onChange={(event) => patch({ accent: event.target.value.toUpperCase() })}
                  type="color"
                  value={isValidAccent(draft.accent) ? draft.accent : '#F97316'}
                />
                <Input className="font-mono uppercase" maxLength={7} onChange={(event) => patch({ accent: event.target.value })} value={draft.accent} />
              </div>
            </div>
          </section>

          {error ? (
            <div className="flex items-start gap-2 rounded-lg border border-[var(--danger-line)] bg-[var(--danger-soft)] px-3 py-2.5 text-xs text-[var(--danger)]">
              <Icon className="mt-0.5 shrink-0" name="close" size={14} />{error}
            </div>
          ) : null}
          {notice && !error ? <p className="text-xs text-[var(--ok)]">{notice}</p> : null}

          <div className="flex items-center justify-between gap-3 border-t border-[var(--line)] pt-5">
            <span className="flex items-center gap-2 text-[0.68rem] text-[var(--ink-faint)]">
              <Icon name="shield" size={13} />
              {t('localOnlyCli')}
            </span>
            <Button
              disabled={!canSave || baseSaving}
              icon="check"
              onClick={() => void save()}
              variant="primary"
            >
              {baseSaving ? t('agentBaseModelSaving') : t('save')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function sameSettings(left: Settings, right: Settings): boolean {
  return left.interface === right.interface
    && left.agentUrl === right.agentUrl
    && left.agentPerson === right.agentPerson
    && left.provider === right.provider
    && left.apiKey === right.apiKey
    && left.baseUrl === right.baseUrl
    && left.model === right.model
    && left.language === right.language
    && left.theme === right.theme
    && left.accent === right.accent;
}
