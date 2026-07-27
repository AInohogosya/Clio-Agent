"""
Conversation Context Manager

Maintains consistent context across chat and voice conversations.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A single message in the conversation."""
    role: str  # system, user, assistant, tool
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # For tool calls
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ConversationContext:
    """Manages conversation context with history."""
    
    def __init__(
        self,
        system_prompt: str = "",
        max_messages: int = 50,
        max_tokens: int = 8000
    ):
        self.system_prompt = system_prompt
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self._messages: deque = deque(maxlen=max_messages)
        self._metadata: Dict[str, Any] = {}
        
        if system_prompt:
            self._messages.append(Message(role="system", content=system_prompt))
    
    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        tool_call_id: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> Message:
        """Add a message to the context."""
        msg = Message(
            role=role,
            content=content,
            metadata=metadata or {},
            tool_call_id=tool_call_id,
            tool_calls=tool_calls
        )
        self._messages.append(msg)
        return msg
    
    def add_user_message(self, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Add a user message."""
        return self.add_message("user", content, metadata)
    
    def add_assistant_message(self, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Add an assistant message."""
        return self.add_message("assistant", content, metadata)
    
    def add_system_message(self, content: str):
        """Add a system message."""
        return self.add_message("system", content)
    
    def add_tool_result(self, tool_call_id: str, result: str):
        """Add a tool result."""
        return self.add_message("tool", result, tool_call_id=tool_call_id)
    
    def get_messages(self) -> List[Message]:
        """Get all messages."""
        return list(self._messages)
    
    def get_recent_messages(self, count: int) -> List[Message]:
        """Get recent messages."""
        return list(self._messages)[-count:]
    
    def get_messages_for_llm(self, include_system: bool = True) -> List[Dict[str, Any]]:
        """Get messages formatted for LLM."""
        messages = []
        for msg in self._messages:
            if msg.role == "system" and not include_system:
                continue
            
            m = {"role": msg.role, "content": msg.content}
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                m["tool_calls"] = msg.tool_calls
            if msg.name:
                m["name"] = msg.name
            messages.append(m)
        return messages
    
    def get_context_for_prompt(self, max_chars: int = 4000) -> str:
        """Get context as formatted string for prompt injection."""
        lines = []
        
        for msg in self._messages:
            if msg.role == "system":
                continue
            
            prefix = "User: " if msg.role == "user" else "Assistant: "
            content = msg.content[:200] + ("..." if len(msg.content) > 200 else "")
            lines.append(f"{prefix}{content}")
        
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:]
            text = "..." + text
        
        return text
    
    def get_context_summary(self) -> Dict[str, Any]:
        """Get context summary for display."""
        user_count = sum(1 for m in self._messages if m.role == "user")
        assistant_count = sum(1 for m in self._messages if m.role == "assistant")
        
        return {
            "total_messages": len(self._messages),
            "user_messages": user_count,
            "assistant_messages": assistant_count,
            "has_system_prompt": bool(self.system_prompt),
            "metadata": self._metadata
        }
    
    def set_metadata(self, key: str, value: Any):
        """Set metadata."""
        self._metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata."""
        return self._metadata.get(key, default)
    
    def clear(self):
        """Clear conversation history but keep system prompt."""
        system_msgs = [m for m in self._messages if m.role == "system"]
        self._messages.clear()
        for msg in system_msgs:
            self._messages.append(msg)
    
    def clear_all(self):
        """Clear everything including system prompt."""
        self._messages.clear()
        self._metadata.clear()
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        data = {
            "system_prompt": self.system_prompt,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "metadata": m.metadata,
                    "tool_call_id": m.tool_call_id,
                    "tool_calls": m.tool_calls
                }
                for m in self._messages
            ],
            "metadata": self._metadata
        }
        return json.dumps(data, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> "ConversationContext":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        ctx = cls(system_prompt=data.get("system_prompt", ""))
        ctx._metadata = data.get("metadata", {})
        
        for m in data.get("messages", []):
            msg = Message(
                role=m["role"],
                content=m["content"],
                timestamp=m.get("timestamp", time.time()),
                metadata=m.get("metadata", {}),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls")
            )
            ctx._messages.append(msg)
        
        return ctx
    
    def save(self, path: Path):
        """Save context to file."""
        path.write_text(self.to_json(), encoding="utf-8")
    
    @classmethod
    def load(cls, path: Path) -> "ConversationContext":
        """Load context from file."""
        return cls.from_json(path.read_text(encoding="utf-8"))


class ContextManager:
    """Manages multiple conversation contexts."""
    
    def __init__(self, storage_dir: Path = None):
        self.storage_dir = storage_dir or Path("~/.clio_agent/contexts").expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._contexts: Dict[str, ConversationContext] = {}
        self._active_context: Optional[str] = None
    
    def create_context(
        self,
        name: str,
        system_prompt: str = "",
        max_messages: int = 50
    ) -> ConversationContext:
        """Create a new context."""
        ctx = ConversationContext(system_prompt, max_messages)
        self._contexts[name] = ctx
        return ctx
    
    def get_context(self, name: str) -> Optional[ConversationContext]:
        """Get a context by name."""
        return self._contexts.get(name)
    
    def set_active(self, name: str) -> bool:
        """Set active context."""
        if name in self._contexts:
            self._active_context = name
            return True
        return False
    
    def get_active(self) -> Optional[ConversationContext]:
        """Get active context."""
        if self._active_context:
            return self._contexts.get(self._active_context)
        return None
    
    def list_contexts(self) -> List[str]:
        """List all context names."""
        return list(self._contexts.keys())
    
    def delete_context(self, name: str) -> bool:
        """Delete a context."""
        if name in self._contexts:
            del self._contexts[name]
            if self._active_context == name:
                self._active_context = None
            return True
        return False
    
    def save_context(self, name: str) -> bool:
        """Save context to disk."""
        ctx = self._contexts.get(name)
        if not ctx:
            return False
        
        path = self.storage_dir / f"{name}.json"
        try:
            ctx.save(path)
            return True
        except Exception as e:
            logger.error(f"Failed to save context {name}: {e}")
            return False
    
    def load_context(self, name: str) -> Optional[ConversationContext]:
        """Load context from disk."""
        path = self.storage_dir / f"{name}.json"
        if not path.exists():
            return None
        
        try:
            ctx = ConversationContext.load(path)
            self._contexts[name] = ctx
            return ctx
        except Exception as e:
            logger.error(f"Failed to load context {name}: {e}")
            return None
    
    def load_all(self):
        """Load all saved contexts."""
        for path in self.storage_dir.glob("*.json"):
            name = path.stem
            self.load_context(name)


def create_context(system_prompt: str = "", max_messages: int = 50) -> ConversationContext:
    """Factory function to create context."""
    return ConversationContext(system_prompt, max_messages)


def create_context_manager(storage_dir: Path = None) -> ContextManager:
    """Factory function to create context manager."""
    return ContextManager(storage_dir)