import {
  MAX_API_KEY_LENGTH,
  MAX_ENDPOINT_LENGTH,
  isProviderModelName,
  isSafeCredential,
  isSafeIdentifier,
  isSafeModelName,
  isValidAgentEndpoint,
  isValidProviderEndpoint,
  sanitizeTerminalText,
} from './security.js';
import type { TranslationKey } from './i18n.js';
import { providerFor } from './providers.js';
import { MAX_PERSON_LENGTH, type ModelDiscoveryResult, type ProviderId, type Settings } from './types.js';
import { createSettings } from './types.js';

/**
 * The rules the provider setup shares between the terminal wizard and the
 * browser setup screen. Both walk the same four steps, so the decisions that
 * matter — which fields may be edited freely, what a provider change resets,
 * what has to be true before the next step — live here rather than being written
 * out twice and drifting apart.
 *
 * These are pure functions on purpose: each interface keeps its own state and
 * its own rendering, and neither can end up with different rules.
 */

export type SetupStep = 1 | 2 | 3 | 4;

export const SETUP_STEPS: readonly SetupStep[] = [1, 2, 3, 4];

export const SETUP_STEP_LABELS: Record<SetupStep, TranslationKey> = {
  1: 'provider',
  2: 'credentials',
  3: 'discovery',
  4: 'model',
};

/**
 * Keeps a draft editable.
 *
 * A credential, an endpoint and a model name are each re-checked as they are
 * typed, so a draft can never hold something that would be refused on save. The
 * other fields are left exactly as they are: this is an editor, not a validator.
 */
export function sanitizeDraftSettings(draft: Settings): Settings {
  return {
    ...draft,
    apiKey: isSafeCredential(draft.apiKey) ? draft.apiKey.slice(0, MAX_API_KEY_LENGTH) : '',
    baseUrl: sanitizeTerminalText(draft.baseUrl).slice(0, MAX_ENDPOINT_LENGTH),
    agentUrl: sanitizeTerminalText(draft.agentUrl).slice(0, MAX_ENDPOINT_LENGTH),
    agentPerson: sanitizeTerminalText(draft.agentPerson).slice(0, MAX_PERSON_LENGTH),
    model: isSafeModelName(draft.model) ? draft.model : '',
  };
}

/**
 * The credential tuple a provider starts from.
 *
 * The key is deliberately empty, and so is the model unless the provider is
 * OpenAI: an endpoint, a model and a key all belong to the provider they were
 * chosen for, so carrying one across would post a provider's key to a different
 * vendor's endpoint — and a model a person never chose is a default presented
 * as a choice. Choosing a provider clears the model; discovery or the picker
 * fills it back in.
 */
export function credentialsForProvider(provider: ProviderId): Pick<Settings, 'apiKey' | 'baseUrl' | 'model'> {
  const definition = providerFor(provider);
  return { apiKey: '', baseUrl: definition.defaultBaseUrl, model: definition.defaultModel ?? '' };
}

/** What, if anything, must be fixed before a direct-provider draft can be saved. */
export function setupProblem(step: SetupStep, draft: Settings, keyOnFile = false): TranslationKey | null {
  if (step <= 1) return null;
  if (!isValidProviderEndpoint(draft.baseUrl, draft.provider)) return 'invalidEndpoint';
  // `keyOnFile` is a fact about the credential rather than about the draft, for
  // the same reason `baseModelProblem` takes it: a key the form never held — on
  // file, or in the environment — makes an empty field a deliberate choice rather
  // than a missing one, and refusing to save on that basis would make the form
  // unusable for the machine that has no key to type.
  if (providerFor(draft.provider).requiresApiKey && !draft.apiKey.trim() && !keyOnFile) return 'keyRequired';
  if (step >= 3 && !draft.model.trim()) return 'modelRequired';
  return null;
}

/**
 * What must be fixed before an *agent* draft can be saved.
 *
 * A separate question from `setupProblem`, and the answer is smaller: the address
 * and the name of the person. Validating the agent with the provider's own rules
 * would demand an API key the agent interface does not have.
 */
export function agentProblem(draft: Settings): TranslationKey | null {
  if (!isValidAgentEndpoint(draft.agentUrl)) return 'invalidEndpoint';
  if (!isSafeIdentifier(draft.agentPerson, MAX_PERSON_LENGTH) || !draft.agentPerson.trim()) {
    return 'agentPersonRequired';
  }
  return null;
}

/** What the agent reports about its own base model, as far as a rule needs. */
export interface BaseModelState {
  configured: boolean;
  keyPresent: boolean;
}

/**
 * What must be fixed before an agent draft can be saved, base model included.
 *
 * The one thing this gets right that the parts do not: **a base model is
 * optional.** The agent can be perfectly able to think on its own catalogue with
 * keys in the environment, and an install that has never been asked about a base
 * model has no reason to be made to supply one — least of all to change its
 * language, which is three fields further down the same page. So the base model
 * is only held to a rule when there is one to hold: the agent already has one, or
 * the person has started filling one in.
 *
 * `base === null` is "the agent could not be asked", not "the agent has no base
 * model", and it holds nothing up. There is nothing to validate against, and
 * blocking a save because a service is down would make the whole page unusable
 * for everything else on it — which is what happens if the base model is treated
 * as required by default.
 */
export function agentDraftProblem(
  draft: Settings,
  base: BaseModelState | null,
  touched = false,
): TranslationKey | null {
  const address = agentProblem(draft);
  if (address) return address;
  if (base === null) return null;
  if (!base.configured && !touched) return null;
  return baseModelProblem(draft, base.keyPresent);
}

/**
 * What must be fixed before a base model can be handed to the agent.
 *
 * The agent checks all of this again and refuses the same things, so a surface
 * that only checked would be one reporting success for something the agent then
 * ignored. Checking here as well is what turns a round trip into an underline.
 *
 * `keyOnFile` is a fact about the agent rather than about the draft: a stored key
 * means an empty field keeps it, so an empty key is not a missing one. Getting
 * that backwards would make the form refuse to save the model a person is trying
 * to change *because* they never saw the key they had already given it.
 */
export function baseModelProblem(draft: Settings, keyOnFile = false): TranslationKey | null {
  if (!isProviderModelName(draft.model)) return 'baseModelInvalidModel';
  if (!isValidProviderEndpoint(draft.baseUrl, draft.provider)) return 'baseModelInvalidEndpoint';
  if (providerFor(draft.provider).requiresApiKey && !draft.apiKey.trim() && !keyOnFile) {
    return 'baseModelKeyRequired';
  }
  return null;
}

/** The draft as it will be saved, normalised once at the end rather than per keystroke. */
export function commitDraftSettings(draft: Settings): Settings {
  return createSettings(sanitizeDraftSettings(draft));
}

/** The key half of a credential status, which is all a rule about it needs. */
export interface SavedKeyState {
  present: boolean;
  hint: string;
}

/** What a form may say about a credential it is not allowed to read. */
export interface SavedApiKey {
  /** Whether a key is saved at all. */
  present: boolean;
  /** The mask for the field, or `''` when nothing is saved. */
  mask: string;
  /** Whether *this* form may offer to take it back. */
  removable: boolean;
}

/** The mask a field shows when a key is saved and its hint could not be read. */
const MASK = '••••••••';

/**
 * The key that is already saved, as a form is allowed to describe it.
 *
 * Never the key. A page is handed a hint and nothing else — not the value, not
 * its length — so what this answers is not "what is the key" but "is there one",
 * which is the question that was going unanswered on a screen whose provider was
 * plainly configured.
 *
 * The reason it matters is what a blank field means. An empty key field reads as
 * *nothing is set*, and after a restart that is false: the field is empty because
 * the key is on the other side of a link this page is not allowed to read back,
 * and leaving it empty keeps it. Those two states are identical on the screen and
 * opposite in fact, and the one that loses is the one that is working — so the
 * field carries the mask and the line under it says where the key is.
 *
 * Two files, because the key belongs to whichever machine is doing the answering:
 * in agent mode the agent's own base-model file, in direct mode the shared
 * settings file the terminal also writes. Neither is the other's, and a form
 * reporting the wrong one would put a hint beside a model with no key while the
 * real one went undescribed.
 *
 * `removable` is deliberately narrower than `present`. The agent's key can be
 * taken back from this page, because the browser owns that write. The shared
 * file's cannot: a redacted snapshot also sends an empty key, so a page that
 * could clear it by omission would clear it by being opened — which is why
 * `phone config unset apiKey` is the deliberate way to remove that one.
 */
export function savedApiKey(
  agentMode: boolean,
  base: SavedKeyState | null,
  credential: SavedKeyState,
): SavedApiKey {
  const hint = agentMode ? base?.hint ?? '' : credential.hint;
  const present = agentMode ? Boolean(base?.present) : credential.present;
  return { present, mask: present ? hint || MASK : '', removable: agentMode && present };
}

/**
 * The model to use after a discovery run: the one already chosen if the
 * provider still offers it, otherwise the first that was found, otherwise
 * whatever was there before — an empty catalogue is not a reason to forget.
 */
export function chooseDiscoveredModel(current: string, models: readonly string[]): string {
  if (models.includes(current)) return current;
  return models[0] ?? current;
}

/**
 * Each reason a catalogue did not come from the vendor, in words of its own.
 *
 * A fallback list and "using the offline catalog" is the same sentence for a
 * key that was refused, a provider that is down, an endpoint that will not answer
 * and a page that asked the wrong process — four situations whose fixes are
 * nothing alike. `http_*` is not in the table because one key covers every
 * status, and the status travels with it as `{status}`.
 */
const DISCOVERY_PROBLEMS: Record<string, TranslationKey> = {
  missing_credentials: 'discoverNoCredentials',
  invalid_endpoint: 'discoverBadEndpoint',
  unknown_provider: 'discoverUnknownProvider',
  empty_catalog: 'discoverEmptyCatalog',
  redirect_refused: 'discoverRedirect',
  timeout: 'discoverTimeout',
  // The provider is the thing that could not be reached, whoever did the asking:
  // both the service that holds the key and the page itself report a failure to
  // reach the vendor under this one code, and in both cases the vendor is what is
  // missing. Only the two below are about this machine rather than about them.
  network_unavailable: 'providerUnavailable',
  bridge_unavailable: 'discoverUnreachable',
  agent_offline: 'discoverAgentOffline',
  agent_unreachable: 'discoverAgentOffline',
};

/**
 * Why a discovery run answered with a fallback, or `null` when it did not.
 *
 * Asked of the *result* rather than of a code, so a surface cannot report a
 * reason the run did not give, and `null` for a real catalogue keeps a caller
 * from inventing a complaint about a request that worked. The values travel with
 * the key because the two sentences that have holes in them are the ones about
 * *which* provider and *which* status, and a caller that had to re-parse the code
 * to fill them in would be the second place those codes are understood.
 */
export function discoveryProblem(
  result: ModelDiscoveryResult,
  provider: string,
): { key: TranslationKey; values: Record<string, string> } | null {
  if (result.source === 'remote') return null;
  const reason = result.error ?? '';
  const status = /^http_(\d{3})$/.exec(reason)?.[1] ?? '';
  if (Object.prototype.hasOwnProperty.call(DISCOVERY_PROBLEMS, reason)) {
    return { key: DISCOVERY_PROBLEMS[reason], values: { provider } };
  }
  if (status) return { key: 'providerHttp', values: { status } };
  // A fallback list with a reason nobody has a sentence for is still a list, and
  // the sentence that says so without pretending to know more is the honest one.
  return result.models.length ? null : { key: 'discoverNoModels', values: { provider } };
}

/**
 * The credential a catalogue request would be made with, as one comparable line.
 *
 * Provider, endpoint, key, and which of the two answers about the key is being
 * spent are the four things that decide which vendor is asked and with what.
 * Everything else about a draft — the model, the interface, the language —
 * changes nothing about the answer, which is what lets a caller ask whether a run
 * is worth making without asking the network to be told what is already on the
 * screen.
 *
 * It is a comparison key, not a record: nothing here is displayed, logged or
 * written down, and the line lives exactly as long as the page that made it.
 */
export function probeSignature(draft: Settings, useEnv: boolean): string {
  return JSON.stringify([draft.provider, draft.baseUrl, draft.apiKey, useEnv]);
}

/**
 * Whether a catalogue may be fetched without being told to, or `false` when a
 * person has to ask for it.
 *
 * Leaving the key field is a finished edit, and it is the only finished one a
 * secret has: there is nothing to watch as a key is typed and no partial key
 * worth acting on, so a person who pastes one and tabs to the next field should
 * find the models that key can reach already waiting. The rule is therefore about
 * the *credential* rather than about the field — a run is worth making when there
 * is something to make it with.
 *
 * Three ways there is, and they are the three answers this form gives about a
 * credential: one typed here, the environment's key spent on purpose, or one
 * already saved on whichever machine is answering. A provider that asks for no
 * key needs none of them — the endpoint is the whole request, and pressing into
 * an empty field is not a mistake.
 *
 * And the fourth case is the reason this is a rule rather than a habit. With
 * nothing to spend on an install that has no key anywhere, the only possible
 * answer is a failure about credentials that nobody caused and nobody asked
 * about, written onto a form nobody has submitted anything on. The button stays
 * for that, and for every retry.
 */
export function shouldProbeCatalog(
  draft: Settings,
  options: { useEnv: boolean; keyOnFile: boolean },
): boolean {
  if (!providerFor(draft.provider).requiresApiKey) return true;
  if (options.useEnv || options.keyOnFile) return true;
  return draft.apiKey.trim() !== '';
}

/** Case-insensitive substring search over a discovered catalogue. */
export function filterModels(models: readonly string[], query: string): string[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...models];
  return models.filter((model) => model.toLowerCase().includes(needle));
}

/**
 * Case-insensitive substring search over the provider list, by label and id.
 *
 * Shared by the surfaces that offer more providers than a screen can show at
 * once: a list of a hundred needs a search box, and the rule the box applies
 * lives here rather than in a component, so the page and any other surface
 * cannot disagree about what a match is.
 */
export function filterProviders(providers: readonly ProviderId[], query: string): ProviderId[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...providers];
  return providers.filter((provider) => (
    providerFor(provider).label.toLowerCase().includes(needle) || provider.includes(needle)
  ));
}
