import {
  createSettings,
  DEFAULT_ACCENT,
  DEFAULT_AGENT_PERSON,
  DEFAULT_SETTINGS,
  INTERFACES,
  isProviderId,
  isValidAgentEndpoint,
  isValidProviderEndpoint,
  LANGUAGES,
  MAX_PERSON_LENGTH,
  PROVIDER_DEFAULTS,
  type ProviderId,
  type Settings,
  type Theme,
} from '@project-phone/core';
import { isConfigField, CliError, type ConfigField } from './args.js';
import { hasEnvironmentApiKey, type PhoneConfig } from './config.js';

/**
 * `phone config` in one place: which fields exist, how a value is written, how a
 * value is cleared, and how a field is printed. Every screen that shows settings
 * reads these tables rather than repeating the mapping, so a field added here
 * shows up in the list, in `get`, in `set` and in `unset` together.
 */

/** The fields whose value is a plain string, for `get` and for the settings list. */
type ValueField = Exclude<ConfigField, 'apiKey'>;

const STRING_FIELDS = new Set<ConfigField>([
  'interface', 'agentUrl', 'agentPerson',
  'provider', 'model', 'baseUrl', 'language', 'theme', 'accent',
]);

export function isValueField(field: ConfigField): field is ValueField {
  return STRING_FIELDS.has(field);
}

/** The fresh-install value of a provider field: an endpoint, and a model only for OpenAI. */
function providerDefault<K extends 'baseUrl' | 'model'>(field: K, provider: ProviderId): string {
  return PROVIDER_DEFAULTS[provider][field] ?? '';
}

/**
 * Validates one written value against one field.
 *
 * Returns the settings with that field replaced, so a caller never has to hold a
 * half-validated object. `provider` is the exception that needs the most care:
 * choosing a provider resets the whole credential tuple, because the endpoint,
 * the model and the key all belong to the provider being left behind.
 */
function applyValue(settings: Settings, field: ConfigField, value: string): Settings {
  switch (field) {
    case 'interface': {
      if (!INTERFACES.includes(value as Settings['interface'])) throw new CliError('invalid_value', { field });
      return { ...settings, interface: value as Settings['interface'] };
    }
    case 'agentUrl': {
      if (!isValidAgentEndpoint(value)) throw new CliError('invalid_value', { field });
      return { ...settings, agentUrl: value };
    }
    case 'agentPerson':
      if (value.length > MAX_PERSON_LENGTH || !value.trim()) throw new CliError('invalid_value', { field });
      return { ...settings, agentPerson: value.trim() };
    case 'provider': {
      if (!isProviderId(value)) throw new CliError('invalid_provider');
      return {
        ...settings,
        provider: value,
        apiKey: '',
        baseUrl: providerDefault('baseUrl', value),
        model: providerDefault('model', value),
      };
    }
    case 'model':
      return { ...settings, model: value };
    case 'baseUrl':
      if (!isValidProviderEndpoint(value, settings.provider)) throw new CliError('invalid_value', { field });
      return { ...settings, baseUrl: value };
    case 'language':
      if (!LANGUAGES.includes(value as Settings['language'])) throw new CliError('invalid_value', { field });
      return { ...settings, language: value as Settings['language'] };
    case 'theme':
      if (value !== 'dark' && value !== 'light') throw new CliError('invalid_value', { field });
      return { ...settings, theme: value as Theme };
    case 'accent':
      if (!/^#[0-9a-f]{6}$/i.test(value)) throw new CliError('invalid_value', { field });
      return { ...settings, accent: value.toUpperCase() };
    default:
      // `apiKey` is refused by the argument parser before it can reach here.
      throw new CliError('sensitive_argument');
  }
}

/** Restores one field to the value a fresh install would have. */
function clearValue(settings: Settings, field: ConfigField): Settings {
  switch (field) {
    case 'interface':
      return { ...settings, interface: DEFAULT_SETTINGS.interface };
    case 'agentUrl':
      return { ...settings, agentUrl: DEFAULT_SETTINGS.agentUrl };
    case 'agentPerson':
      return { ...settings, agentPerson: DEFAULT_AGENT_PERSON };
    case 'provider':
      // Deliberately not resettable: there is no "no provider", and quietly
      // repointing a live session at a hosted vendor is not a reset.
      throw new CliError('not_resettable', { field });
    case 'model':
      return { ...settings, model: providerDefault('model', settings.provider) };
    case 'baseUrl':
      return { ...settings, baseUrl: providerDefault('baseUrl', settings.provider) };
    case 'language':
      return { ...settings, language: DEFAULT_SETTINGS.language };
    case 'theme':
      return { ...settings, theme: DEFAULT_SETTINGS.theme };
    case 'accent':
      return { ...settings, accent: DEFAULT_ACCENT };
    case 'apiKey':
      return { ...settings, apiKey: '' };
    default:
      throw new CliError('unknown_field', { field });
  }
}

export function setConfigField(config: PhoneConfig, field: string, value: string): PhoneConfig {
  if (!isConfigField(field)) throw new CliError('unknown_field', { field });
  const settings = applyValue(config.settings, field, value);
  return { ...config, settings: createSettings(settings) };
}

export function unsetConfigField(config: PhoneConfig, field: string): PhoneConfig {
  if (!isConfigField(field)) throw new CliError('unknown_field', { field });
  const settings = clearValue(config.settings, field);
  return { ...config, settings: createSettings(settings) };
}

/** The field's value as plain text, for `phone config get`. */
export function readConfigField(config: PhoneConfig, field: string): string {
  if (!isConfigField(field)) throw new CliError('unknown_field', { field });
  if (field === 'apiKey') {
    // The value itself is never printed: only where it came from, and a hint.
    if (hasEnvironmentApiKey()) return 'environment';
    return config.credential.present ? config.credential.hint : '';
  }
  if (isValueField(field)) return config.settings[field];
  throw new CliError('unknown_field', { field });
}
