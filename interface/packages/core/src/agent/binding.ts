import { mergeMessages } from '../shared-state.js';
import type { DuplexClient } from '../duplex-client.js';
import type { AgentClient } from './client.js';
import { toChatMessages } from './types.js';

/**
 * Keeps one transcript between the agent and the surface.
 *
 * The agent's store is the record of the conversation, and the shared client is
 * what a surface draws. Left alone they hold two lists: the surface's own turn
 * (which it writes the instant Enter is pressed, so the question appears without
 * a round trip) and the agent's row for the same turn. So the two are unioned by
 * id, which is exactly the merge the web bridge and the terminal already use for
 * their shared file — the agent's row wins, because it is the one with a
 * delivery status and a timestamp the agent chose.
 *
 * Nothing is cleared. `/clear` on the agent side is the agent's decision to make.
 */
export function bindAgentConversation(client: DuplexClient, agent: AgentClient): () => void {
  const adopt = () => {
    const timeline = toChatMessages(agent.getView().messages);
    if (!timeline.length) return;
    const merged = mergeMessages(client.getSnapshot().messages, timeline);
    client.adoptHistory(merged);
  };
  // Adopted once here, not only on the next change: a surface that binds to an
  // already-read agent would otherwise show an empty transcript until the agent
  // happened to say something.
  adopt();
  return agent.subscribe((event) => {
    if (event.type === 'view') adopt();
  });
}
