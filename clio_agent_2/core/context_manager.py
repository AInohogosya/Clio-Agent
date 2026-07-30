import asyncio
import json
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path

from .token_budget import estimate_tokens

_EMPTY = {}


class ContextEntry:

    __slots__ = ('entry_type', 'content', 'timestamp', 'metadata')

    def __init__(self, entry_type, content, metadata=None):
        self.entry_type = entry_type
        self.content = content
        self.timestamp = datetime.now().isoformat()
        self.metadata = metadata if metadata is not None else _EMPTY

    def to_dict(self):
        md = self.metadata
        return {
            "type": self.entry_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": md if md is not _EMPTY else {},
        }

    def __str__(self):
        return f"[{self.entry_type.upper()}] {self.content}"

    def to_log_line(self):
        return f"{self.timestamp} | {self.entry_type}: {self.content}"


class ContextLog:

    def __init__(
        self,
        max_lines=1000,
        window_size=50,
        cold_batch=25,
        compression_callback=None,
        persist_path=None,
        archive_path=None,
        encoding_model="gpt-4",
    ):
        self.max_lines = max_lines
        self.window_size = window_size
        self.cold_batch = cold_batch
        self.compression_callback = compression_callback
        self.encoding_model = encoding_model
        self._entry_count = 0

        self.working_window = deque(maxlen=window_size)
        self.working_summary = ""
        self._cold_pending = []

        self.is_compressing = False
        self._dirty = False
        self._lock = asyncio.Lock()
        self.persist_path = Path(persist_path) if persist_path else None
        self.archive_path = Path(archive_path) if archive_path else None

    async def add_entry(self, entry_type, content, metadata=None):
        async with self._lock:
            entry = ContextEntry(entry_type, content, metadata)
            self._entry_count += 1

            self.working_window.append(entry)
            while len(self.working_window) > self.window_size:
                self._cold_pending.append(self.working_window.popleft())

            self._dirty = True
            if self.persist_path and self._entry_count % 10 == 0:
                await asyncio.to_thread(self._save_to_file)

        if (
            self._cold_pending
            and len(self._cold_pending) >= self.cold_batch
            and not self.is_compressing
            and self.compression_callback
        ):
            await self._compress_cold()

    async def add_user_message(self, message):
        await self.add_entry("user_message", f"User Message: {message}")

    async def add_tool_execution(self, tool_name, arguments, result):
        await self.add_entry("tool_execution", f"Tool: {tool_name}\nArguments: {arguments}\nResult: {result}", {"tool_name": tool_name})

    async def add_thinking(self, thought):
        await self.add_entry("thinking", f"Thinking: {thought}")

    async def add_system_message(self, message):
        await self.add_entry("system", message)

    async def add_assistant_response(self, response):
        await self.add_entry("assistant_response", response)

    async def _compress_cold(self):
        self.is_compressing = True
        try:
            batch = self._cold_pending
            self._cold_pending = []
            if self.compression_callback and batch:
                try:
                    summary = await self.compression_callback(batch)
                except Exception as exc:
                    summary = f"(summary unavailable: {exc})"
                if summary:
                    self.working_summary = f"{self.working_summary}\n{summary}".strip() if self.working_summary else summary
            if self.archive_path and batch:
                await asyncio.to_thread(self._append_archive, batch)
            self._dirty = True
            await asyncio.to_thread(self._save_to_file)
        finally:
            self.is_compressing = False

    def get_entries(self):
        return list(self.working_window)

    def get_entries_as_messages(self, max_tokens=None):
        messages = []
        for entry in self.working_window:
            role, content = self._role_for(entry)
            if role is None:
                continue
            messages.append({"role": role, "content": content})
        if max_tokens:
            total = sum(estimate_tokens(m["content"], self.encoding_model) for m in messages)
            while len(messages) > 1 and total > max_tokens:
                dropped = messages.pop(0)
                total -= estimate_tokens(dropped["content"], self.encoding_model)
        return messages

    def get_line_count(self):
        return self._entry_count

    def get_recent_entries(self, count=10):
        return list(self.working_window)[-count:]

    @staticmethod
    def _role_for(entry):
        t = entry.entry_type
        if t == "user_message":
            return "user", entry.content
        if t == "assistant_response":
            return "assistant", entry.content
        if t == "thinking":
            return "user", f"Thinking: {entry.content}"
        if t == "tool_execution":
            return "user", f"Tool execution: {entry.content}"
        return None, None

    def clear(self):
        try:
            self._backup_to_trash()
        except Exception:
            pass
        self.working_window.clear()
        self._cold_pending.clear()
        self.working_summary = ""
        self._entry_count = 0
        self._dirty = False
        if self.archive_path:
            try:
                self.archive_path.parent.mkdir(parents=True, exist_ok=True)
                self.archive_path.write_text("", encoding="utf-8")
            except Exception:
                pass
        self._save_to_file()

    def export_to_file(self, filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            for entry in self.working_window:
                f.write(entry.to_log_line() + "\n")

    def _save_to_file(self):
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            if self.persist_path.exists():
                try:
                    shutil.copyfile(self.persist_path, self.persist_path.with_suffix(self.persist_path.suffix + ".bak"))
                except Exception:
                    pass
            data = {
                "entries": [entry.to_dict() for entry in self.working_window],
                "working_summary": self.working_summary,
                "max_lines": self.max_lines,
                "window_size": self.window_size,
                "entry_count": self._entry_count,
                "last_updated": datetime.now().isoformat(),
            }
            tmp = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp.replace(self.persist_path)
        except Exception as e:
            print(f"Error saving context to file: {e}")

    def _append_archive(self, batch):
        if not self.archive_path:
            return
        try:
            self.archive_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.archive_path, 'a', encoding='utf-8') as f:
                for entry in batch:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Error archiving context: {e}")

    def load_from_file(self):
        if not self.persist_path:
            return False
        primary = self.persist_path
        backup = self.persist_path.with_suffix(self.persist_path.suffix + ".bak")
        for path in (primary, backup):
            if not path.exists():
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            self._rebuild_from_data(data)
            self._dirty = False
            return True
        return False

    def _rebuild_from_data(self, data):
        self.working_window.clear()
        self._cold_pending.clear()
        self.working_summary = data.get("working_summary", "")
        self._entry_count = data.get("entry_count", len(data.get("entries", [])))
        for entry_data in data.get("entries", []):
            entry = ContextEntry(
                entry_type=entry_data.get("type", "unknown"),
                content=entry_data.get("content", ""),
                metadata=entry_data.get("metadata"),
            )
            self.working_window.append(entry)
        if "max_lines" in data:
            self.max_lines = data["max_lines"]
        if "window_size" in data:
            self.window_size = data["window_size"]

    def _backup_to_trash(self):
        if not self.persist_path:
            return
        trash = self.persist_path.with_name(f"{self.persist_path.stem}.trash{self.persist_path.suffix}")
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": [entry.to_dict() for entry in self.working_window],
            "working_summary": self.working_summary,
            "max_lines": self.max_lines,
            "window_size": self.window_size,
            "entry_count": self._entry_count,
            "last_updated": datetime.now().isoformat(),
        }
        with open(trash, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def restore_backup(self):
        if not self.persist_path:
            return False
        trash = self.persist_path.with_name(f"{self.persist_path.stem}.trash{self.persist_path.suffix}")
        if not trash.exists():
            return False
        try:
            with open(trash, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return False
        self._rebuild_from_data(data)
        self._dirty = False
        self._save_to_file()
        return True

    def save(self):
        self._save_to_file()
        self._dirty = False

    async def save_async(self):
        await asyncio.to_thread(self._save_to_file)
        self._dirty = False
