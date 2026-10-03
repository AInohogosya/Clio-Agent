/**
 * The agent link: everything that makes an agent reachable as an interface.
 *
 * The kit's own vocabulary — `Settings`, `ClientSnapshot`, `ChatMessage` — is
 * kept intact, so both surfaces render the agent and a direct provider through
 * the same components and the same staleness rules.
 */
export * from './types.js';
export * from './presentation.js';
export * from './channel.js';
export * from './client.js';
export * from './transport.js';
export * from './reasons.js';
export * from './binding.js';
