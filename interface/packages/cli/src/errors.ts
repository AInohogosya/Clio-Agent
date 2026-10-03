import { createTranslator, type Language, type TranslationKey } from '@project-phone/core';
import type { ErrorCode } from './args.js';

/**
 * Why a failure is not in the table.
 *
 * A code with no sentence is not silently replaced with a shrug: the raw text
 * is printed instead. That is deliberate — a misconfiguration is diagnosable
 * from the terminal, and a table of sentences that swallowed `ENOENT` and
 * "Incorrect API key provided" is how both became invisible.
 */
const CODE_TABLE: Record<ErrorCode, TranslationKey> = {
  sensitive_argument: 'cliSecretArgument',
  door_secret_argument: 'cliDoorSecretArgument',
  // The same key the bridge's own `invalid_address` refusal reads as, on purpose:
  // one problem, one sentence, whichever surface caught it first.
  door_invalid_address: 'doorInvalidAddress',
  unknown_command: 'cliUnknownCommand',
  invalid_value: 'cliInvalidValue',
  unknown_field: 'cliUnknownField',
  not_resettable: 'cliNotResettable',
  input_timeout: 'cliInputTimeout',
  input_too_large: 'cliInputTooLarge',
  input_failed: 'cliInputFailed',
  missing_message: 'cliMissingMessage',
  missing_key: 'cliMissingKey',
  invalid_provider: 'cliInvalidProvider',
  config_cleanup_failed: 'cliConfigCleanup',
  legacy_key_cleanup_failed: 'cliConfigCleanup',
  config_too_large: 'cliConfigTooLarge',
  unsafe_config_target: 'cliUnsafeConfigTarget',
  insecure_config_permissions: 'cliConfigPermissions',
  config_not_owned_by_user: 'cliConfigOwner',
  not_configured: 'cliNotConfigured',
  agent_target_required: 'cliAgentTargetRequired',
  agent_target_unexpected: 'cliAgentTargetUnexpected',
  channel_required: 'cliChannelRequired',
  channel_unknown: 'cliChannelUnknown',
  channel_closed: 'cliChannelClosed',
  channel_unaddressed: 'cliChannelUnaddressed',
  aborted: 'cliInterrupted',
};

export function isKnownErrorCode(code: string): code is ErrorCode {
  return Object.prototype.hasOwnProperty.call(CODE_TABLE, code);
}

export function translationKeyForCode(code: string): TranslationKey {
  return isKnownErrorCode(code) ? CODE_TABLE[code] : 'cliUnexpected';
}

export function describeError(
  code: string,
  language: Language,
  values: Record<string, string | number> = {},
): string {
  return createTranslator(language)(translationKeyForCode(code), values);
}
