"""
Tools module for Clio-Agent-2.
Provides file editing, web search, and file search capabilities.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import aiohttp


logger = logging.getLogger(__name__)


class ToolResult:
    """Represents the result of a tool execution."""
    
    def __init__(self, success: bool, output: str, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }


class FileEditTool:
    """Tool for reading, writing, and modifying local files."""
    
    @staticmethod
    async def read_file(
        filepath: Optional[str] = None,
        max_lines: int = 100,
        path: Optional[str] = None,
    ) -> ToolResult:
        """
        Read content from a file.

        Accepts either ``filepath`` or ``path`` as the target (the latter is an
        alias commonly emitted by the agent). At least one must be provided.

        Args:
            filepath: Path to the file to read (preferred argument name).
            max_lines: Maximum number of lines to read.
            path: Alias for ``filepath``; used if ``filepath`` is not given.

        Returns:
            ToolResult with file content or error message
        """
        # Accept ``path`` as an alias for ``filepath`` (commonly emitted by the agent).
        target = filepath if filepath is not None else path
        if not target:
            return ToolResult(
                False, "", "Missing required argument: provide 'filepath' or 'path'."
            )
        try:
            resolved = Path(target).expanduser().resolve()

            if not resolved.exists():
                return ToolResult(False, "", f"File not found: {target}")

            if not resolved.is_file():
                return ToolResult(False, "", f"Not a file: {target}")

            with open(resolved, 'r', encoding='utf-8') as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"... ({max_lines} lines shown, file continues)")
                        break
                    lines.append(line.rstrip('\n'))
                
                content = '\n'.join(lines)
                return ToolResult(True, content)
        
        except Exception as e:
            return ToolResult(False, "", f"Error reading file: {str(e)}")
    
    @staticmethod
    async def write_file(
        filepath: Optional[str] = None,
        content: str = "",
        path: Optional[str] = None,
    ) -> ToolResult:
        """
        Write content to a file (creates or overwrites).

        Accepts either ``filepath`` or ``path`` as the target (the latter is an
        alias commonly emitted by the agent). At least one must be provided.

        Args:
            filepath: Path to the file to write (preferred argument name).
            content: Content to write to the file.
            path: Alias for ``filepath``; used if ``filepath`` is not given.

        Returns:
            ToolResult with success status
        """
        # Accept ``path`` as an alias for ``filepath`` (commonly emitted by the agent).
        target = filepath if filepath is not None else path
        if not target:
            return ToolResult(
                False, "", "Missing required argument: provide 'filepath' or 'path'."
            )
        try:
            resolved = Path(target).expanduser().resolve()

            # Create parent directories if they don't exist
            resolved.parent.mkdir(parents=True, exist_ok=True)

            with open(resolved, 'w', encoding='utf-8') as f:
                f.write(content)

            return ToolResult(True, f"Successfully wrote {len(content)} characters to {target}")
        
        except Exception as e:
            return ToolResult(False, "", f"Error writing file: {str(e)}")
    
    @staticmethod
    async def append_file(
        filepath: Optional[str] = None,
        content: str = "",
        path: Optional[str] = None,
    ) -> ToolResult:
        """
        Append content to a file.

        Accepts either ``filepath`` or ``path`` as the target (the latter is an
        alias commonly emitted by the agent). At least one must be provided.

        Args:
            filepath: Path to the file to append to (preferred argument name).
            content: Content to append.
            path: Alias for ``filepath``; used if ``filepath`` is not given.

        Returns:
            ToolResult with success status
        """
        # Accept ``path`` as an alias for ``filepath`` (commonly emitted by the agent).
        target = filepath if filepath is not None else path
        if not target:
            return ToolResult(
                False, "", "Missing required argument: provide 'filepath' or 'path'."
            )
        try:
            resolved = Path(target).expanduser().resolve()

            with open(resolved, 'a', encoding='utf-8') as f:
                f.write(content)

            return ToolResult(True, f"Successfully appended content to {target}")
        
        except Exception as e:
            return ToolResult(False, "", f"Error appending to file: {str(e)}")
    
    @staticmethod
    async def edit_file(
        filepath: Optional[str] = None,
        old_str: str = "",
        new_str: str = "",
        path: Optional[str] = None,
    ) -> ToolResult:
        """
        Edit a file by replacing old_str with new_str.

        Accepts either ``filepath`` or ``path`` as the target (the latter is an
        alias commonly emitted by the agent). At least one must be provided.

        Args:
            filepath: Path to the file to edit (preferred argument name).
            old_str: String to find and replace.
            new_str: Replacement string.
            path: Alias for ``filepath``; used if ``filepath`` is not given.

        Returns:
            ToolResult with success status
        """
        # Accept ``path`` as an alias for ``filepath`` (commonly emitted by the agent).
        target = filepath if filepath is not None else path
        if not target:
            return ToolResult(
                False, "", "Missing required argument: provide 'filepath' or 'path'."
            )
        try:
            resolved = Path(target).expanduser().resolve()

            if not resolved.exists():
                return ToolResult(False, "", f"File not found: {target}")

            with open(resolved, 'r', encoding='utf-8') as f:
                content = f.read()

            # Require EXACTLY ONE match. Replacing only the first occurrence
            # when ``old_str`` appears multiple times silently edits the wrong
            # place; zero matches is a no-op failure. Both are bugs an editor
            # tool must refuse, so verify uniqueness up front.
            occurrences = content.count(old_str)
            if occurrences == 0:
                return ToolResult(False, "", f"String to replace not found in file")
            if occurrences > 1:
                return ToolResult(
                    False,
                    "",
                    f"Text appears {occurrences} times; refusing ambiguous edit. "
                    "Provide more surrounding context so the match is unique.",
                )

            new_content = content.replace(old_str, new_str, 1)

            with open(resolved, 'w', encoding='utf-8') as f:
                f.write(new_content)

            return ToolResult(True, f"Successfully replaced text in {target}")
        
        except Exception as e:
            return ToolResult(False, "", f"Error editing file: {str(e)}")


class WebSearchTool:
    """Tool for performing internet searches."""
    
    def __init__(self, search_api_key: Optional[str] = None):
        """
        Initialize the web search tool.
        
        Args:
            search_api_key: API key for search service (optional, uses basic scraping if not provided)
        """
        self.search_api_key = search_api_key
    
    async def search(self, query: str, num_results: int = 5) -> ToolResult:
        """
        Perform a web search.
        
        Args:
            query: Search query string
            num_results: Number of results to return
        
        Returns:
            ToolResult with search results
        """
        try:
            if self.search_api_key:
                # Use a search API (e.g., Serper, Bing, etc.)
                return await self._api_search(query, num_results)
            else:
                # Basic search without API (limited functionality)
                return await self._basic_search(query, num_results)
        
        except Exception as e:
            return ToolResult(False, "", f"Search error: {str(e)}")
    
    async def _api_search(self, query: str, num_results: int) -> ToolResult:
        """Perform search using an API."""
        # Example using Serper API (you can configure your preferred search API)
        headers = {
            "X-API-KEY": self.search_api_key,
            "Content-Type": "application/json",
        }
        
        payload = {
            "q": query,
            "num": num_results,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://google.serper.dev/search",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = []
                    
                    for organic in data.get("organic", [])[:num_results]:
                        results.append(f"Title: {organic.get('title', 'N/A')}")
                        results.append(f"URL: {organic.get('link', 'N/A')}")
                        results.append(f"Snippet: {organic.get('snippet', 'N/A')}")
                        results.append("")
                    
                    return ToolResult(True, "\n".join(results))
                else:
                    return ToolResult(False, "", f"Search API returned status {response.status}")
    
    async def _basic_search(self, query: str, num_results: int) -> ToolResult:
        """Basic search without API - returns a message about configuration."""
        return ToolResult(
            True,
            f"Web search configured but no API key provided.\n"
            f"To enable full web search, set SEARCH_API_KEY in your .env file.\n"
            f"Query received: {query}\n"
            f"Supported APIs: Serper, Bing, etc."
        )
    
    async def fetch_url(self, url: str) -> ToolResult:
        """
        Fetch content from a URL.
        
        Args:
            url: URL to fetch
        
        Returns:
            ToolResult with page content
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; Clio-Agent-2/1.0)"
            }
            
            # FIX: use an explicit ClientTimeout (an int only works by accident),
            # and cap the download with ``read()`` so a huge page can never OOM
            # the process. We truncate *before* the full body is in memory.
            read_cap = 5000 + 4096
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    # Read at most ``read_cap`` bytes, then decode lazily.
                    raw = await response.content.read(read_cap)
                    text_content = raw.decode("utf-8", errors="replace")
                    if len(raw) >= read_cap:
                        text_content = text_content[:5000] + (
                            "\n... (truncated at 5000 chars)"
                        )

                    return ToolResult(True, f"Content from {url}:\n\n{text_content}")
        
        except Exception as e:
            return ToolResult(False, "", f"Error fetching URL: {str(e)}")


class FileSearchTool:
    """Tool for searching through local directories and files."""
    
    @staticmethod
    async def search_files(
        directory: str,
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = 50
    ) -> ToolResult:
        """
        Search for files matching a pattern.
        
        Args:
            directory: Directory to search in
            pattern: Glob pattern to match (e.g., "*.py", "*.txt")
            recursive: Whether to search recursively
            max_results: Maximum number of results to return
        
        Returns:
            ToolResult with list of matching files
        """
        try:
            path = Path(directory).expanduser().resolve()
            
            if not path.exists():
                return ToolResult(False, "", f"Directory not found: {directory}")
            
            if not path.is_dir():
                return ToolResult(False, "", f"Not a directory: {directory}")
            
            results = []
            
            if recursive:
                files = path.rglob(pattern)
            else:
                files = path.glob(pattern)
            
            for i, file_path in enumerate(files):
                if i >= max_results:
                    results.append(f"... ({max_results} results shown)")
                    break
                
                rel_path = file_path.relative_to(path)
                results.append(str(rel_path))
            
            if not results:
                return ToolResult(True, f"No files matching '{pattern}' found in {directory}")
            
            return ToolResult(True, f"Found {len(results)} files:\n" + "\n".join(results))
        
        except Exception as e:
            return ToolResult(False, "", f"Error searching files: {str(e)}")
    
    @staticmethod
    async def search_content(
        directory: str,
        search_term: str,
        file_pattern: str = "*",
        case_sensitive: bool = False,
        max_results: int = 20
    ) -> ToolResult:
        """
        Search for content within files.
        
        Args:
            directory: Directory to search in
            search_term: Text to search for
            file_pattern: Glob pattern for files to search
            case_sensitive: Whether search is case-sensitive
            max_results: Maximum number of results to return
        
        Returns:
            ToolResult with matching lines
        """
        try:
            path = Path(directory).expanduser().resolve()
            
            if not path.exists():
                return ToolResult(False, "", f"Directory not found: {directory}")
            
            results = []
            files_searched = 0
            
            for file_path in path.rglob(file_pattern):
                if file_path.is_file() and len(results) < max_results:
                    files_searched += 1
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line_num, line in enumerate(f, 1):
                                search_line = line if case_sensitive else line.lower()
                                search_query = search_term if case_sensitive else search_term.lower()
                                
                                if search_query in search_line:
                                    rel_path = file_path.relative_to(path)
                                    results.append(
                                        f"{rel_path}:{line_num}: {line.strip()[:100]}"
                                    )
                                    
                                    if len(results) >= max_results:
                                        break
                    except (PermissionError, UnicodeDecodeError):
                        continue
            
            if not results:
                return ToolResult(
                    True,
                    f"No content matching '{search_term}' found in {files_searched} files"
                )
            
            return ToolResult(
                True,
                f"Found {len(results)} matches for '{search_term}' in {files_searched} files:\n"
                + "\n".join(results)
            )
        
        except Exception as e:
            return ToolResult(False, "", f"Error searching content: {str(e)}")
    
    @staticmethod
    async def list_directory(
        directory: Optional[str] = None,
        detailed: bool = False,
        path: Optional[str] = None,
    ) -> ToolResult:
        """
        List contents of a directory.

        Accepts either ``directory`` or ``path`` as the target (the latter is an
        alias commonly emitted by the agent). At least one must be provided.

        Args:
            directory: Directory to list (preferred argument name).
            detailed: Whether to show detailed size/type information.
            path: Alias for ``directory``; used if ``directory`` is not given.

        Returns:
            ToolResult with the directory listing, an explicit entry count,
            and surfaced access/OS errors when enumeration fails.
        """
        # Bug 1 fix: accept both ``directory`` and ``path`` as the target.
        target = directory if directory is not None else path
        if not target:
            return ToolResult(
                False,
                "",
                "Missing required argument: provide 'directory' or 'path'.",
            )

        try:
            resolved = Path(target).expanduser().resolve()
        except (OSError, RuntimeError) as e:
            return ToolResult(False, "", f"Invalid path '{target}': {str(e)}")

        if not resolved.exists():
            return ToolResult(False, "", f"Directory not found: {target}")

        if not resolved.is_dir():
            return ToolResult(False, "", f"Not a directory: {target}")

        results: List[str] = []

        # Bug 2 fix: explicitly enumerate via os.scandir and surface any
        # PermissionError / OSError in the output so the agent knows access
        # was denied rather than silently receiving an empty listing.
        try:
            with os.scandir(resolved) as scanner:
                entries = sorted(scanner, key=lambda e: e.name)
                for entry in entries:
                    try:
                        if detailed:
                            stats = entry.stat()
                            size = stats.st_size
                            item_type = "DIR" if entry.is_dir() else "FILE"
                            results.append(f"{item_type} {size:>10} {entry.name}")
                        else:
                            prefix = "[DIR] " if entry.is_dir() else ""
                            results.append(f"{prefix}{entry.name}")
                    except (PermissionError, OSError) as e:
                        # A single inaccessible entry should not abort the
                        # whole listing; surface it inline for the agent.
                        results.append(
                            f"[ERR] {entry.name}: "
                            f"{type(e).__name__}: {str(e)}"
                        )
        except (PermissionError, OSError) as e:
            return ToolResult(
                False,
                f"Contents of {target}:\n"
                f"[ACCESS DENIED] Could not enumerate directory "
                f"({type(e).__name__}): {str(e)}",
                f"Permission/OS error listing directory: {str(e)}",
            )

        count = len(results)
        if count == 0:
            output = f"Contents of {target}:\n0 entries"
        else:
            noun = "entry" if count == 1 else "entries"
            output = (
                f"Contents of {target} ({count} {noun}):\n"
                + "\n".join(results)
            )

        return ToolResult(True, output, None)


class ShellCommandTool:
    """Tool for executing shell commands on the local machine."""

    DEFAULT_TIMEOUT = 30
    DEFAULT_MAX_OUTPUT_CHARS = 12000
    # When a command times out it is almost always transient (the machine was
    # briefly busy), so instead of giving up we run the *exact same* command
    # again. ``MAX_COMMAND_ATTEMPTS`` is the total number of attempts (first
    # try + retries) before we finally report a timeout failure.
    MAX_COMMAND_ATTEMPTS = 5

    @staticmethod
    async def run_command(
        command: Optional[str] = None,
        cmd: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ToolResult:
        """
        Execute a shell command and return stdout/stderr plus the exit code.

        Accepts either ``command`` or ``cmd`` as the command text. ``cwd`` may
        be supplied to run inside a specific working directory.
        """
        command_text = command if command is not None else cmd
        if not command_text or not command_text.strip():
            return ToolResult(
                False,
                "",
                "Missing required argument: provide 'command' or 'cmd'.",
            )

        try:
            timeout = max(1, int(timeout))
            max_output_chars = max(1000, int(max_output_chars))
        except (TypeError, ValueError):
            return ToolResult(False, "", "timeout and max_output_chars must be integers.")

        working_dir = None
        if cwd:
            try:
                resolved_cwd = Path(cwd).expanduser().resolve()
            except (OSError, RuntimeError) as e:
                return ToolResult(False, "", f"Invalid cwd '{cwd}': {str(e)}")

            if not resolved_cwd.exists():
                return ToolResult(False, "", f"cwd does not exist: {cwd}")
            if not resolved_cwd.is_dir():
                return ToolResult(False, "", f"cwd is not a directory: {cwd}")
            working_dir = str(resolved_cwd)

        # Retry policy: when a command times out it is almost always transient
        # (the machine was briefly busy), so instead of giving up we kill the
        # stuck process and run the *exact same* command again -- up to
        # ``MAX_COMMAND_ATTEMPTS`` total attempts. Any other execution error
        # (e.g. the command itself crashed) is a real, non-transient failure
        # and is reported back immediately without retrying.
        # NOTE: ``run_command`` is a ``@staticmethod`` so we reference the class
        # directly rather than ``self``.
        for attempt in range(1, ShellCommandTool.MAX_COMMAND_ATTEMPTS + 1):
            try:
                process = await asyncio.create_subprocess_shell(
                    command_text,
                    cwd=working_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    # Kill the stuck process and retry the same command.
                    process.kill()
                    await process.communicate()
                    if attempt >= ShellCommandTool.MAX_COMMAND_ATTEMPTS:
                        return ToolResult(
                            False,
                            "",
                            f"Command timed out after {timeout} seconds "
                            f"(retried {attempt} times): {command_text}",
                        )
                    logger.warning(
                        "Shell command timed out (attempt %d/%d), retrying: %s",
                        attempt, ShellCommandTool.MAX_COMMAND_ATTEMPTS, command_text,
                    )
                    continue

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")

                output_parts = [f"Exit code: {process.returncode}"]
                if stdout:
                    output_parts.append(f"STDOUT:\n{stdout.rstrip()}")
                if stderr:
                    output_parts.append(f"STDERR:\n{stderr.rstrip()}")
                if len(output_parts) == 1:
                    output_parts.append("(no output)")

                output = "\n\n".join(output_parts)
                truncated = False
                if len(output) > max_output_chars:
                    output = output[:max_output_chars]
                    truncated = True

                if truncated:
                    output += f"\n... (output truncated to {max_output_chars} characters)"

                success = process.returncode == 0
                error = None if success else f"Command exited with code {process.returncode}"
                return ToolResult(success, output, error)

            except Exception as e:
                # Non-timeout execution failure: report immediately (no retry).
                return ToolResult(False, "", f"Error executing shell command: {str(e)}")

        # Unreachable in practice; guards against any logic slip above.
        return ToolResult(
            False, "", f"Command failed after {ShellCommandTool.MAX_COMMAND_ATTEMPTS} attempts: {command_text}"
        )


class ThinkingTool:
    """Tool for internal monologue and reasoning."""
    
    def __init__(self, context_log):
        """
        Initialize the thinking tool.
        
        Args:
            context_log: ContextLog instance to record thoughts
        """
        self.context_log = context_log
    
    async def think(
        self,
        thought: Optional[str] = None,
        *,
        text: Optional[str] = None,
        content: Optional[str] = None,
        context: Optional[str] = None,
        note: Optional[str] = None,
        message: Optional[str] = None,
    ) -> ToolResult:
        """
        Record an internal thought/monologue.

        The reasoning text is accepted via the canonical keyword ``thought``
        OR any of the common aliases the agent/LLM may emit
        (``text``, ``content``, ``context``, ``note``, ``message``). This keeps
        the tool tolerant of variations so it never fails with an
        "unexpected keyword argument" error.

        When calling the registered "thinking" tool, prefer::

            {"thought": "your reasoning here"}

        but the following are all accepted as equivalents::

            {"content": "..."}   {"context": "..."}   {"text": "..."}
            {"note": "..."}      {"message": "..."}

        Args:
            thought: Canonical reasoning text (preferred).
            text: Alias for ``thought``.
            content: Alias for ``thought``.
            context: Alias for ``thought``.
            note: Alias for ``thought``.
            message: Alias for ``thought``.

        Returns:
            ToolResult confirming the thought was recorded, or an error
            result if no reasoning text was supplied.
        """
        reasoning = thought or text or content or context or note or message
        if not reasoning:
            return ToolResult(
                False,
                "",
                "Missing reasoning text: provide 'thought' "
                "(or content/context/text/note/message).",
            )
        await self.context_log.add_thinking(reasoning)
        return ToolResult(True, f"Thought recorded: {reasoning[:100]}...")


class SayTool:
    """The agent's explicit 'Say' command: the ONLY way to address the user.

    ``say`` is a *normal, executable tool* -- it is registered alongside
    ``read_file``, ``shell_command``, ``thinking``, etc. and runs through the
    same tool-execution loop as any other tool. When the agent runs it, it
    delivers its ``message`` to the user through the response channel
    (``send_response`` -> registered callbacks). Plain natural-language text the
    model writes is NOT shown to the user (the auto-reply behaviour has been
    removed), so the agent must emit the ``say`` tool whenever it wants to
    communicate.

    Accepts the message via the canonical ``message`` keyword or any of the
    common aliases (``text``, ``content``, ``say``) so the tool tolerates
    variations the model may emit.
    """

    def __init__(self, context_log=None, response_sink=None):
        """Wire the tool to the agent's context log and user-facing sink.

        Args:
            context_log: Optional ContextLog (the say is recorded as an
                assistant response when available).
            response_sink: Optional async callable ``(message: str) -> None``
                that delivers the message to the user (typically
                ``ClioAgent.send_response``).
        """
        self.context_log = context_log
        self.response_sink = response_sink

    async def say(
        self,
        message: Optional[str] = None,
        *,
        text: Optional[str] = None,
        content: Optional[str] = None,
        say: Optional[str] = None,
    ) -> ToolResult:
        """Deliver the user-facing message and return a ToolResult.

        Accepts the message via the canonical ``message`` keyword or any of the
        common aliases (``text``, ``content``, ``say``). When a message is
        present it is (best-effort) delivered through ``response_sink`` and
        recorded in the context log, then returned in a success ToolResult.
        """
        value = message or text or content or say
        if not value:
            return ToolResult(
                False,
                "",
                "Missing message: provide 'message' (or text/content/say).",
            )
        text_value = str(value).strip()
        if self.response_sink is not None:
            try:
                await self.response_sink(text_value)
            except Exception:
                # A failing delivery callback must never break the turn.
                pass
        if self.context_log is not None:
            try:
                await self.context_log.add_assistant_response(text_value)
            except Exception:
                pass
        return ToolResult(True, text_value)





class ToolRegistry:
    """Registry for managing available tools."""
    
    def __init__(self, context_log=None, search_api_key=None, response_sink=None):
        """
        Initialize the tool registry.

        Dependency injection: the registry receives only the concrete values it
        needs (e.g. ``search_api_key``) instead of reaching into a global
        ``config`` object. This decouples tools from the configuration layer and
        makes the registry trivially testable.

        Args:
            context_log: ContextLog instance for recording operations
            search_api_key: API key for the web-search tool (optional)
            response_sink: Optional async callable ``(message: str) -> None``
                that delivers a user-facing message (typically the agent's
                ``send_response``). Wired into the ``say`` tool so it behaves
                like any other executable tool yet still reaches the user.
        """
        self.context_log = context_log
        self.search_api_key = search_api_key
        self.response_sink = response_sink
        self.tools: Dict[str, callable] = {}

        # Register default tools
        self._register_default_tools()

    def _register_default_tools(self):
        """Register the default set of tools."""
        # File editing tools
        self.register_tool("read_file", FileEditTool.read_file)
        self.register_tool("write_file", FileEditTool.write_file)
        self.register_tool("append_file", FileEditTool.append_file)
        self.register_tool("edit_file", FileEditTool.edit_file)

        # Web search tools
        search_tool = WebSearchTool(self.search_api_key)
        self.register_tool("web_search", search_tool.search)
        self.register_tool("fetch_url", search_tool.fetch_url)
        
        # File search tools
        self.register_tool("search_files", FileSearchTool.search_files)
        self.register_tool("search_content", FileSearchTool.search_content)
        self.register_tool("list_directory", FileSearchTool.list_directory)

        # Shell command tools
        self.register_tool("shell_command", ShellCommandTool.run_command)
        self.register_tool("run_shell_command", ShellCommandTool.run_command)
        self.register_tool("execute_shell_command", ShellCommandTool.run_command)
        
        # Thinking tool
        if self.context_log:
            thinking_tool = ThinkingTool(self.context_log)
            self.register_tool("thinking", thinking_tool.think)

        # Say command - the agent's explicit way to address the user. It is now a
        # normal, executable tool (registered like read_file / shell_command) but
        # its execution delivers the message to the user through the response
        # sink. This makes it behave exactly like the other tools.
        say_tool = SayTool(self.context_log, self.response_sink)
        self.register_tool("say", say_tool.say)
    
    def register_tool(self, name: str, func: callable):
        """
        Register a new tool.
        
        Args:
            name: Tool name
            func: Tool function (async)
        """
        self.tools[name] = func
    
    def get_tool(self, name: str) -> Optional[callable]:
        """Get a tool by name."""
        return self.tools.get(name)
    
    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self.tools.keys())
    
    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> ToolResult:
        """
        Execute a tool with given arguments.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Dictionary of arguments for the tool
        
        Returns:
            ToolResult from the tool execution
        """
        tool = self.get_tool(tool_name)
        
        if not tool:
            return ToolResult(False, "", f"Unknown tool: {tool_name}")
        
        try:
            # Execute the tool
            result = await tool(**arguments)
            
            # Log the execution if context_log is available
            if self.context_log:
                await self.context_log.add_tool_execution(
                    tool_name,
                    arguments,
                    result.output if result.success else result.error
                )
            
            return result
        
        except Exception as e:
            error_msg = f"Tool execution error: {str(e)}"
            if self.context_log:
                await self.context_log.add_tool_execution(tool_name, arguments, error_msg)
            return ToolResult(False, "", error_msg)
