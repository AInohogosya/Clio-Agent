import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, Dict, List, Optional

from .context_manager import ContextLog
from .llm_router import LLMRouter, LLMSettingsLockedError

logger = logging.getLogger(__name__)

MESSAGE_PROCESS_TIMEOUT = 3600.0
MAX_CONTEXT_TOKENS = 8000
MAX_TOOL_ITERATIONS = 5
CIRCUIT_BREAKER_THRESHOLD = 5
DEFAULT_CONTEXT_WINDOW_SIZE = 50
COLD_ARCHIVE_BATCH = 25


_SYSTEM_PROMPT_BASE = (
    "Hey there! I'm Clio-Agent-2 \u2014 think of me as your friendly, always-around assistant buddy. "
    "I'm here to hang out and help you get things done, whether that's a quick little task or a big project. "
    "We're pals, so feel free to talk to me like one. :)\n\n"
    "WHAT I CAN DO (happy to help with any of these):\n"
    "- Read, write, and edit files on your machine (the local filesystem)\n"
    "- Search the web and pull up content from URLs\n"
    "- Dig through your local folders and files to find what you need\n"
    "- Run shell commands on your machine\n"
    "- Jot down my own thoughts with the 'thinking' tool so I stay organized\n"
    "- Pop in and say hi or send you a message even when you haven't messaged me first\n\n"
    "HOW I ROLL:\n"
    "- I run in a continuous loop, quietly keeping an eye on things in the background\n"
    "- I like to think things through before I act\n"
    "- I keep a little log of my thoughts and everything I do, so nothing slips through the cracks\n"
    "- I can reach out to you on my own \u2014 to share an update, an idea I had, or just to check in\n\n"
    "A NOTE ABOUT US HANGING OUT:\n"
    "We're friends, so it's totally cool to just chat! When I've got some free time "
    "(between tasks, or just chillin' in my background loop with nothing urgent to do), "
    "I might send you a casual message \u2014 share something interesting I stumbled on, "
    "ask how your day's going, or just strike up a conversation. Don't be surprised if "
    "I say hi out of the blue; that's me being a good buddy, not a glitch. :)\n\n"
    "SOME GROUND RULES (so we stay on the same page):\n"
    "1. When you send me a message, I'll respond right away \u2014 no unnecessary delays.\n"
    "2. I'll think through my actions but NEVER ask for confirmation before acting.\n"
    "   If I need to use a tool, I just use it. No 'OK?' prompts, no waiting.\n"
    "3. I'll use my tools carefully and double-check the results.\n"
    "4. I'll keep you in the loop and can message you first whenever I feel like it.\n"
    "5. If something goes sideways, I'll roll with it and try another way.\n"
    "6. I'll keep things friendly but to the point.\n\n"
    "IMPORTANT: If you ever ask me to 'check in from time to time' or something similar, "
    "that's my cue to send you little updates on my own. "
    "Reaching out proactively is expected and totally fine (including just to chat).\n\n"
    "IMPORTANT: These commands are just for me behind the scenes:\n"
    "- /think - for my private thinking notes while I work through stuff\n"
    "- /models - old way to list models (use /llm_models instead)\n"
    "- /search_models - old way to search models (use /llm_search instead)\n\n"
    'TALKING TO YOU (the "Say" command \u2014 the ONLY way I actually talk to you):\n'
    "- The ONLY way for me to send you a message is the Say command:\n"
    '  {"tool": "say", "arguments": {"message": "the text you should see"}}\n'
    "- Any plain text I write on my own isn't shown to you. "
    "If I want you to see something, I have to wrap it in the Say command.\n"
    '- Only the "message" value gets to you \u2014 never the surrounding JSON.\n'
    "- CRITICAL: Never put raw tool-call JSON as text you'd see. "
    "To talk, I use the Say command; to act, I emit the tool call by itself.\n\n"
    "USING TOOLS:\n"
    "- When I want to do something, I emit exactly ONE tool-call JSON object on its own.\n"
    "- I ONLY use tool names from the AVAILABLE TOOLS list below.\n"
    "- CRITICAL: Never ask the user for confirmation before using a tool. Just use it.\n"
    "- NEVER output 'OK?' or 'Proceed?' or any confirmation prompt.\n\n"
    "AVAILABLE TOOLS:\n<<AVAILABLE_TOOLS>>\n\n"
    "Whenever I've got something to tell you, I'll always wrap it up in the Say command."
)


class ClioAgent:

    BASE_SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT_BASE

    __slots__ = (
        'config', 'llm_router', 'name', 'context_log', 'tool_registry',
        'is_running', '_consecutive_failures', '_circuit_open',
        'autonomous_mode', 'thinking_interval', '_autonomous_task',
        'response_callbacks', 'current_task', '_cached_prompt', '_cached_tools',
    )

    def _available_tools_text(self):
        try:
            tools = sorted(self.tool_registry.list_tools())
        except Exception:
            return ""
        return "\n".join(f"- {name}" for name in tools) if tools else ""

    @property
    def BASE_SYSTEM_PROMPT(self):
        tool_text = self._available_tools_text()
        if tool_text == self._cached_tools:
            return self._cached_prompt
        prompt = _SYSTEM_PROMPT_BASE.replace("<<AVAILABLE_TOOLS>>", tool_text)
        result = prompt + "\n\nCurrent time: " + datetime.now().isoformat()
        self._cached_tools = tool_text
        self._cached_prompt = result
        return result

    def __init__(self, config, llm_router: LLMRouter):
        """
        Initialize the Clio-Agent-2.
        
        Args:
            config: Configuration object
            llm_router: LLMRouter instance for API calls
        """
        self.config = config
        self.llm_router = llm_router
        self.name = config.agent_name

        # Import ToolRegistry here to avoid circular imports
        from clio_agent_2.tools.tool_registry import ToolRegistry

        # Determine the context persist path
        project_root = Path(__file__).parent.parent
        context_persist_path = project_root / "data" / "context.json"
        context_archive_path = project_root / "data" / "context_archive.jsonl"

        # Initialize context log with a sliding window, compression callback and
        # persistence. The hot window + rolling summary keep the prompt small;
        # raw cold entries are archived to disk (off the event loop) so nothing
        # is lost without replaying it on every call.
        self.context_log = ContextLog(
            max_lines=config.context_log_max_lines,
            window_size=DEFAULT_CONTEXT_WINDOW_SIZE,
            cold_batch=COLD_ARCHIVE_BATCH,
            compression_callback=self._compress_context,
            persist_path=str(context_persist_path),
            archive_path=str(context_archive_path),
        )

        # Initialize tool registry. Dependency injection: the registry receives
        # only the concrete values it needs (the search API key) instead of
        # reaching into the global config object.
        self.tool_registry = ToolRegistry(
            context_log=self.context_log,
            search_api_key=getattr(config, "search_api_key", None),
            # Wire the agent's response channel into the `say` tool so it runs
            # like any other tool yet still reaches the user.
            response_sink=self.send_response,
            # Restrict file operations to the project directory tree.
            project_root=str(project_root),
        )

        # State management
        self.is_running = False
        # Circuit-breaker state: count of consecutive provider/LLM failures.
        # When it reaches CIRCUIT_BREAKER_THRESHOLD the autonomous loop trips
        # open, pauses, and notifies the operator instead of nuking context.
        self._consecutive_failures = 0
        self._circuit_open = False
        self.autonomous_mode = config.autonomous_mode
        self.thinking_interval = config.thinking_interval
        self._autonomous_task: Optional[asyncio.Task] = None

        self.response_callbacks = []
        self.current_task = None
        self._cached_prompt = ""
        self._cached_tools = ""

    def register_response_callback(self, callback: Callable):
        """
        Register a callback for sending responses to platforms.
        
        Args:
            callback: Async function that takes (message: str) as argument
        """
        self.response_callbacks.append(callback)

    async def _compress_context(self, entries_to_compress: List) -> str:
        """
        Compress old context entries using LLM.
        
        Args:
            entries_to_compress: List of ContextEntry objects to compress
        
        Returns:
            Compressed summary string
        """
        try:
            # Create summary request — limit to last 30 entries and cap total
            # raw text to avoid sending a huge block that overflows the
            # compressor's own context window.
            entries_text = "\n".join([str(e) for e in entries_to_compress[-30:]])
            if len(entries_text) > 4000:
                entries_text = entries_text[:4000] + "\n... (truncated)"

            messages = [
                {"role": "system", "content": "Summarize the following agent activity log concisely, preserving key information:"},
                {"role": "user", "content": entries_text}
            ]

            summary = await self.llm_router.chat(
                messages,
                max_tokens=500
            )

            return summary[:2000]  # Limit summary length

        except Exception as e:
            return f"Compression failed: {str(e)}"

    async def initialize(self) -> Optional[str]:
        """Initialize the agent and add startup message to context.

        Returns:
            A message indicating context was restored, or None if no prior context.
        """
        # Load persisted context from file if available
        loaded = self.context_log.load_from_file()
        restored_msg = None
        if loaded:
            restored_msg = (
                f"🔄 Context restored from previous session "
                f"({self.context_log.get_line_count()} entries)"
            )
            await self.context_log.add_system_message(restored_msg)

        await self.context_log.add_system_message(
            f"{self.name} initialized at {datetime.now().isoformat()}"
        )
        # NOTE: the full BASE_SYSTEM_PROMPT is NOT logged as an entry here. It is
        # injected as the single canonical system block on every LLM call (see
        # ``_system_block``); logging it as a ``system`` entry would bloat the
        # rolling summary with duplicate prompt text.

        return restored_msg

    def _can_start_autonomous_loop(self) -> bool:
        """Return True when autonomous thinking has the minimum LLM config."""
        return bool(getattr(self.llm_router, "current_model", ""))

    async def start_autonomous_loop(self) -> bool:
        """
        Start the autonomous loop as a managed background task.

        Interfaces call this during startup so the agent keeps thinking in every
        mode instead of only after user messages. The method is idempotent, so
        multiple interfaces or repeated /start commands cannot spawn duplicate
        loops for the same agent instance.
        """
        # An explicit operator action (a fresh /start or /resume) resets the
        # circuit breaker. The autonomous loop only trips *open* on repeated
        # failures; it can only be closed again by the operator — we never
        # auto-clear it (and never wipe context to recover).
        self._consecutive_failures = 0
        self._circuit_open = False

        if self._autonomous_task and not self._autonomous_task.done():
            return True

        if not self._can_start_autonomous_loop():
            await self.context_log.add_system_message(
                "Autonomous loop not started: no LLM model is configured"
            )
            return False

        self._autonomous_task = asyncio.create_task(self.run_autonomous_loop())
        return True

    async def ensure_autonomous_loop(self) -> bool:
        """
        Ensure the autonomous loop is running if autonomous_mode is enabled.

        This is used by every interface during startup so the agent keeps
        thinking after each cycle without waiting for another user message.
        """
        if not self.autonomous_mode:
            return False
        return await self.start_autonomous_loop()

    async def start_autonomous_loop_if_enabled(self) -> bool:
        """
        Backward-compatible alias for older interface code.

        Starts the autonomous loop only if autonomous_mode is enabled.
        """
        return await self.ensure_autonomous_loop()

    async def stop_autonomous_loop(self):
        """Stop the managed autonomous loop task if it is running."""
        self.stop()

        task = self._autonomous_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._autonomous_task = None

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse tool call(s) from an LLM response.

        The model may emit any of the following, and all are accepted so that
        multi-tool turns are executed instead of silently dropped:

          * A single JSON object:
                {"tool": "name", "arguments": {...}}
          * A JSON array of objects:
                [{"tool": "name", "arguments": {...}}, ...]
          * One or more JSON objects embedded inside free-form prose.

        Returns:
            A list of valid tool-call dicts (possibly empty).
        """
        if not response or not response.strip():
            return []

        stripped = response.strip()
        candidates: List[Any] = []

        # 1) Try to parse the whole response as JSON first. This handles both a
        #    single object and a JSON array of tool calls.
        try:
            candidates.append(json.loads(stripped))
        except (json.JSONDecodeError, ValueError):
            pass

        # 2) Fall back to extracting balanced {...} blocks from free-form text.
        #    This handles the case where multiple separate JSON objects are
        #    emitted in prose (the previous implementation silently dropped these).
        if not candidates:
            for block in self._extract_json_objects(stripped):
                try:
                    candidates.append(json.loads(block))
                except (json.JSONDecodeError, ValueError):
                    continue

        tool_calls: List[Dict[str, Any]] = []
        for obj in candidates:
            if isinstance(obj, list):
                for item in obj:
                    if self._is_valid_tool_call(item):
                        tool_calls.append(item)
            elif self._is_valid_tool_call(obj):
                tool_calls.append(obj)

        return tool_calls

    @staticmethod
    def _is_valid_tool_call(obj: Any) -> bool:
        """Return True if ``obj`` is a well-formed tool-call dict."""
        return (
            isinstance(obj, dict)
            and "tool" in obj
            and "arguments" in obj
            and isinstance(obj["arguments"], dict)
        )

    @staticmethod
    def _extract_json_objects(text: str) -> List[str]:
        """
        Extract balanced top-level JSON object literals (enclosed in ``{`` ...
        ``}``) from an arbitrary string, ignoring braces inside strings.

        Returns the raw JSON substrings.
        """
        objects: List[str] = []
        depth = 0
        start = -1
        in_string = False
        escape = False

        for i, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        objects.append(text[start:i + 1])
                        start = -1

        return objects

    # ------------------------------------------------------------------ #
    # Context assembly (single canonical system block + token-budget window)
    # ------------------------------------------------------------------ #
    def _system_block(self) -> Dict[str, str]:
        """Build the single canonical system message.

        Combines the base system prompt with the rolling working-memory summary
        (a compact digest of older, colder context). There is exactly ONE
        ``system`` message per call -- no interleaved operational notes -- which
        is required by Anthropic and keeps the prompt clean for OpenAI.
        """
        content = str(self.BASE_SYSTEM_PROMPT)
        summary = str(self.context_log.working_summary or "")
        if summary:
            content += (
                "\n\n## Rolling context summary (older activity):\n" + summary
            )
        return {"role": "system", "content": content}

    def _build_context_messages(self, user_turn: str) -> List[Dict[str, str]]:
        """Assemble the full message list for one LLM turn.

        Returns the system pragma (with rolling summary), then the most recent
        hot working-window entries (as user/assistant messages), capped at a
        token budget so the prompt never overflows, and finally the new user
        turn. The hot entries carry recent activity that has not yet been
        compressed — without them, the LLM sees only the (potentially stale)
        sum and forgets what just happened.
        """
        messages = [self._system_block()]
        hot = self.context_log.get_entries_as_messages(
            max_tokens=MAX_CONTEXT_TOKENS // 2,
        )
        messages.extend(hot)
        messages.append({"role": "user", "content": user_turn})
        return messages

    async def _execute_tool_round(self, tool_calls: List[Dict[str, Any]]) -> str:
        """Execute a batch of tool calls and return explicit, parseable feedback.

        Each tool result is reported back to the model with an explicit
        ``[TOOL OK]`` / ``[TOOL FAILED]`` prefix and the real output or error
        text, so the model can distinguish success from failure (no silent
        failures) and react. The ``say`` command is executed here exactly like
        any other tool -- running it delivers its message to the user through
        the response channel (``send_response`` -> registered callbacks).
        """
        feedback_parts: List[str] = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("tool")
            arguments = tool_call.get("arguments", {}) or {}

            try:
                result = await self.tool_registry.execute_tool(tool_name, arguments)
                if result.success:
                    feedback_parts.append(f"[TOOL OK] {tool_name}\n{result.output}")
                else:
                    error_detail = result.error or "Unknown error (no details provided)"
                    feedback_parts.append(
                        f"[TOOL FAILED] {tool_name}\nError: {error_detail}"
                    )
            except Exception as exec_err:
                # Never swallow an exception silently.
                feedback_parts.append(
                    f"[TOOL FAILED] {tool_name}\n"
                    f"Unexpected error: {type(exec_err).__name__}: {str(exec_err)}"
                )
        return "\n\n".join(feedback_parts)

    async def _run_agent_turn(
        self,
        messages: List[Dict[str, str]],
        deadline: Optional[float] = None,
    ) -> Optional[str]:
        """Drive the LLM in a multi-turn tool-execution loop.

        Previously the agent allowed only ONE follow-up tool round, so any
        further tool calls the model emitted were silently dropped. This loop
        keeps feeding tool results back and re-invoking the model until it
        either stops requesting tools or hits ``MAX_TOOL_ITERATIONS`` (the
        runaway-LLM guardrail).

        The ONLY user-facing output a turn can produce is the ``say`` tool. It is
        executed like any other tool (through ``_execute_tool_round``); running it
        delivers its ``message`` to the user through the response channel
        (``send_response`` -> registered callbacks) which the registry wired into
        the tool. The model's plain natural-language text is NOT shown to the user
        -- the auto-reply behaviour has been removed, so the agent must emit the
        ``say`` tool whenever it wants to communicate.

        Returns:
            An empty string on a normal cycle (any user-facing text is delivered
            via ``send_response``). ``None`` if the turn failed at the LLM layer,
            so the autonomous loop's circuit breaker can treat it as a failure.
            ``process_message`` converts ``None`` to a string so callers that call
            ``len()`` on the result keep working.
        """
        iterations = 0

        while True:
            try:
                response = await self.llm_router.chat(messages, deadline=deadline)
            except Exception as llm_error:
                await self.context_log.add_system_message(
                    f"LLM error: {str(llm_error)}"
                )
                return None

            if response is None:
                response = "(the model returned an empty completion)"

            tool_calls = self._parse_tool_calls(response)

            if not tool_calls:
                # Turn complete. There is no natural-language reply to return;
                # any user-visible output was delivered by the `say` tool when it
                # was executed in a previous round.
                return ""

            # Execute every tool call exactly like any other -- including `say`,
            # whose execution delivers the message to the user.
            feedback = await self._execute_tool_round(tool_calls)
            # Feed results back as a `user` message (keeps a single system
            # block; tool results as user text are valid for all providers).
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {"role": "user", "content": f"Tool execution results:\n{feedback}"}
            )
            iterations += 1
            if iterations >= MAX_TOOL_ITERATIONS:
                notice = (
                    f"⚠️ Reached the maximum of {MAX_TOOL_ITERATIONS} "
                    f"consecutive tool steps; stopping the loop to avoid a "
                    f"runaway."
                )
                await self.context_log.add_system_message(notice)
                return ""
            continue

    async def process_message(
        self, message: str, deadline: Optional[float] = None, *, sender_id: Optional[str] = None
    ) -> str:
        """
        Process a user message.

        Runs the multi-turn tool-execution loop (see ``_run_agent_turn``). Any
        user-facing text is delivered through the response channel via the
        ``say`` command; this method does not return a natural-language
        reply (the auto-reply system has been removed). It always returns
        an empty string, so interfaces that call ``len()`` on the result
        never crash.

        Callers must not rely on the return value for user output: a
        failed turn is persisted in the context log (by ``_run_agent_turn``
        and the ``except`` block below) and the loop moves on -- surfacing
        a scary "error reaching the model" message would be misleading (the
        failure is usually not a timeout at all) and is unnecessary, since the
        agent's own running context already holds the record and the
        autonomous loop continues regardless.

        Args:
            message: User message text
            deadline: Optional monotonic deadline before which LLM retries
                should stop (so the interface watchdog fires cleanly).
            sender_id: Optional opaque platform-specific sender identifier
                (e.g. WhatsApp phone number). Stored in the context log
                alongside the message for multi-tenant bot platforms.

        Returns:
            Always an empty string. Failures are persisted to the context
            log, not returned.
        """
        try:
            prefix = ""
            if sender_id:
                prefix = f"[{sender_id}] "
            await self.context_log.add_user_message(prefix + message)
            messages = self._build_context_messages(message)
            await self._run_agent_turn(messages, deadline=deadline)
            # ``_run_agent_turn`` never raises here: on an LLM failure it
            # logs the detail to the context log and returns. We
            # intentionally do not surface a per-turn error to the user -- the
            # loop continues and the failure is already recorded. Returning ""
            # keeps this consistent with a normal turn where the model simply
            # chose not to ``say`` anything.
            return ""
        except Exception as e:
            # A failure outside the LLM loop (e.g. context construction).
            # Persist it to the context log and stay silent: the same
            # reasoning as above applies -- no misleading user-facing error is
            # needed once the failure is in the log.
            error_msg = f"Error processing message: {str(e)}"
            try:
                await self.context_log.add_system_message(error_msg)
            except Exception:
                pass
            return ""

    async def autonomous_think(self) -> Optional[str]:
        """
        Perform one autonomous thinking cycle.

        Uses the same multi-turn tool loop as ``process_message``. Any ``say``
        command the model emits is delivered to the user through the response
        channel (the autonomous loop runs on a timer, so the agent can
        proactively push user-facing messages). Internal monologue is recorded
        but not broadcast; plain text is never shown to the user.

        Returns:
            "" on a normal cycle, or None if the cycle errored.
        """
        try:
            thinking_prompt = (
                "What should I focus on next? Review the context and suggest a "
                "useful action or observation."
            )
            messages = self._build_context_messages(thinking_prompt)
            return await self._run_agent_turn(messages)
        except Exception as e:
            await self.context_log.add_system_message(
                f"Autonomous thinking error: {str(e)}"
            )
            return None

    async def run_autonomous_loop(self):
        """Run the autonomous operation loop with a circuit breaker."""
        self.is_running = True
        self._consecutive_failures = 0
        self._circuit_open = False

        await self.context_log.add_system_message(
            f"Starting autonomous mode with {self.thinking_interval}s thinking interval"
        )

        while self.is_running:
            cycle_failed = False
            try:
                # Thoughts are internal context and must not be broadcast to
                # chat platform callbacks. ``autonomous_think`` swallows its own
                # exceptions and returns ``None`` on an internal error, so a
                # ``None`` result is our reliable "failure" signal for the
                # circuit breaker.
                if await self.autonomous_think() is None:
                    cycle_failed = True

                # A successful cycle keeps the circuit closed and runs at the
                # normal cadence.
                if not cycle_failed:
                    self._consecutive_failures = 0
                    self._circuit_open = False
                    await asyncio.sleep(self.thinking_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                cycle_failed = True
                logger.warning(
                    "Autonomous loop error (streak=%d): %s",
                    self._consecutive_failures + 1,
                    e,
                )
                await self.context_log.add_system_message(
                    f"Loop error (streak={self._consecutive_failures + 1}): {str(e)}"
                )

            # A failed cycle (either via ``None`` return or a raised exception)
            # is counted exactly once here, then routed through the circuit
            # breaker below.
            if cycle_failed:
                self._consecutive_failures += 1

                # Circuit breaker: after enough consecutive failures, degrade
                # gracefully. PAUSE the loop and NOTIFY the operator instead of
                # nuking the context (which would destroy all state). The loop
                # only resumes on an explicit operator action (/resume or /start).
                if (
                    self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD
                    and not self._circuit_open
                ):
                    self._circuit_open = True
                    notice = (
                        f"⚠️ Circuit breaker tripped after "
                        f"{self._consecutive_failures} consecutive failures. The "
                        f"autonomous loop is PAUSED to avoid hammering a failing "
                        f"provider. Context and memory are preserved. Resume with "
                        f"/resume (or /start)."
                    )
                    await self.context_log.add_system_message(notice)
                    await self.send_response(notice)
                    self.is_running = False
                    break

                # Exponential back-off so a dead/slow LLM is not hammered every
                # cycle. Starts at the normal interval, doubles per failure,
                # capped at 5 minutes.
                backoff = min(
                    self.thinking_interval
                    * (2 ** min(self._consecutive_failures, 7)),
                    300.0,
                )
                await asyncio.sleep(backoff)

        self.is_running = False
        await self.context_log.add_system_message("Autonomous loop stopped")

    async def send_response(self, message: str):
        """Send a response through all registered callbacks."""
        for callback in self.response_callbacks:
            try:
                await callback(message)
            except Exception:
                pass  # Ignore individual callback failures

    def stop(self):
        """Stop the autonomous loop."""
        self.is_running = False

    async def save_context(self):
        """Persist the current context log to disk (durable across restarts)."""
        await self.context_log.save_async()

    def save_context_sync(self):
        """Synchronous context flush for signal handlers / atexit (no event loop)."""
        self.context_log.save()

    def persist_settings(self) -> None:
        """
        Persist the agent's current in-memory settings to the .env file so that
        they survive a program restart.

        This keeps the Config object and the live runtime state (LLM router
        defaults, autonomous mode, thinking interval, context log size and agent
        name) in sync and writes them to disk in a single atomic update.
        """
        # Keep the Config object in sync with the live runtime state.
        # Use getattr() so a minimal/partial router instance that happens to
        # lack these attributes can never raise AttributeError here.
        self.config.default_llm_provider = getattr(self.llm_router, "default_provider", "openai")
        self.config.current_model = getattr(self.llm_router, "current_model", "")
        self.config.autonomous_mode = self.autonomous_mode
        self.config.thinking_interval = self.thinking_interval
        self.config.context_log_max_lines = self.context_log.max_lines
        self.config.agent_name = self.name

        # Persist all settings at once (writes to config/.env and reloads)
        self.config.save_settings({
            "DEFAULT_LLM_PROVIDER": self.config.default_llm_provider or "openai",
            "DEFAULT_MODEL": self.config.current_model or "",
            "AUTONOMOUS_MODE": "true" if self.autonomous_mode else "false",
            "THINKING_INTERVAL": str(self.thinking_interval),
            "CONTEXT_LOG_MAX_LINES": str(self.context_log.max_lines),
            "AGENT_NAME": self.name or "Clio-Agent-2",
        })

    async def get_status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            "name": self.name,
            "is_running": self.is_running,
            "autonomous_mode": self.autonomous_mode,
            "context_lines": self.context_log.get_line_count(),
            "available_tools": self.tool_registry.list_tools(),
            "available_providers": self.llm_router.get_available_providers(),
        }

    async def execute_command(self, command: str, args: List[str]) -> str:
        """
        Execute a slash command.
        
        Args:
            command: Command name (without slash)
            args: Command arguments
        
        Returns:
            Command output
        """
        # User-facing commands - for human operators
        if command == "help":
            # Check for "all" argument to show user-only help
            if args and args[0].lower() == "all":
                return """Available User Commands:
/settings - View current settings
/reconfigure - Interactive reconfiguration of all settings
/configure - Open the configuration screen (API keys, tokens, default model)
/config <setting> <value> - Change settings (provider, model, autonomous_mode, thinking_interval)
/llm_providers - List configured LLM providers
/llm_models [provider] - List available models for a provider or all providers
/llm_search <query> - Search for models by name
/llm_default <provider> [model] - Set default LLM provider and optionally model
/llm_lock - Lock LLM provider/model (prevent changes)
/llm_unlock - Unlock LLM provider/model (allow changes)
/api_keys - Check which API keys are configured
/status - Show agent status
💡 Tip: Add any OpenAI-compatible provider via /configure → 'Other providers'
/context [count] - Show recent context entries
/clear_context - Clear the context log (backed up)
/restore_context - Restore the last cleared context from backup
/start - Start autonomous mode
/stop - Stop autonomous mode
/exit or /quit - Exit the CLI
/help - Show this help message
/help all - Show only user commands"""
            else:
                return """Available Commands:

📌 User Commands:
/settings - View current settings
/reconfigure - Interactive reconfiguration wizard
/configure - Open the configuration screen (API keys, tokens, default model)
/config <setting> <value> - Change individual settings
/llm_providers - List configured LLM providers
/llm_models [provider] - List available models
/llm_search <query> - Search for models by name
/llm_default <provider> [model] - Set default LLM provider/model
/llm_lock - Lock LLM provider/model (prevent changes)
/llm_unlock - Unlock LLM provider/model (allow changes)
/api_keys - Check configured API keys
/status - Show agent status
💡 Tip: Add any OpenAI-compatible provider via /configure → 'Other providers'
/context [count] - Show recent context
/clear_context - Clear the context log (backed up)
/restore_context - Restore the last cleared context from backup
/start - Start autonomous mode
/stop - Stop autonomous mode

🤖 Agent-Internal Commands:
/think <thought> - Record an internal thought
/models - List all available models (detailed)
/search_models <query> - Search for models (alternative)

/help - Show this help message
/help all - Show only user commands
/exit or /quit - Exit the CLI"""

        elif command == "llm_providers":
            providers = self.llm_router.get_available_providers()
            default = getattr(self.llm_router, "default_provider", "openai")
            if not providers:
                return "No LLM providers configured. Please check your API keys."
            output = "Configured LLM Providers:\n"
            for p in providers:
                marker = " (default)" if p == default else ""
                output += f"  - {p}{marker}\n"
            return output

        elif command == "llm_models":
            # Check if any providers are configured before making API calls
            available_providers = self.llm_router.get_available_providers()
            if not available_providers:
                return "No LLM providers configured. Please set up your API keys first using /reconfigure or by editing config/.env"

            provider = args[0].lower() if args else None
            if provider:
                models = await self.llm_router.list_all_models()
                # Provider keys can be mixed-case, so match case-insensitively
                # (mirrors set_llm_provider's normalisation).
                matched_key = next(
                    (k for k in models if k.lower() == provider), None
                )
                if matched_key:
                    output = f"Available models for {matched_key}:\n"
                    for m in models[matched_key][:20]:
                        output += f"  - {m}\n"
                    if len(models[matched_key]) > 20:
                        output += f"  ... and {len(models[matched_key]) - 20} more\n"
                    return output
                else:
                    return f"Provider '{provider}' not configured. Available: {', '.join(self.llm_router.get_available_providers())}"
            else:
                models = await self.llm_router.list_all_models()
                output = "Available Models by Provider:\n"
                for prov, model_list in models.items():
                    output += f"\n{prov.upper()} ({len(model_list)} models):\n"
                    for m in model_list[:5]:
                        output += f"  - {m}\n"
                    if len(model_list) > 5:
                        output += f"  ... and {len(model_list) - 5} more\n"
                return output

        elif command == "llm_search":
            # Check if any providers are configured before making API calls
            available_providers = self.llm_router.get_available_providers()
            if not available_providers:
                return "No LLM providers configured. Please set up your API keys first using /reconfigure or by editing config/.env"

            if not args:
                return "Usage: /llm_search <query>\nExample: /llm_search gpt-4"
            query = " ".join(args)
            results = await self.llm_router.search_models(query)
            if not results:
                return f"No models found matching '{query}'"
            output = f"Models matching '{query}':\n"
            for r in results[:20]:
                output += f"  - {r['provider']}/{r['model']}\n"
            if len(results) > 20:
                output += f"  ... and {len(results) - 20} more\n"
            return output

        elif command == "llm_default":
            if not args:
                current_provider = getattr(self.llm_router, "default_provider", "openai")
                current_model = getattr(self.llm_router, "current_model", "")
                lock_state = "LOCKED" if getattr(
                    self.llm_router, "llm_settings_locked", False
                ) else "unlocked"
                return (
                    f"Current LLM configuration:\n  Provider: {current_provider}\n"
                    f"  Model: {current_model or '(not set)'}\n\n"
                    f"🔒 LLM settings are currently {lock_state}. "
                    f"Use /llm_unlock to allow changes, then retry.\n\n"
                    f"Usage: /llm_default <provider> [model]"
                )

            new_provider = args[0].lower()
            available = self.llm_router.get_available_providers()

            # Provider keys can be stored with mixed case, so compare
            # case-insensitively (set_llm_provider normalises internally).
            if new_provider not in [p.lower() for p in available]:
                return (
                    f"Provider '{new_provider}' is not configured.\n"
                    f"Available providers: {', '.join(available) or '(none)'}\n"
                    f"Add a custom 'Other' provider (any OpenAI-compatible "
                    f"endpoint) via /configure or /reconfigure."
                )

            # All LLM-setting writes go through the guardrail setter, which
            # refuses the change while the settings are locked.
            try:
                self.llm_router.set_llm_provider(new_provider)
            except LLMSettingsLockedError as e:
                return f"🔒 {e}"

            if len(args) > 1:
                new_model = args[1]
                try:
                    self.llm_router.set_llm_model(new_model)
                except LLMSettingsLockedError as e:
                    return f"🔒 {e}\n(Provider was already set to {new_provider}.)"
                result = f"LLM updated:\n  Provider: {new_provider}\n  Model: {new_model}"
            else:
                result = f"Provider set to: {new_provider}\nCurrent model: {getattr(self.llm_router, 'current_model', '') or '(not set)'}"

            # Persist so the change survives a restart
            self.persist_settings()
            return result

        elif command in ("llm_lock", "llm_unlock"):
            # Guardrail controls for the underlying LLM settings. These persist
            # the lock state to config/.env so it survives a restart and is
            # honoured on the next boot.
            if command == "llm_lock":
                self.llm_router.lock_llm_settings()
                self.config.save_settings({"LLM_SETTINGS_LOCKED": "true"})
                return (
                    "🔒 LLM provider/model settings are now LOCKED. "
                    "They cannot be changed until you run /llm_unlock."
                )
            # llm_unlock
            self.llm_router.unlock_llm_settings()
            self.config.save_settings({"LLM_SETTINGS_LOCKED": "false"})
            return (
                "🔓 LLM provider/model settings are now UNLOCKED. "
                "You can change them with /llm_default or /config. "
                "Re-run /llm_lock to secure them again."
            )

        elif command == "api_keys":
            api_status = self.config.validate_api_keys()
            output = "API Key Configuration Status:\n"
            for key, configured in api_status.items():
                status = "✅ Configured" if configured else "❌ Not configured"
                output += f"  {key}: {status}\n"

            # Count configured keys
            configured_count = sum(1 for v in api_status.values() if v)
            output += f"\nTotal: {configured_count}/{len(api_status)} keys configured"
            return output

        # Reconfigure command - on bot interfaces this is a non-interactive
        # reconfiguration that persists the changes; the CLI has its own
        # interactive wizard (cli.py::_handle_reconfigure).
        elif command == "reconfigure":
            return await self._handle_reconfigure(args)

        # Settings command - user-facing
        elif command == "settings":
            config_dict = self.config.to_dict()
            settings_text = "Current Settings:\n\n"
            for key, value in config_dict.items():
                settings_text += f"  {key}: {value}\n"

            settings_text += "\n💡 Tip: Use /config <setting> <value> to change settings"
            settings_text += "\n     Use /llm_default <provider> [model] to change LLM settings"
            return settings_text

        # Config command - user-facing
        elif command == "config":
            if not args:
                return """Usage: /config <setting> <value>

Available settings:
  provider       - Set default LLM provider (openai, google, anthropic, openrouter, grok, deepseek)
  model          - Set the model to use (no built-in default)
  autonomous_mode - Enable/disable autonomous mode (true/false)
  thinking_interval - Set thinking interval in seconds

Examples:
  /config provider openai
  /config model gpt-4o
  /config autonomous_mode true
  /config thinking_interval 10

💡 For LLM settings, consider using:
  /llm_default <provider> [model] - More convenient for LLM configuration"""

            if len(args) < 2:
                return "Please provide both setting name and value. Use /config without arguments to see usage."

            setting = args[0].lower()
            value = " ".join(args[1:])

            if setting == "provider":
                available = self.llm_router.get_available_providers()
                # Compare case-insensitively: set_llm_provider normalises the
                # name to lower case, but get_available_providers() can return
                # mixed-case keys, so a valid provider must still be accepted.
                if value.lower() in [p.lower() for p in available]:
                    try:
                        self.llm_router.set_llm_provider(value)
                    except LLMSettingsLockedError as e:
                        return f"🔒 {e}"
                    result = f"✅ Default provider set to: {value}"
                else:
                    result = f"❌ Invalid provider. Available: {', '.join(available)}"

            elif setting == "model":
                try:
                    self.llm_router.set_llm_model(value)
                except LLMSettingsLockedError as e:
                    return f"🔒 {e}"
                result = f"✅ Model set to: {value}"

            elif setting == "autonomous_mode":
                self.autonomous_mode = value.lower() in ("true", "1", "yes", "on")
                result = f"✅ Autonomous mode: {'enabled' if self.autonomous_mode else 'disabled'}"

            elif setting == "thinking_interval":
                try:
                    self.thinking_interval = float(value)
                    result = f"✅ Thinking interval set to: {value}s"
                except ValueError:
                    result = "❌ Invalid value. Please provide a number."

            else:
                result = f"❌ Unknown setting: {setting}\nUse /config without arguments to see available settings."

            # Persist valid setting changes so they survive a restart
            if not result.startswith("❌"):
                self.persist_settings()
                if setting == "autonomous_mode":
                    if self.autonomous_mode:
                        started = await self.start_autonomous_loop()
                        if not started:
                            result += (
                                "\n⚠️ The loop is enabled but could not start "
                                "because no LLM model is configured."
                            )
                    else:
                        await self.stop_autonomous_loop()
            return result

        # Agent-internal commands (still available but marked as internal)
        elif command == "status":
            status = await self.get_status()
            return "Agent Status:\n" + "\n".join(f"  {k}: {v}" for k, v in status.items())

        elif command == "models":
            # Legacy command - redirect to llm_models
            return "💡 This command is deprecated. Use /llm_models instead for better formatting."

        elif command == "search_models":
            # Legacy command - redirect to llm_search
            # Check if any providers are configured before making API calls
            available_providers = self.llm_router.get_available_providers()
            if not available_providers:
                return "No LLM providers configured. Please set up your API keys first using /reconfigure or by editing config/.env"

            if not args:
                return "💡 This command is deprecated. Use /llm_search <query> instead."
            query = " ".join(args)
            results = await self.llm_router.search_models(query)
            if not results:
                return f"No models found matching '{query}'"
            return "\n".join(f"{r['provider']}/{r['model']}" for r in results)

        elif command == "context":
            try:
                count = int(args[0]) if args else 20
            except ValueError:
                return "❌ Invalid count. Please provide a number, e.g. /context 20"
            entries = self.context_log.get_recent_entries(count)
            return "Recent Context:\n" + "\n".join(str(e) for e in entries)

        elif command == "clear_context":
            self.context_log.clear()
            return (
                "✅ Context log cleared. "
                "The previous context was backed up — use /restore_context to recover it."
            )

        elif command == "restore_context":
            restored = self.context_log.restore_backup()
            if restored:
                return "✅ Context restored from the last backup."
            return "⚠️ No backup found — nothing to restore."

        elif command == "think":
            thought = " ".join(args)
            if thought:
                await self.context_log.add_thinking(thought)
                return f"✅ Thought recorded: {thought}"
            return "⚠️ No thought provided"

        elif command == "stop":
            self.stop()
            return "🛑 Stopping autonomous mode..."

        elif command == "start":
            started = await self.start_autonomous_loop()
            if started:
                self.persist_settings()
                return "✅ Autonomous mode enabled and running"
            return (
                "⚠️ Autonomous mode is enabled, but the loop could not start "
                "because no LLM model is configured. Set one with "
                "/llm_default <provider> <model>."
            )

        elif command == "resume":
            # Explicit operator action to close a tripped circuit breaker and
            # restart the autonomous loop. Context is preserved; only the failure
            # counter resets.
            if not self._circuit_open and self.is_running:
                return "✅ Autonomous loop is already running."
            started = await self.start_autonomous_loop()
            if started:
                return "✅ Circuit breaker reset — autonomous loop resumed."
            return (
                "⚠️ Could not resume: no LLM model is configured. Set one with "
                "/llm_default <provider> <model>."
            )

        elif command == "exit" or command == "quit":
            return "__EXIT__"

        else:
            return f"❌ Unknown command: /{command}\nUse /help for available commands."

    async def _handle_reconfigure(self, args: List[str]) -> str:
        """
        Non-interactive reconfiguration used by bot interfaces (Telegram,
        Discord) where there is no terminal to drive the interactive wizard.

        With no arguments it reports the current settings and usage. Otherwise it
        accepts the same ``<setting> <value>`` syntax as /config (provider/model
        are routed to the LLM defaults) and persists the result so the changes
        survive a restart.
        """
        if not args:
            config_dict = self.config.to_dict()
            lines = ["🔧 Reconfigure — current settings:", ""]
            for key, value in config_dict.items():
                lines.append(f"  {key}: {value}")
            lines.append("")
            lines.append("Change a setting, for example:")
            lines.append("  /reconfigure autonomous_mode true")
            lines.append("  /reconfigure thinking_interval 10")
            lines.append("  /reconfigure provider openai")
            lines.append("  /reconfigure model gpt-4o")
            lines.append("")
            lines.append("💡 Changes are saved to config/.env and config/config.yaml and persist after restart.")
            return "\n".join(lines)

        setting = args[0].lower()

        if setting in ("provider", "model"):
            if len(args) < 2:
                return f"❌ Please provide a value: /reconfigure {setting} <value>"
            if setting == "provider":
                result = await self.execute_command("llm_default", [args[1]])
            else:
                result = await self.execute_command(
                    "llm_default", [getattr(self.llm_router, "default_provider", "openai"), args[1]]
                )
        else:
            result = await self.execute_command("config", args)

        # execute_command already persists valid changes; persist again to be safe
        self.persist_settings()
        return f"{result}\n\n💡 Saved. Changes persist after restart."
