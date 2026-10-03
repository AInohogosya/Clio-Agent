from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger

logger = get_logger("ethos.toolhost.watchers")


class DirectoryWatcher:
    """Polling directory watcher: emits fs events onto the bus as percepts."""

    def __init__(self, path: Path, callback: Callable[[dict[str, Any]], Any], interval_s: float = 5.0):
        self.path = Path(path)
        self.callback = callback
        self.interval_s = interval_s
        self._state: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def _scan(self) -> dict[str, float]:
        state: dict[str, float] = {}
        try:
            for entry in self.path.rglob("*"):
                try:
                    if entry.is_file():
                        state[str(entry)] = entry.stat().st_mtime
                except OSError:
                    continue
        except OSError:
            return self._state
        return state

    async def _loop(self) -> None:
        while self._running:
            new_state = await asyncio.to_thread(self._scan)
            for path, mtime in new_state.items():
                if path not in self._state:
                    try:
                        result = self.callback({
                            "type": "fs.created", "path": path, "mtime": mtime,
                        })
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("watcher.callback_failed")
                elif self._state[path] != mtime:
                    try:
                        result = self.callback({
                            "type": "fs.modified", "path": path, "mtime": mtime,
                        })
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("watcher.callback_failed")
            for path in set(self._state) - set(new_state):
                try:
                    result = self.callback({"type": "fs.deleted", "path": path})
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("watcher.callback_failed")
            self._state = new_state
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._state = self._scan()
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None


class ProcessWatcher:
    """Long-running process: streams stdout lines to a callback."""

    def __init__(self, command: list[str], callback: Callable[[dict[str, Any]], Any], cwd: Path | None = None):
        self.command = command
        self.callback = callback
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self.started_at: float | None = None

    async def _pump(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        async for line in self.process.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            try:
                result = self.callback({"type": "proc.line", "line": text, "proc": self.command[0]})
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("process_watcher.callback_failed")
        rc = await self.process.wait() if self.process is not None else None
        try:
            result = self.callback({"type": "proc.exit", "returncode": rc, "proc": self.command[0]})
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("process_watcher.callback_failed")

    async def start(self) -> None:
        env = dict(os.environ)
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.cwd) if self.cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self.started_at = time.monotonic()
        self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class WatcherService:
    def __init__(self) -> None:
        self.dir_watchers: dict[str, DirectoryWatcher] = {}
        self.proc_watchers: dict[str, ProcessWatcher] = {}

    def watch_dir(self, key: str, path: Path, callback: Callable[[dict[str, Any]], Any],
                  interval_s: float = 5.0) -> None:
        if key in self.dir_watchers:
            return
        watcher = DirectoryWatcher(path, callback, interval_s)
        self.dir_watchers[key] = watcher
        watcher.start()

    def unwatch_dir(self, key: str) -> None:
        watcher = self.dir_watchers.pop(key, None)
        if watcher is not None:
            watcher.stop()

    async def watch_process(self, key: str, command: list[str],
                            callback: Callable[[dict[str, Any]], Any], cwd: Path | None = None) -> None:
        if key in self.proc_watchers:
            return
        watcher = ProcessWatcher(command, callback, cwd)
        self.proc_watchers[key] = watcher
        await watcher.start()

    async def stop_process(self, key: str) -> None:
        watcher = self.proc_watchers.pop(key, None)
        if watcher is not None:
            await watcher.stop()

    def stop_all(self) -> None:
        for watcher in self.dir_watchers.values():
            watcher.stop()
        self.dir_watchers.clear()
