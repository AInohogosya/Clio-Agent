"""
Prompt Cache System for VEXIS-CLI
Caches LLM responses to reduce API calls and improve response times
"""

import hashlib
import time
import json
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock

from .logger import get_logger


@dataclass
class CacheEntry:
    """Cache entry with metadata"""
    prompt_hash: str
    response: str
    model: str
    provider: str
    task_type: str
    timestamp: float
    ttl: int  # Time to live in seconds
    access_count: int = 0
    last_accessed: float = 0.0
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        return time.time() - self.timestamp > self.ttl
    
    def touch(self):
        """Update access metadata"""
        self.access_count += 1
        self.last_accessed = time.time()


@dataclass
class CacheStats:
    """Cache statistics"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_entries: int = 0
    total_size_bytes: int = 0
    hit_rate: float = 0.0


class PromptCache:
    """
    LRU Cache for LLM prompt responses
    
    Features:
    - In-memory caching with optional disk persistence
    - TTL-based expiration
    - LRU eviction policy
    - Cache statistics tracking
    - Hash-based key generation
    """
    
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: int = 3600,  # 1 hour
        persist_to_disk: bool = True,
        cache_dir: Optional[str] = None
    ):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.persist_to_disk = persist_to_disk
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".vexis" / "cache"
        
        self.logger = get_logger("prompt_cache")
        self.cache: Dict[str, CacheEntry] = {}
        self.lock = Lock()
        self.stats = CacheStats()
        
        # Create cache directory if needed
        if self.persist_to_disk:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()
        
        self.logger.info(
            "Prompt cache initialized",
            max_size=max_size,
            default_ttl=default_ttl,
            persist_to_disk=persist_to_disk
        )
    
    def _generate_key(
        self,
        prompt: str,
        model: str,
        provider: str,
        task_type: str,
        temperature: float = 1.0,
        max_tokens: int = 5000
    ) -> str:
        """
        Generate cache key from prompt parameters
        
        Includes all parameters that would affect the response
        """
        # Create normalized content string
        content = json.dumps({
            "prompt": prompt,
            "model": model,
            "provider": provider,
            "task_type": task_type,
            "temperature": round(temperature, 2),  # Round to reduce noise
            "max_tokens": max_tokens
        }, sort_keys=True)
        
        # Generate SHA-256 hash
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def get(
        self,
        prompt: str,
        model: str,
        provider: str,
        task_type: str,
        temperature: float = 1.0,
        max_tokens: int = 5000
    ) -> Optional[str]:
        """
        Get cached response if available and not expired
        
        Returns:
            Cached response or None if not found/expired
        """
        key = self._generate_key(prompt, model, provider, task_type, temperature, max_tokens)
        
        with self.lock:
            entry = self.cache.get(key)
            
            if entry is None:
                self.stats.misses += 1
                self._update_hit_rate()
                return None
            
            if entry.is_expired():
                self.logger.debug(f"Cache entry expired for key {key[:16]}...")
                del self.cache[key]
                self.stats.evictions += 1
                self.stats.misses += 1
                self._update_hit_rate()
                return None
            
            # Cache hit
            entry.touch()
            self.stats.hits += 1
            self._update_hit_rate()
            
            self.logger.debug(
                f"Cache hit for key {key[:16]}...",
                access_count=entry.access_count,
                age_seconds=time.time() - entry.timestamp
            )
            
            return entry.response
    
    def put(
        self,
        prompt: str,
        response: str,
        model: str,
        provider: str,
        task_type: str,
        temperature: float = 1.0,
        max_tokens: int = 5000,
        ttl: Optional[int] = None
    ):
        """
        Store response in cache
        """
        key = self._generate_key(prompt, model, provider, task_type, temperature, max_tokens)
        
        with self.lock:
            # Check if we need to evict entries
            if len(self.cache) >= self.max_size:
                self._evict_lru()
            
            # Create new entry
            entry = CacheEntry(
                prompt_hash=key,
                response=response,
                model=model,
                provider=provider,
                task_type=task_type,
                timestamp=time.time(),
                ttl=ttl or self.default_ttl,
                last_accessed=time.time()
            )
            
            self.cache[key] = entry
            self.stats.total_entries = len(self.cache)
            
            self.logger.debug(
                f"Cached response for key {key[:16]}...",
                ttl=ttl or self.default_ttl,
                cache_size=len(self.cache)
            )
            
            # Persist to disk if enabled
            if self.persist_to_disk:
                self._save_to_disk()
    
    def _evict_lru(self):
        """Evict least recently used entry"""
        if not self.cache:
            return
        
        # Find LRU entry (least recently accessed)
        lru_key = min(
            self.cache.keys(),
            key=lambda k: self.cache[k].last_accessed
        )
        
        del self.cache[lru_key]
        self.stats.evictions += 1
        
        self.logger.debug(f"Evicted LRU entry {lru_key[:16]}...")
    
    def _update_hit_rate(self):
        """Update cache hit rate statistic"""
        total = self.stats.hits + self.stats.misses
        if total > 0:
            self.stats.hit_rate = self.stats.hits / total
    
    def invalidate(
        self,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        older_than: Optional[float] = None
    ):
        """
        Invalidate cache entries matching criteria
        
        Args:
            model: Only invalidate entries for this model
            provider: Only invalidate entries for this provider
            older_than: Only invalidate entries older than this timestamp
        """
        with self.lock:
            to_remove = []
            
            for key, entry in self.cache.items():
                match = True
                
                if model and entry.model != model:
                    match = False
                
                if provider and entry.provider != provider:
                    match = False
                
                if older_than and entry.timestamp > older_than:
                    match = False
                
                if match:
                    to_remove.append(key)
            
            for key in to_remove:
                del self.cache[key]
                self.stats.evictions += 1
            
            self.stats.total_entries = len(self.cache)
            
            self.logger.info(
                f"Invalidated {len(to_remove)} cache entries",
                model=model,
                provider=provider
            )
            
            if self.persist_to_disk:
                self._save_to_disk()
    
    def clear(self):
        """Clear all cache entries"""
        with self.lock:
            count = len(self.cache)
            self.cache.clear()
            self.stats.total_entries = 0
            
            self.logger.info(f"Cleared {count} cache entries")
            
            if self.persist_to_disk:
                self._save_to_disk()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            total_size = sum(
                len(entry.response.encode('utf-8'))
                for entry in self.cache.values()
            )
            
            return {
                "hits": self.stats.hits,
                "misses": self.stats.misses,
                "hit_rate": f"{self.stats.hit_rate:.2%}",
                "total_entries": self.stats.total_entries,
                "evictions": self.stats.evictions,
                "total_size_bytes": total_size,
                "max_size": self.max_size
            }
    
    def _save_to_disk(self):
        """Save cache to disk"""
        try:
            cache_file = self.cache_dir / "prompt_cache.json"
            
            # Convert cache to serializable format
            data = {
                key: {
                    "prompt_hash": entry.prompt_hash,
                    "response": entry.response,
                    "model": entry.model,
                    "provider": entry.provider,
                    "task_type": entry.task_type,
                    "timestamp": entry.timestamp,
                    "ttl": entry.ttl,
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed
                }
                for key, entry in self.cache.items()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            self.logger.debug(f"Saved {len(data)} entries to disk")
            
        except Exception as e:
            self.logger.error(f"Failed to save cache to disk: {e}")
    
    def _load_from_disk(self):
        """Load cache from disk"""
        try:
            cache_file = self.cache_dir / "prompt_cache.json"
            
            if not cache_file.exists():
                return
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            loaded = 0
            expired = 0
            
            for key, entry_data in data.items():
                entry = CacheEntry(
                    prompt_hash=entry_data["prompt_hash"],
                    response=entry_data["response"],
                    model=entry_data["model"],
                    provider=entry_data["provider"],
                    task_type=entry_data["task_type"],
                    timestamp=entry_data["timestamp"],
                    ttl=entry_data["ttl"],
                    access_count=entry_data.get("access_count", 0),
                    last_accessed=entry_data.get("last_accessed", 0.0)
                )
                
                # Skip expired entries
                if entry.is_expired():
                    expired += 1
                    continue
                
                self.cache[key] = entry
                loaded += 1
            
            self.stats.total_entries = len(self.cache)
            
            self.logger.info(
                f"Loaded cache from disk",
                loaded=loaded,
                expired_skipped=expired
            )
            
        except Exception as e:
            self.logger.error(f"Failed to load cache from disk: {e}")


# Global instance
_global_cache: Optional[PromptCache] = None


def get_prompt_cache(
    max_size: int = 1000,
    default_ttl: int = 3600,
    persist_to_disk: bool = True
) -> PromptCache:
    """Get global prompt cache instance"""
    global _global_cache
    
    if _global_cache is None:
        _global_cache = PromptCache(
            max_size=max_size,
            default_ttl=default_ttl,
            persist_to_disk=persist_to_disk
        )
    
    return _global_cache


def invalidate_cache_for_provider(provider: str):
    """Invalidate all cache entries for a specific provider"""
    cache = get_prompt_cache()
    cache.invalidate(provider=provider)


def invalidate_cache_for_model(model: str):
    """Invalidate all cache entries for a specific model"""
    cache = get_prompt_cache()
    cache.invalidate(model=model)


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    cache = get_prompt_cache()
    return cache.get_stats()
