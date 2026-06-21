"""
Provider Fallback Manager for Clio-Agent-1
Manages automatic failover between multiple AI providers for high availability
"""

import time
import random
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .exceptions import ErrorCategory, ErrorContext, ErrorHandler, APIError
from .logger import get_logger


class ProviderStatus(Enum):
    """Status of a provider"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Slow but working
    UNAVAILABLE = "unavailable"  # Temporarily down
    DISABLED = "disabled"  # Manually disabled


@dataclass
class ProviderHealth:
    """Health metrics for a provider"""
    provider: str
    status: ProviderStatus
    last_success: float = field(default_factory=time.time)
    last_failure: Optional[float] = None
    consecutive_failures: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    avg_latency: float = 0.0
    circuit_breaker_until: Optional[float] = None  # Circuit breaker timeout
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        if self.total_requests == 0:
            return 1.0
        return (self.total_requests - self.failed_requests) / self.total_requests
    
    @property
    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open"""
        if self.circuit_breaker_until is None:
            return False
        return time.time() < self.circuit_breaker_until


@dataclass
class FallbackConfig:
    """Configuration for fallback behavior"""
    # Circuit breaker settings
    circuit_breaker_threshold: int = 5  # Failures before opening circuit
    circuit_breaker_timeout: float = 60.0  # Seconds before trying again
    
    # Health check settings
    health_check_interval: float = 30.0  # Seconds between health checks
    slow_threshold: float = 10.0  # Latency threshold for degraded status
    
    # Retry settings
    max_retries_per_provider: int = 2
    retry_delay_base: float = 1.0
    
    # Fallback order (priority list)
    fallback_order: List[str] = field(default_factory=lambda: [
        "openrouter", "google", "openai", "anthropic", "ollama"
    ])
    
    # Cost optimization
    prefer_cheaper_providers: bool = False
    max_cost_per_request: Optional[float] = None


class ProviderFallbackManager:
    """
    Manages provider fallback for high availability
    
    Features:
    - Circuit breaker pattern for failing providers
    - Health tracking per provider
    - Automatic fallback to next available provider
    - Cost-aware provider selection
    - Recovery detection for failed providers
    """
    
    def __init__(self, config: Optional[FallbackConfig] = None):
        self.config = config or FallbackConfig()
        self.logger = get_logger("provider_fallback")
        self.health: Dict[str, ProviderHealth] = {}
        self.last_health_check: float = 0.0
        
        # Initialize health for all providers in fallback order
        for provider in self.config.fallback_order:
            self.health[provider] = ProviderHealth(
                provider=provider,
                status=ProviderStatus.HEALTHY
            )
    
    def get_next_available_provider(
        self, 
        preferred_provider: str,
        excluded: Optional[List[str]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Get the next available provider with fallback support
        
        Returns:
            Tuple of (provider_name, reason) or (None, error_message)
        """
        excluded = excluded or []
        
        # First, try the preferred provider if healthy
        if preferred_provider not in excluded:
            health = self._get_health(preferred_provider)
            if health and health.status == ProviderStatus.HEALTHY and not health.is_circuit_open:
                return preferred_provider, "preferred_provider_available"
        
        # Try providers in fallback order
        for provider in self.config.fallback_order:
            if provider == preferred_provider:
                continue
            if provider in excluded:
                continue
            
            health = self._get_health(provider)
            if not health:
                continue
            
            if health.is_circuit_open:
                self.logger.debug(f"Provider {provider} circuit breaker is open")
                continue
            
            if health.status in [ProviderStatus.HEALTHY, ProviderStatus.DEGRADED]:
                return provider, f"fallback_from_{preferred_provider}"
        
        # No healthy providers found
        return None, "no_healthy_providers_available"
    
    def execute_with_fallback(
        self,
        preferred_provider: str,
        preferred_model: str,
        execute_func,
        *args,
        **kwargs
    ) -> Tuple[Any, str]:
        """
        Execute a function with automatic fallback on failure
        
        Args:
            preferred_provider: Primary provider to try
            preferred_model: Primary model to use
            execute_func: Function to execute (should accept provider and model params)
            *args, **kwargs: Arguments to pass to execute_func
            
        Returns:
            Tuple of (result, provider_used)
        """
        attempted_providers: List[str] = []
        current_provider = preferred_provider
        current_model = preferred_model
        
        max_total_attempts = len(self.config.fallback_order) * self.config.max_retries_per_provider
        total_attempts = 0

        while current_provider and total_attempts < max_total_attempts:
            total_attempts += 1

            # Skip if circuit breaker is open
            health = self._get_health(current_provider)
            if health and health.is_circuit_open:
                self.logger.info(f"Skipping {current_provider} - circuit breaker open")
                attempted_providers.append(current_provider)
                current_provider, _ = self.get_next_available_provider(
                    preferred_provider,
                    excluded=attempted_providers
                )
                continue

            try:
                self.logger.info(f"Attempting execution with {current_provider}/{current_model}")
                start_time = time.time()

                # Execute the function
                result = execute_func(
                    provider=current_provider,
                    model=current_model,
                    *args, **kwargs
                )

                latency = time.time() - start_time

                # Record success
                self._record_success(current_provider, latency)

                self.logger.info(
                    f"Execution successful with {current_provider}",
                    latency=latency
                )

                return result, current_provider

            except Exception as e:
                latency = time.time() - start_time if 'start_time' in locals() else 0.0

                # Classify error
                context = ErrorHandler.classify_error(e, provider=current_provider)

                self.logger.warning(
                    f"Execution failed with {current_provider}",
                    error=str(e),
                    category=context.category.value if context else "unknown",
                    retryable=context.retryable if context else False
                )

                # Record failure
                self._record_failure(current_provider, latency)

                # Check if we should retry with same provider
                if context and context.retryable and self._get_health(current_provider).consecutive_failures < self.config.max_retries_per_provider:
                    self.logger.info(f"Retrying with same provider {current_provider}")
                    time.sleep(context.backoff_seconds)
                    continue

                # Move to next provider
                attempted_providers.append(current_provider)
                next_provider, reason = self.get_next_available_provider(
                    preferred_provider,
                    excluded=attempted_providers
                )

                if next_provider:
                    self.logger.info(
                        f"Falling back from {current_provider} to {next_provider}",
                        reason=reason
                    )
                    current_provider = next_provider
                    # Use default model for fallback provider
                    current_model = self._get_default_model(next_provider)
                else:
                    self.logger.error("All providers exhausted")
                    raise e

        # Exceeded max total attempts
        raise RuntimeError(
            f"Provider fallback exhausted after {total_attempts} attempts "
            f"across {len(attempted_providers)} providers"
        )
    
    def _get_health(self, provider: str) -> Optional[ProviderHealth]:
        """Get health record for a provider"""
        if provider not in self.health:
            self.health[provider] = ProviderHealth(
                provider=provider,
                status=ProviderStatus.HEALTHY
            )
        return self.health.get(provider)
    
    def _record_success(self, provider: str, latency: float):
        """Record a successful request"""
        health = self._get_health(provider)
        if not health:
            return
        
        health.total_requests += 1
        health.last_success = time.time()
        health.consecutive_failures = 0
        
        # Update average latency (exponential moving average)
        if health.avg_latency == 0:
            health.avg_latency = latency
        else:
            health.avg_latency = 0.7 * health.avg_latency + 0.3 * latency
        
        # Update status based on latency
        if health.avg_latency > self.config.slow_threshold:
            health.status = ProviderStatus.DEGRADED
        else:
            health.status = ProviderStatus.HEALTHY
        
        # Close circuit breaker if it was open
        if health.circuit_breaker_until and time.time() > health.circuit_breaker_until:
            health.circuit_breaker_until = None
            self.logger.info(f"Circuit breaker closed for {provider}")
    
    def _record_failure(self, provider: str, latency: float):
        """Record a failed request"""
        health = self._get_health(provider)
        if not health:
            return
        
        health.total_requests += 1
        health.failed_requests += 1
        health.last_failure = time.time()
        health.consecutive_failures += 1
        
        # Check if we should open circuit breaker
        if health.consecutive_failures >= self.config.circuit_breaker_threshold:
            health.circuit_breaker_until = time.time() + self.config.circuit_breaker_timeout
            health.status = ProviderStatus.UNAVAILABLE
            self.logger.warning(
                f"Circuit breaker opened for {provider}",
                consecutive_failures=health.consecutive_failures,
                cooldown_until=health.circuit_breaker_until
            )
        elif health.consecutive_failures >= 3:
            health.status = ProviderStatus.DEGRADED
    
    def _get_default_model(self, provider: str) -> str:
        """Get default model for a provider"""
        defaults = {
            "ollama": "llama3.2:latest",
            "google": "gemini-3.1-pro-preview",
            "openai": "gpt-5.4",
            "anthropic": "claude-opus-4.6",
            "groq": "llama-3.3-70b-versatile",
            "xai": "grok-4.1",
            "meta": "llama-4-scout-17b",
            "mistral": "mistral-large-latest",
            "azure": "gpt-5.4",
            "amazon": "claude-opus-4.6",
            "cohere": "command-r-plus",
            "deepseek": "deepseek-r1",
            "together": "llama-4-scout-17b",
            "minimax": "minimax-text-01",
            "zhipuai": "glm-4-plus",
            "openrouter": "anthropic/claude-3.5-sonnet"
        }
        return defaults.get(provider, "unknown")
    
    def get_health_report(self) -> Dict[str, Any]:
        """Get health report for all providers"""
        return {
            provider: {
                "status": health.status.value,
                "success_rate": health.success_rate,
                "avg_latency_ms": health.avg_latency * 1000,
                "consecutive_failures": health.consecutive_failures,
                "total_requests": health.total_requests,
                "circuit_open": health.is_circuit_open
            }
            for provider, health in self.health.items()
        }


# Global instance
_global_fallback_manager: Optional[ProviderFallbackManager] = None


def get_fallback_manager(config: Optional[FallbackConfig] = None) -> ProviderFallbackManager:
    """Get global fallback manager instance"""
    global _global_fallback_manager
    
    if _global_fallback_manager is None:
        _global_fallback_manager = ProviderFallbackManager(config)
    
    return _global_fallback_manager
