"""
Cost Manager for Clio-Agent-1
Tracks and manages API usage costs with budget controls
"""

import time
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from enum import Enum

from .logger import get_logger


class BudgetAlertLevel(Enum):
    """Budget alert levels"""
    NORMAL = "normal"
    WARNING = "warning"      # 80% of budget
    CRITICAL = "critical"    # 95% of budget
    EXCEEDED = "exceeded"    # 100%+ of budget


@dataclass
class ModelPricing:
    """Pricing information for a model"""
    provider: str
    model: str
    input_price_per_1m: float   # USD per 1M input tokens
    output_price_per_1m: float  # USD per 1M output tokens
    context_window: int = 0


@dataclass
class UsageRecord:
    """Single usage record"""
    timestamp: float
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    task_type: str


@dataclass
class BudgetConfig:
    """Budget configuration"""
    daily_budget: Optional[float] = None      # USD
    monthly_budget: Optional[float] = None    # USD
    per_request_budget: Optional[float] = None  # USD
    warning_threshold: float = 0.8
    critical_threshold: float = 0.95
    
    # Cost optimization
    prefer_cheaper_models: bool = False
    auto_fallback_on_cost: bool = True


@dataclass
class CostStats:
    """Cost statistics"""
    total_requests: int = 0
    total_cost: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    
    # Time-based tracking
    daily_costs: Dict[str, float] = field(default_factory=dict)
    monthly_costs: Dict[str, float] = field(default_factory=dict)
    provider_costs: Dict[str, float] = field(default_factory=dict)
    model_costs: Dict[str, float] = field(default_factory=dict)


class CostManager:
    """
    Manages API usage costs and budget enforcement
    
    Features:
    - Real-time cost tracking
    - Budget limit enforcement
    - Cost-based provider selection
    - Usage analytics
    - Alert generation
    """
    
    # Provider pricing (as of 2026, approximate)
    PRICING: Dict[str, ModelPricing] = {
        # OpenAI
        "gpt-5.4": ModelPricing("openai", "gpt-5.4", 5.00, 20.00, 128000),
        "gpt-5.4-pro": ModelPricing("openai", "gpt-5.4-pro", 7.50, 30.00, 200000),
        "gpt-5.4-mini": ModelPricing("openai", "gpt-5.4-mini", 0.60, 2.40, 128000),
        
        # Google
        "gemini-3.1-pro": ModelPricing("google", "gemini-3.1-pro", 2.50, 15.00, 2000000),
        "gemini-3-flash": ModelPricing("google", "gemini-3-flash", 0.125, 0.375, 1000000),
        
        # Anthropic
        "claude-opus-4.6": ModelPricing("anthropic", "claude-opus-4.6", 15.00, 75.00, 200000),
        "claude-sonnet-4.6": ModelPricing("anthropic", "claude-sonnet-4.6", 3.00, 15.00, 200000),
        
        # Groq (typically cheaper/faster)
        "llama-3.3-70b-versatile": ModelPricing("groq", "llama-3.3-70b-versatile", 0.59, 0.79, 128000),
        "gemma2-9b-it": ModelPricing("groq", "gemma2-9b-it", 0.20, 0.20, 8192),
        
        # Local (Ollama) - essentially free
        "llama3.2:latest": ModelPricing("ollama", "llama3.2:latest", 0.0, 0.0, 128000),
        "deepseek-r1": ModelPricing("ollama", "deepseek-r1", 0.0, 0.0, 128000),
    }
    
    def __init__(
        self,
        config: Optional[BudgetConfig] = None,
        persist_path: Optional[str] = None
    ):
        self.config = config or BudgetConfig()
        self.logger = get_logger("cost_manager")
        self.lock = Lock()
        
        # Storage
        self.stats = CostStats()
        self.recent_usage: List[UsageRecord] = []
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".clio_agent" / "costs.json"
        
        # Load persisted data
        self._load_from_disk()
        
        self.logger.info(
            "Cost manager initialized",
            daily_budget=self.config.daily_budget,
            monthly_budget=self.config.monthly_budget,
            per_request_budget=self.config.per_request_budget
        )
    
    def estimate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> float:
        """
        Estimate cost for a request
        
        Returns:
            Estimated cost in USD
        """
        pricing = self._get_pricing(provider, model)
        
        input_cost = (prompt_tokens / 1_000_000) * pricing.input_price_per_1m
        output_cost = (completion_tokens / 1_000_000) * pricing.output_price_per_1m
        
        return round(input_cost + output_cost, 6)
    
    def record_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        task_type: str = "unknown"
    ) -> float:
        """
        Record actual usage and update statistics
        
        Returns:
            Actual cost incurred
        """
        cost = self.estimate_cost(provider, model, prompt_tokens, completion_tokens)
        
        with self.lock:
            record = UsageRecord(
                timestamp=time.time(),
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                task_type=task_type
            )
            
            self.recent_usage.append(record)
            # Keep only last 1000 records in memory
            if len(self.recent_usage) > 1000:
                self.recent_usage = self.recent_usage[-1000:]
            
            # Update statistics
            self.stats.total_requests += 1
            self.stats.total_cost += cost
            self.stats.total_prompt_tokens += prompt_tokens
            self.stats.total_completion_tokens += completion_tokens
            
            # Time-based tracking
            today = time.strftime("%Y-%m-%d")
            month = time.strftime("%Y-%m")
            
            self.stats.daily_costs[today] = self.stats.daily_costs.get(today, 0.0) + cost
            self.stats.monthly_costs[month] = self.stats.monthly_costs.get(month, 0.0) + cost
            
            # Provider and model tracking
            provider_key = f"{provider}/{model}"
            self.stats.provider_costs[provider] = self.stats.provider_costs.get(provider, 0.0) + cost
            self.stats.model_costs[provider_key] = self.stats.model_costs.get(provider_key, 0.0) + cost
            
            self.logger.info(
                f"Recorded usage for {provider}/{model}",
                cost=cost,
                tokens=prompt_tokens + completion_tokens,
                total_cost=self.stats.total_cost
            )
            
            # Persist to disk
            self._save_to_disk()
            
            # Check budget alerts
            self._check_budget_alerts()
        
        return cost
    
    def check_budget(self, estimated_cost: float) -> tuple[bool, Optional[str]]:
        """
        Check if request is within budget
        
        Returns:
            Tuple of (allowed, reason)
        """
        # Check per-request limit
        if self.config.per_request_budget and estimated_cost > self.config.per_request_budget:
            return False, f"Estimated cost ${estimated_cost:.4f} exceeds per-request budget ${self.config.per_request_budget:.4f}"
        
        # Check daily budget
        if self.config.daily_budget:
            today = time.strftime("%Y-%m-%d")
            daily_spent = self.stats.daily_costs.get(today, 0.0)
            if daily_spent + estimated_cost > self.config.daily_budget:
                return False, f"Daily budget ${self.config.daily_budget:.2f} would be exceeded"
        
        # Check monthly budget
        if self.config.monthly_budget:
            month = time.strftime("%Y-%m")
            monthly_spent = self.stats.monthly_costs.get(month, 0.0)
            if monthly_spent + estimated_cost > self.config.monthly_budget:
                return False, f"Monthly budget ${self.config.monthly_budget:.2f} would be exceeded"
        
        return True, None
    
    def get_cheaper_alternative(
        self,
        provider: str,
        model: str,
        max_quality_degradation: str = "minimal"
    ) -> Optional[tuple[str, str]]:
        """
        Suggest a cheaper alternative model
        
        Returns:
            Tuple of (alternative_provider, alternative_model) or None
        """
        current_pricing = self._get_pricing(provider, model)
        current_cost = current_pricing.input_price_per_1m + current_pricing.output_price_per_1m
        
        alternatives = []
        
        for key, pricing in self.PRICING.items():
            if pricing.provider == provider and pricing.model == model:
                continue
            
            alt_cost = pricing.input_price_per_1m + pricing.output_price_per_1m
            
            # Skip if more expensive
            if alt_cost >= current_cost:
                continue
            
            # Check quality level (simplified)
            quality_ok = self._check_quality_compatibility(model, pricing.model, max_quality_degradation)
            
            if quality_ok:
                alternatives.append((pricing.provider, pricing.model, alt_cost))
        
        # Sort by cost and return cheapest
        if alternatives:
            alternatives.sort(key=lambda x: x[2])
            best = alternatives[0]
            savings = ((current_cost - best[2]) / current_cost) * 100
            self.logger.info(
                f"Suggested cheaper alternative: {best[0]}/{best[1]}",
                savings_percent=f"{savings:.1f}%"
            )
            return (best[0], best[1])
        
        return None
    
    def get_budget_status(self) -> Dict[str, Any]:
        """Get current budget status"""
        today = time.strftime("%Y-%m-%d")
        month = time.strftime("%Y-%m")
        
        daily_spent = self.stats.daily_costs.get(today, 0.0)
        monthly_spent = self.stats.monthly_costs.get(month, 0.0)
        
        status = {
            "total_cost": round(self.stats.total_cost, 4),
            "total_requests": self.stats.total_requests,
            "today": {
                "spent": round(daily_spent, 4),
                "budget": self.config.daily_budget,
                "percentage": (daily_spent / self.config.daily_budget * 100) if self.config.daily_budget else None
            },
            "this_month": {
                "spent": round(monthly_spent, 4),
                "budget": self.config.monthly_budget,
                "percentage": (monthly_spent / self.config.monthly_budget * 100) if self.config.monthly_budget else None
            },
            "alert_level": self._get_alert_level(daily_spent, monthly_spent).value
        }
        
        return status
    
    def get_usage_report(self, days: int = 7) -> Dict[str, Any]:
        """Get usage report for the last N days"""
        cutoff = time.time() - (days * 24 * 3600)
        
        recent_records = [r for r in self.recent_usage if r.timestamp > cutoff]
        
        # Group by day
        daily_usage: Dict[str, Dict] = {}
        for record in recent_records:
            day = time.strftime("%Y-%m-%d", time.localtime(record.timestamp))
            if day not in daily_usage:
                daily_usage[day] = {"cost": 0.0, "requests": 0, "tokens": 0}
            daily_usage[day]["cost"] += record.cost
            daily_usage[day]["requests"] += 1
            daily_usage[day]["tokens"] += record.prompt_tokens + record.completion_tokens
        
        return {
            "period_days": days,
            "total_requests": len(recent_records),
            "total_cost": round(sum(r.cost for r in recent_records), 4),
            "daily_breakdown": daily_usage,
            "top_providers": dict(sorted(
                self.stats.provider_costs.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]),
            "top_models": dict(sorted(
                self.stats.model_costs.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5])
        }
    
    def _get_pricing(self, provider: str, model: str) -> ModelPricing:
        """Get pricing for a model"""
        key = model
        if key in self.PRICING:
            return self.PRICING[key]
        
        # Try fuzzy match
        for pkey, pricing in self.PRICING.items():
            if pricing.provider == provider and model.startswith(pricing.model.split(':')[0]):
                return pricing
        
        # Default pricing if unknown
        self.logger.warning(f"Unknown pricing for {provider}/{model}, using defaults")
        return ModelPricing(provider, model, 1.0, 3.0, 128000)
    
    def _check_quality_compatibility(
        self,
        original: str,
        alternative: str,
        max_degradation: str
    ) -> bool:
        """Check if alternative model meets quality requirements"""
        # Simplified quality mapping
        quality_tiers = {
            "claude-opus-4.6": 5, "gpt-5.4-pro": 5,
            "claude-sonnet-4.6": 4, "gpt-5.4": 4, "gemini-3.1-pro-preview": 4,
            "llama-3.3-70b-versatile": 3, "gpt-5.4-mini": 3,
            "gemma2-9b-it": 2, "gemini-3-flash-preview": 2,
            "llama3.2:latest": 1, "deepseek-r1": 1
        }
        
        orig_tier = quality_tiers.get(original, 3)
        alt_tier = quality_tiers.get(alternative, 3)
        
        degradation = orig_tier - alt_tier
        
        if max_degradation == "none":
            return degradation <= 0
        elif max_degradation == "minimal":
            return degradation <= 1
        elif max_degradation == "moderate":
            return degradation <= 2
        else:
            return True
    
    def _check_budget_alerts(self):
        """Check and log budget alerts"""
        today = time.strftime("%Y-%m-%d")
        month = time.strftime("%Y-%m")
        
        daily_spent = self.stats.daily_costs.get(today, 0.0)
        monthly_spent = self.stats.monthly_costs.get(month, 0.0)
        
        level = self._get_alert_level(daily_spent, monthly_spent)
        
        if level == BudgetAlertLevel.WARNING:
            self.logger.warning(
                "BUDGET WARNING: Approaching budget limit",
                daily_spent=round(daily_spent, 2),
                monthly_spent=round(monthly_spent, 2)
            )
        elif level == BudgetAlertLevel.CRITICAL:
            self.logger.error(
                "BUDGET CRITICAL: Very close to budget limit",
                daily_spent=round(daily_spent, 2),
                monthly_spent=round(monthly_spent, 2)
            )
        elif level == BudgetAlertLevel.EXCEEDED:
            self.logger.error(
                "BUDGET EXCEEDED: Budget limit has been exceeded",
                daily_spent=round(daily_spent, 2),
                monthly_spent=round(monthly_spent, 2)
            )
    
    def _get_alert_level(
        self,
        daily_spent: float,
        monthly_spent: float
    ) -> BudgetAlertLevel:
        """Determine alert level based on spending"""
        max_percentage = 0.0
        
        if self.config.daily_budget:
            max_percentage = max(max_percentage, daily_spent / self.config.daily_budget)
        
        if self.config.monthly_budget:
            max_percentage = max(max_percentage, monthly_spent / self.config.monthly_budget)
        
        if max_percentage >= 1.0:
            return BudgetAlertLevel.EXCEEDED
        elif max_percentage >= self.config.critical_threshold:
            return BudgetAlertLevel.CRITICAL
        elif max_percentage >= self.config.warning_threshold:
            return BudgetAlertLevel.WARNING
        else:
            return BudgetAlertLevel.NORMAL
    
    def _save_to_disk(self):
        """Persist cost data to disk"""
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "stats": {
                    "total_requests": self.stats.total_requests,
                    "total_cost": self.stats.total_cost,
                    "total_prompt_tokens": self.stats.total_prompt_tokens,
                    "total_completion_tokens": self.stats.total_completion_tokens,
                    "daily_costs": self.stats.daily_costs,
                    "monthly_costs": self.stats.monthly_costs,
                    "provider_costs": self.stats.provider_costs,
                    "model_costs": self.stats.model_costs
                },
                "recent_usage": [
                    {
                        "timestamp": r.timestamp,
                        "provider": r.provider,
                        "model": r.model,
                        "prompt_tokens": r.prompt_tokens,
                        "completion_tokens": r.completion_tokens,
                        "cost": r.cost,
                        "task_type": r.task_type
                    }
                    for r in self.recent_usage[-100:]  # Last 100 only
                ]
            }
            
            with open(self.persist_path, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Failed to save cost data: {e}")
    
    def _load_from_disk(self):
        """Load persisted cost data"""
        try:
            if not self.persist_path.exists():
                return
            
            with open(self.persist_path, 'r') as f:
                data = json.load(f)
            
            stats_data = data.get("stats", {})
            self.stats.total_requests = stats_data.get("total_requests", 0)
            self.stats.total_cost = stats_data.get("total_cost", 0.0)
            self.stats.total_prompt_tokens = stats_data.get("total_prompt_tokens", 0)
            self.stats.total_completion_tokens = stats_data.get("total_completion_tokens", 0)
            self.stats.daily_costs = stats_data.get("daily_costs", {})
            self.stats.monthly_costs = stats_data.get("monthly_costs", {})
            self.stats.provider_costs = stats_data.get("provider_costs", {})
            self.stats.model_costs = stats_data.get("model_costs", {})
            
            self.logger.info(f"Loaded cost data: ${self.stats.total_cost:.4f} total spent")
            
        except Exception as e:
            self.logger.error(f"Failed to load cost data: {e}")


# Global instance
_global_cost_manager: Optional[CostManager] = None


def get_cost_manager(
    daily_budget: Optional[float] = None,
    monthly_budget: Optional[float] = None
) -> CostManager:
    """Get global cost manager instance"""
    global _global_cost_manager
    
    if _global_cost_manager is None:
        config = BudgetConfig(
            daily_budget=daily_budget,
            monthly_budget=monthly_budget
        )
        _global_cost_manager = CostManager(config)
    
    return _global_cost_manager


def estimate_request_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int
) -> float:
    """Convenience function to estimate cost"""
    manager = get_cost_manager()
    return manager.estimate_cost(provider, model, prompt_tokens, completion_tokens)
