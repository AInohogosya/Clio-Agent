import { createTranslator, type TranslationKey } from '../i18n.js';
import type { Language } from '../types.js';
import { AgentRequestError, isBaseModelRefusal, isChannelRefusal, isIdentityRefusal, type BaseModelRefusal, type ChannelRefusal, type IdentityRefusal } from './types.js';

/**
 * Why the agent is not answering, in words.
 *
 * Each code is a *different* situation with a *different next step*, so they do
 * not share a sentence: a link that is not running, an interface service that is
 * not answering, a machine with no agent on it, an agent that has been stopped,
 * an agent that chose not to reply, one that never managed to answer, and one
 * that went quiet mid-thought all look identical under "something went wrong" —
 * and the actions they call for are opposites. A stopped agent needs `resume`; a
 * machine with nothing running on it needs the agent started; a declined
 * question needs nothing; an unreachable service needs the agent started.
 */

/** The translation each refusal reads as. */
const REASONS: Record<string, Parameters<ReturnType<typeof createTranslator>>[0]> = {
  agent_offline: 'agentOffline',
  agent_unreachable: 'agentUnreachable',
  agent_invalid_endpoint: 'agentBadEndpoint',
  agent_not_running: 'agentNotRunning',
  agent_stopped: 'agentStopped',
  agent_declined: 'agentDeclined',
  agent_unanswered: 'agentUnanswered',
  agent_timeout: 'agentTimeout',
  agent_aborted: 'agentInterrupted',
  agent_refused: 'agentRefused',
};


/**
 * Describes an agent failure, or returns `null` for anything that is not one, so
 * a caller can fall through to whatever else it knows how to say.
 */
export function describeAgentFailure(error: unknown, language: Language): string | null {
  if (!(error instanceof AgentRequestError)) return null;
  const t = createTranslator(language);
  const key = REASONS[error.code];
  const reason = key ? t(key) : t('agentRefused');
  if (!error.detail || error.detail === reason) return reason;
  return `${reason} · ${error.detail}`;
}

/**
 * The agent's refusals, each in the words a surface should use.
 */
export const BASE_MODEL_REFUSAL_KEYS: Record<BaseModelRefusal, TranslationKey> = {
  unknown_provider: 'baseModelUnknownProvider',
  invalid_model: 'baseModelInvalidModel',
  invalid_endpoint: 'baseModelInvalidEndpoint',
  key_required: 'baseModelKeyRequired',
  env_key_missing: 'baseModelEnvKeyMissing',
  home_unwritable: 'baseModelHomeUnwritable',
  cross_origin_refused: 'baseModelCrossOrigin',
};

/** The same two shared failures, and the one thing about a name that can be wrong. */
const IDENTITY_REFUSAL_KEYS: Record<IdentityRefusal, TranslationKey> = {
  invalid_name: 'agentNameInvalid',
  home_unwritable: 'baseModelHomeUnwritable',
  cross_origin_refused: 'baseModelCrossOrigin',
};

/**
 * Service answers that are not refusals, each with its own next step.
 *
 * A missing route is not an outage. It means the address answered, and what it
 * answered was that this is not the agent's interface service — an older build,
 * or a different program on the port. "The service is not answering" sends
 * somebody to start something that is already running, and a raw `http_404` is
 * not something a person can act on at all.
 */
const WRITE_OUTCOMES: Record<string, TranslationKey> = {
  http_404: 'agentBaseModelNoRoute',
  http_405: 'agentBaseModelNoRoute',
  http_501: 'agentBaseModelNoRoute',
};

/**
 * What a failed base-model write reads as, as a key for the caller's language.
 *
 * The agent puts its refusal in `detail`, not in `message` — `message` reads
 * `agent_refused: invalid_endpoint`, which names the fact that it failed and none
 * of the reason. Reading the wrong one is silent rather than loud: every refusal
 * would be reported as "could not reach the agent", which is advice to start
 * something that is already running. So this is one function, used by every
 * surface, because three copies of "where is the code" is three chances to read
 * the wrong field.
 *
 * The fallback is about the *write*, on purpose. A service that did not answer
 * this one call is a different thing from a service that could not be read at
 * all, and reporting one as the other is how "the agent could not be reached"
 * ended up describing a save that had in fact been attempted and refused. The
 * caller says which half failed; this says what the write was told.
 */
export function baseModelFailureKey(error: unknown): TranslationKey {
  const detail = error instanceof AgentRequestError
    ? error.detail
    : (typeof error === 'string' ? error : '');
  if (isBaseModelRefusal(detail)) return BASE_MODEL_REFUSAL_KEYS[detail];
  if (Object.prototype.hasOwnProperty.call(WRITE_OUTCOMES, detail)) return WRITE_OUTCOMES[detail];
  return 'agentBaseModelWriteUnanswered';
}

/**
 * What a refused name reads as, as a key for the caller's language.
 *
 * Two of the three codes are the base model's, and they point at the same keys on
 * purpose: a home that cannot be written and a request from another site are the same
 * two failures whichever route hit them, and giving the name its own wording for them
 * would mean two vocabularies for one problem.
 *
 * It reads `detail` for the reason {@link baseModelFailureKey} does, and for the same
 * reason — a failure here says `agent_refused: invalid_name`, which names the fact
 * that it failed and none of the reason.
 */
export function identityFailureKey(error: unknown): TranslationKey {
  const detail = error instanceof AgentRequestError
    ? error.detail
    : (typeof error === 'string' ? error : '');
  if (isIdentityRefusal(detail)) return IDENTITY_REFUSAL_KEYS[detail];
  if (Object.prototype.hasOwnProperty.call(WRITE_OUTCOMES, detail)) return WRITE_OUTCOMES[detail];
  return 'agentBaseModelWriteUnanswered';
}

/**
 * The translation each door refusal reads as.
 *
 * Two of the seven are the shared failures and they point at the base model's keys
 * on purpose: a home that cannot be written and a request from another site are the
 * same two problems whichever route hit them, and two vocabularies for one problem
 * is two chances to teach somebody the wrong one.
 */
const CHANNEL_REFUSAL_KEYS: Record<ChannelRefusal, TranslationKey> = {
  unknown_channel: 'doorUnknownChannel',
  unknown_field: 'doorUnknownField',
  invalid_value: 'doorInvalidValue',
  invalid_allowed_id: 'doorInvalidAllowedId',
  invalid_address: 'doorInvalidAddress',
  home_unwritable: 'baseModelHomeUnwritable',
  cross_origin_refused: 'baseModelCrossOrigin',
};

/**
 * What a refused door write reads as, as a key for the caller's language.
 *
 * The same one function `baseModelFailureKey` is, for the same reason: the agent
 * puts its refusal in `detail`, so reading the wrong field would report every
 * refusal as "could not reach the agent" — advice to start something that is
 * already running, sent to somebody whose only problem is a chat id.
 */
export function channelFailureKey(error: unknown): TranslationKey {
  const detail = error instanceof AgentRequestError
    ? error.detail
    : (typeof error === 'string' ? error : '');
  if (isChannelRefusal(detail)) return CHANNEL_REFUSAL_KEYS[detail];
  if (Object.prototype.hasOwnProperty.call(WRITE_OUTCOMES, detail)) return WRITE_OUTCOMES[detail];
  return 'agentBaseModelWriteUnanswered';
}
