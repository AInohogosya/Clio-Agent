# Ollama Integration Guide

## Overview

This guide covers comprehensive integration with Ollama, including setup, configuration, model management, and troubleshooting for VEXIS-CLI.

## Ollama Architecture

### Integration Overview

```
┌─────────────────┐    HTTP/REST    ┌─────────────────┐
│ VEXIS-CLI       │◄──────────────►│   Ollama        │
│ Model Runner    │                │   Local Server  │
└─────────────────┘                └─────────────────┘
        │                                   │
        │                                   ▼
        │                          ┌─────────────────┐
        │                          │ Local Models    │
        │                          │ • Gemini 3 Flash│
        │                          │ • Gemma3        │
        │                          │ • Qwen2.5       │
        │                          │ • Mistral       │
        │                          └─────────────────┘
```

### Key Components

- **Ollama Server**: Local HTTP API server
- **Model Storage**: Local model files in `~/.ollama/models`
- **API Client**: HTTP client for Ollama communication
- **Error Handler**: Enhanced error handling and user guidance

## Installation and Setup

### System Requirements

- **OS**: Windows 10+, macOS 10.15+, Ubuntu 20.04+
- **RAM**: Minimum 8GB, recommended 16GB+
- **Storage**: Minimum 10GB free space
- **Python**: 3.9 or higher

### Ollama Installation

#### macOS

```bash
# Install via Homebrew
brew install ollama

# Or download directly
curl -fsSL https://ollama.ai/install.sh | sh

# Start Ollama service
ollama serve
```

#### Linux

```bash
# Download and install
curl -fsSL https://ollama.ai/install.sh | sh

# Start systemd service
sudo systemctl start ollama
sudo systemctl enable ollama

# Or run manually
ollama serve
```

#### Windows

```bash
# Download and run installer
# https://ollama.ai/download

# Start in PowerShell
ollama serve
```

### Verification

```bash
# Check installation
ollama --version

# Check service status
ollama list

# Test API
curl http://localhost:11434/api/tags
```

## Model Management

### Available Models

#### Available Models

| Model | Size | Use Case | Speed | Capability |
|-------|------|----------|-------|------------|
| **gemini-3-flash-preview** | 4.7GB | General purpose | Fast | High (if available) |
| **gemma3:4b** | 3.1GB | Balanced | Fast | Medium |
| **qwen2.5:3b** | 1.9GB | Lightweight | Very Fast | Medium |
| **deepseek-r1:7b** | ~4GB | Reasoning | Fast | High |
| **qwen3:8b** | ~4.7GB | Latest generation | Fast | High |
| **mistral:7b** | 4.7GB | General purpose | Medium | High |
| **llama3.2:3b** | 1.9GB | Reasoning | Fast | Medium |
| **codellama:7b** | 3.8GB | Code | Medium | High |

#### Model Installation

```bash
# Install recommended models
ollama pull gemma3:4b
ollama pull qwen2.5:3b
ollama pull deepseek-r1:7b

# Alternative models
ollama pull qwen3:8b
ollama pull llama3.2:3b
ollama pull mistral:7b

# List installed models
ollama list

# Show model details
ollama show gemma3:4b

# Remove model
ollama rm model-name
```

#### Model Selection Strategy

```python
# Automatic model selection based on task
def select_model(task_type: str, complexity: str) -> str:
    """Select appropriate model based on task requirements."""
    
    models = {
        "simple": {
            "fast": "qwen2.5:3b",
            "balanced": "gemma3:4b",
            "capable": "deepseek-r1:7b"
        },
        "complex": {
            "fast": "gemma3:4b",
            "balanced": "qwen3:8b",
            "capable": "deepseek-r1:7b"
        },
        "coding": {
            "fast": "qwen2.5-coder:3b",
            "balanced": "deepseek-coder:1.3b",
            "capable": "codegemma:7b"
        }
    }
    
    return models.get(task_type, {}).get(complexity, "gemma3:4b")
```

## Configuration

### Basic Configuration

```yaml
# config.yaml
api:
  preferred_provider: "ollama"
  local_endpoint: "http://localhost:11434"
  local_model: "gemma3:4b"  # Default model
  timeout: 120
  max_retries: 3

models:
  ollama:
    primary: "gemma3:4b"
    fallbacks: [
      "qwen2.5:3b",
      "deepseek-r1:7b",
      "qwen3:8b"
    ]
    temperature: 1.0
    max_tokens: 5000
    top_p: 0.9
```

### Advanced Configuration

```yaml
# Advanced Ollama settings
api:
  ollama:
    # Connection settings
    endpoint: "http://localhost:11434"
    timeout: 120
    max_retries: 3
    retry_delay: 1.0
    
    # Model settings
    default_model: "gemini-3-flash-preview:latest"
    fallback_models: ["gemma3:4b", "qwen2.5:3b"]
    
    # Performance settings
    parallel_requests: false
    max_concurrent: 1
    request_timeout: 60
    
    # Caching
    cache_responses: true
    cache_size: 100
    cache_ttl: 3600

# Model-specific settings
models:
  ollama:
    gemini-3-flash-preview:
      temperature: 1.0
      max_tokens: 5000
      top_p: 0.9
      system_prompt: "You are a helpful CLI assistant."
    
    gemma3:4b:
      temperature: 0.8
      max_tokens: 4000
      top_p: 0.8
      system_prompt: "You are a concise CLI assistant."
    
    qwen2.5:3b:
      temperature: 0.7
      max_tokens: 3000
      top_p: 0.7
      system_prompt: "You are a fast CLI assistant."
```

### Environment Variables

```bash
# Ollama configuration
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODELS=~/.ollama/models
export OLLAMA_TIMEOUT=120

# VEXIS configuration
export VEXIS_OLLAMA_MODEL=gemini-3-flash-preview:latest
export VEXIS_OLLAMA_TIMEOUT=60
export VEXIS_OLLAMA_MAX_RETRIES=3
```

## API Integration

### Ollama Client

**Location**: `src/ai_agent/external_integration/ollama_client.py`

```python
import aiohttp
import asyncio
from typing import Dict, Any, Optional

class OllamaClient:
    """Async Ollama API client."""
    
    def __init__(self, endpoint: str = "http://localhost:11434"):
        self.endpoint = endpoint
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def generate(
        self,
        model: str,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate response from Ollama model."""
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            **kwargs
        }
        
        url = f"{self.endpoint}/api/generate"
        
        try:
            async with self.session.post(url, json=payload) as response:
                response.raise_for_status()
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise OllamaConnectionError(f"Connection failed: {e}")
        except Exception as e:
            raise OllamaError(f"Generation failed: {e}")
    
    async def list_models(self) -> Dict[str, Any]:
        """List available models."""
        
        url = f"{self.endpoint}/api/tags"
        
        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise OllamaConnectionError(f"Connection failed: {e}")
    
    async def show_model(self, model: str) -> Dict[str, Any]:
        """Show model information."""
        
        payload = {"name": model}
        url = f"{self.endpoint}/api/show"
        
        try:
            async with self.session.post(url, json=payload) as response:
                response.raise_for_status()
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise OllamaConnectionError(f"Connection failed: {e}")
```

### Model Runner Integration

```python
from ai_agent.external_integration.ollama_client import OllamaClient

class OllamaModelRunner:
    """Ollama-specific model runner."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.endpoint = config.get("local_endpoint", "http://localhost:11434")
        self.default_model = config.get("local_model", "gemini-3-flash-preview:latest")
    
    async def generate_response(
        self,
        task_type: str,
        prompt: str,
        model: Optional[str] = None,
        **kwargs
    ) -> ModelResponse:
        """Generate response using Ollama."""
        
        model = model or self.default_model
        
        try:
            async with OllamaClient(self.endpoint) as client:
                # Generate response
                response = await client.generate(
                    model=model,
                    prompt=prompt,
                    stream=False,  # Use non-streaming for simplicity
                    options={
                        "temperature": kwargs.get("temperature", 1.0),
                        "max_tokens": kwargs.get("max_tokens", 5000),
                        "top_p": kwargs.get("top_p", 0.9)
                    }
                )
                
                return ModelResponse(
                    success=True,
                    content=response.get("response", ""),
                    task_type=TaskType(task_type),
                    model=model,
                    provider="ollama",
                    tokens_used=response.get("eval_count", 0),
                    latency=response.get("total_duration", 0) / 1e9,  # Convert to seconds
                    metadata={
                        "prompt_eval_count": response.get("prompt_eval_count", 0),
                        "eval_count": response.get("eval_count", 0),
                        "total_duration": response.get("total_duration", 0)
                    }
                )
                
        except OllamaError as e:
            return ModelResponse(
                success=False,
                content="",
                task_type=TaskType(task_type),
                model=model,
                provider="ollama",
                error=str(e),
                error_type=type(e).__name__
            )
    
    async def is_available(self) -> bool:
        """Check if Ollama is available."""
        
        try:
            async with OllamaClient(self.endpoint) as client:
                await client.list_models()
                return True
        except:
            return False
    
    async def get_available_models(self) -> List[str]:
        """Get list of available models."""
        
        try:
            async with OllamaClient(self.endpoint) as client:
                models_data = await client.list_models()
                return [model["name"] for model in models_data.get("models", [])]
        except:
            return []
```

## Error Handling

### Enhanced Error Handler

**Location**: `src/ai_agent/utils/ollama_error_handler.py`

```python
import re
from typing import Dict, Any, Tuple, Optional

class OllamaErrorHandler:
    """Enhanced error handler for Ollama integration."""
    
    def __init__(self):
        self.error_patterns = {
            r'permission denied|Permission denied|access denied|Access denied': self._handle_permission_error,
            r'model .* not found|model .* does not exist|unknown model': self._handle_model_not_found,
            r'connection refused|connection failed|cannot connect': self._handle_connection_error,
            r'command not found|not recognized|ollama.*not found': self._handle_installation_error,
            r'not signed in|authentication required|signin required': self._handle_signin_error,
            r'timeout|timed out|deadline exceeded': self._handle_timeout_error,
            r'memory|out of memory|cannot allocate': self._handle_memory_error,
            r'disk|no space|insufficient space': self._handle_disk_error
        }
    
    def handle_error(
        self,
        error_message: str,
        context: Optional[Dict[str, Any]] = None,
        display_to_user: bool = True
    ) -> Tuple[Optional[ErrorInfo], bool]:
        """Handle Ollama error with user guidance."""
        
        # Detect error type
        error_type, handler = self._detect_error_type(error_message)
        
        if handler:
            error_info = handler(error_message, context or {})
            
            if display_to_user:
                self._display_error_to_user(error_info)
            
            return error_info, error_info.should_retry
        
        # Unknown error
        error_info = ErrorInfo(
            error_type="unknown",
            severity="medium",
            message=error_message,
            context=context or {},
            suggestions=["Check Ollama service status", "Verify network connectivity"],
            should_retry=True,
            recovery_actions=["Restart Ollama service", "Check system resources"]
        )
        
        if display_to_user:
            self._display_error_to_user(error_info)
        
        return error_info, True
    
    def _detect_error_type(self, error_message: str) -> Tuple[str, callable]:
        """Detect error type from message."""
        
        for pattern, handler in self.error_patterns.items():
            if re.search(pattern, error_message, re.IGNORECASE):
                error_type = pattern.split('|')[0].replace('_', ' ').title()
                return error_type, handler
        
        return "unknown", None
    
    def _handle_permission_error(self, message: str, context: Dict[str, Any]) -> ErrorInfo:
        """Handle permission-related errors."""
        
        suggestions = [
            "Check file permissions for ~/.ollama",
            "Run Ollama with appropriate permissions",
            "Verify user has access to model directory"
        ]
        
        recovery_actions = [
            "macOS: Grant Full Disk Access to Terminal",
            "Linux: Fix user permissions with chown/chmod",
            "Windows: Run as Administrator"
        ]
        
        return ErrorInfo(
            error_type="permission",
            severity="high",
            message=message,
            context=context,
            suggestions=suggestions,
            should_retry=False,
            recovery_actions=recovery_actions
        )
    
    def _handle_model_not_found(self, message: str, context: Dict[str, Any]) -> ErrorInfo:
        """Handle model not found errors."""
        
        # Extract model name from error message
        model_match = re.search(r'model [\'"]([^\'"]+)[\'"]', message)
        model_name = model_match.group(1) if model_match else "unknown"
        
        suggestions = [
            f"Pull the required model: ollama pull {model_name}",
            "Check available models: ollama list",
            "Try alternative models: gemma3:4b, qwen2.5:3b, deepseek-r1:7b"
        ]
        
        recovery_actions = [
            f"ollama pull {model_name}",
            "ollama pull gemma3:4b",
            "ollama pull qwen2.5:3b",
            "ollama pull deepseek-r1:7b"
        ]
        
        return ErrorInfo(
            error_type="model_not_found",
            severity="medium",
            message=message,
            context=context,
            suggestions=suggestions,
            should_retry=True,
            recovery_actions=recovery_actions
        )
    
    def _display_error_to_user(self, error_info: ErrorInfo):
        """Display error information to user."""
        
        # Error header
        print(f"🚫 {error_info.error_type.replace('_', ' ').title()} Error")
        print(f"📝 {error_info.message}")
        
        # Severity indicator
        severity_icons = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴"
        }
        print(f"⚠️  Severity: {severity_icons.get(error_info.severity, '❓')} {error_info.severity}")
        
        # Suggestions
        if error_info.suggestions:
            print("\n💡 Suggestions:")
            for i, suggestion in enumerate(error_info.suggestions, 1):
                print(f"   {i}. {suggestion}")
        
        # Recovery actions
        if error_info.recovery_actions:
            print("\n🔧 Recovery Actions:")
            for action in error_info.recovery_actions:
                print(f"   • {action}")
        
        # Retry option
        if error_info.should_retry:
            print("\n🔄 This error can be retried automatically.")
```

## Performance Optimization

### Model Caching

```python
import hashlib
from functools import lru_cache
from typing import Dict, Any

class CachedOllamaRunner:
    """Ollama runner with response caching."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cache_enabled = config.get("cache_responses", True)
        self.cache_size = config.get("cache_size", 100)
    
    @lru_cache(maxsize=100)
    def _cached_generate(
        self,
        prompt_hash: str,
        model: str,
        temperature: float
    ) -> str:
        """Cached model generation."""
        
        # Implementation would call actual Ollama API
        # This is a simplified example
        return f"Cached response for {prompt_hash[:8]}"
    
    async def generate_with_cache(
        self,
        prompt: str,
        model: str,
        **kwargs
    ) -> ModelResponse:
        """Generate response with caching."""
        
        if not self.cache_enabled:
            return await self._generate_direct(prompt, model, **kwargs)
        
        # Create cache key
        cache_key = self._create_cache_key(prompt, model, kwargs)
        
        # Check cache
        try:
            cached_response = self._cached_generate(
                cache_key,
                model,
                kwargs.get("temperature", 1.0)
            )
            
            return ModelResponse(
                success=True,
                content=cached_response,
                task_type=TaskType.TASK_GENERATION,
                model=model,
                provider="ollama",
                metadata={"cached": True}
            )
            
        except:
            # Cache miss, generate directly
            return await self._generate_direct(prompt, model, **kwargs)
    
    def _create_cache_key(self, prompt: str, model: str, kwargs: Dict[str, Any]) -> str:
        """Create cache key from prompt and parameters."""
        
        # Include relevant parameters in cache key
        relevant_params = {
            "temperature": kwargs.get("temperature", 1.0),
            "max_tokens": kwargs.get("max_tokens", 5000),
            "top_p": kwargs.get("top_p", 0.9)
        }
        
        key_data = f"{prompt}:{model}:{relevant_params}"
        return hashlib.md5(key_data.encode()).hexdigest()
```

### Batch Processing

```python
import asyncio
from typing import List, Dict, Any

class BatchOllamaRunner:
    """Ollama runner for batch processing."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.max_concurrent = config.get("max_concurrent", 3)
    
    async def batch_generate(
        self,
        prompts: List[str],
        model: str,
        **kwargs
    ) -> List[ModelResponse]:
        """Generate responses for multiple prompts concurrently."""
        
        # Create semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def generate_single(prompt: str) -> ModelResponse:
            async with semaphore:
                return await self.generate_response(prompt, model, **kwargs)
        
        # Execute all requests concurrently
        tasks = [generate_single(prompt) for prompt in prompts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append(
                    ModelResponse(
                        success=False,
                        content="",
                        task_type=TaskType.TASK_GENERATION,
                        model=model,
                        provider="ollama",
                        error=str(result),
                        error_type=type(result).__name__
                    )
                )
            else:
                processed_results.append(result)
        
        return processed_results
```

### Memory Management

```python
import psutil
import gc
from typing import Dict, Any

class MemoryOptimizedRunner:
    """Ollama runner with memory optimization."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.memory_threshold = config.get("memory_threshold", 0.8)  # 80%
    
    async def generate_with_memory_check(
        self,
        prompt: str,
        model: str,
        **kwargs
    ) -> ModelResponse:
        """Generate response with memory monitoring."""
        
        # Check available memory
        if self._check_memory_usage():
            # Optimize memory before generation
            await self._optimize_memory()
        
        try:
            return await self.generate_response(prompt, model, **kwargs)
        
        except MemoryError:
            # Handle memory error
            await self._emergency_cleanup()
            raise
    
    def _check_memory_usage(self) -> bool:
        """Check if memory usage is above threshold."""
        
        memory = psutil.virtual_memory()
        usage_percent = memory.percent / 100
        
        return usage_percent > self.memory_threshold
    
    async def _optimize_memory(self):
        """Optimize memory usage."""
        
        # Force garbage collection
        gc.collect()
        
        # Clear caches if implemented
        if hasattr(self, '_cache'):
            self._cache.clear()
        
        # Log optimization
        logger.info("Memory optimization performed")
    
    async def _emergency_cleanup(self):
        """Emergency memory cleanup."""
        
        # Aggressive cleanup
        gc.collect(2)  # Collect all generations
        
        # Clear all caches
        if hasattr(self, '_cache'):
            self._cache.clear()
        
        logger.warning("Emergency memory cleanup performed")
```

## Monitoring and Debugging

### Health Monitoring

```python
import asyncio
import aiohttp
from typing import Dict, Any

class OllamaHealthMonitor:
    """Monitor Ollama service health."""
    
    def __init__(self, endpoint: str = "http://localhost:11434"):
        self.endpoint = endpoint
    
    async def check_health(self) -> Dict[str, Any]:
        """Comprehensive health check."""
        
        health_status = {
            "service_available": False,
            "models_available": [],
            "response_time": None,
            "memory_usage": None,
            "disk_usage": None,
            "errors": []
        }
        
        try:
            # Check service availability
            start_time = asyncio.get_event_loop().time()
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.endpoint}/api/tags", timeout=5) as response:
                    if response.status == 200:
                        health_status["service_available"] = True
                        
                        # Get response time
                        end_time = asyncio.get_event_loop().time()
                        health_status["response_time"] = end_time - start_time
                        
                        # Get available models
                        data = await response.json()
                        health_status["models_available"] = [
                            model["name"] for model in data.get("models", [])
                        ]
                    else:
                        health_status["errors"].append(f"HTTP {response.status}")
        
        except asyncio.TimeoutError:
            health_status["errors"].append("Request timeout")
        except aiohttp.ClientError as e:
            health_status["errors"].append(f"Connection error: {e}")
        except Exception as e:
            health_status["errors"].append(f"Unexpected error: {e}")
        
        # Check system resources
        try:
            import psutil
            
            # Memory usage
            memory = psutil.virtual_memory()
            health_status["memory_usage"] = {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent
            }
            
            # Disk usage for Ollama models
            disk = psutil.disk_usage(psutil.Path.home() / ".ollama")
            health_status["disk_usage"] = {
                "total": disk.total,
                "free": disk.free,
                "percent": (disk.total - disk.free) / disk.total * 100
            }
            
        except ImportError:
            health_status["errors"].append("psutil not available for system monitoring")
        
        return health_status
    
    async def start_monitoring(self, interval: int = 60):
        """Start continuous health monitoring."""
        
        while True:
            health = await self.check_health()
            
            # Log health status
            if health["service_available"]:
                logger.info(f"Ollama healthy - {len(health['models_available'])} models available")
            else:
                logger.error(f"Ollama unhealthy - {health['errors']}")
            
            # Check for warnings
            if health["memory_usage"] and health["memory_usage"]["percent"] > 90:
                logger.warning("High memory usage detected")
            
            if health["disk_usage"] and health["disk_usage"]["percent"] > 90:
                logger.warning("Low disk space for Ollama models")
            
            await asyncio.sleep(interval)
```

### Debug Tools

```python
class OllamaDebugger:
    """Debugging tools for Ollama integration."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def debug_connection(self) -> Dict[str, Any]:
        """Debug Ollama connection issues."""
        
        debug_info = {
            "endpoint": self.config.get("local_endpoint"),
            "connection_test": False,
            "api_version": None,
            "models_test": False,
            "generation_test": False,
            "errors": []
        }
        
        try:
            # Test basic connection
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{debug_info['endpoint']}/api/tags", timeout=5) as response:
                    if response.status == 200:
                        debug_info["connection_test"] = True
                        
                        # Get API version
                        version_response = await session.get(f"{debug_info['endpoint']}/api/version")
                        if version_response.status == 200:
                            version_data = await version_response.json()
                            debug_info["api_version"] = version_data.get("version")
                        
                        # Test model listing
                        models_data = await response.json()
                        debug_info["models_test"] = True
                        
                        # Test generation with first available model
                        models = models_data.get("models", [])
                        if models:
                            model_name = models[0]["name"]
                            
                            gen_payload = {
                                "model": model_name,
                                "prompt": "test",
                                "stream": False
                            }
                            
                            async with session.post(
                                f"{debug_info['endpoint']}/api/generate",
                                json=gen_payload,
                                timeout=10
                            ) as gen_response:
                                if gen_response.status == 200:
                                    debug_info["generation_test"] = True
                                else:
                                    debug_info["errors"].append(f"Generation failed: HTTP {gen_response.status}")
                        else:
                            debug_info["errors"].append("No models available")
                    else:
                        debug_info["errors"].append(f"Connection failed: HTTP {response.status}")
        
        except Exception as e:
            debug_info["errors"].append(f"Connection error: {e}")
        
        return debug_info
    
    def debug_config(self) -> Dict[str, Any]:
        """Debug configuration issues."""
        
        config_info = {
            "config_loaded": bool(self.config),
            "required_fields": {},
            "optional_fields": {},
            "validation_errors": []
        }
        
        if not self.config:
            config_info["validation_errors"].append("Configuration not loaded")
            return config_info
        
        # Check required fields
        required_fields = ["local_endpoint", "local_model"]
        for field in required_fields:
            if field in self.config:
                config_info["required_fields"][field] = "✅ Present"
            else:
                config_info["required_fields"][field] = "❌ Missing"
                config_info["validation_errors"].append(f"Missing required field: {field}")
        
        # Check optional fields
        optional_fields = ["timeout", "max_retries", "temperature"]
        for field in optional_fields:
            if field in self.config:
                config_info["optional_fields"][field] = f"✅ {self.config[field]}"
            else:
                config_info["optional_fields"][field] = "⚠️ Using default"
        
        return config_info
```

## Troubleshooting

### Common Issues and Solutions

#### Service Not Running

**Symptoms:**
```
connection refused
cannot connect to localhost:11434
```

**Solutions:**
```bash
# Start Ollama service
ollama serve

# Check if running
ps aux | grep ollama

# Start as systemd service (Linux)
sudo systemctl start ollama
sudo systemctl enable ollama
```

#### Model Not Found

**Symptoms:**
```
model 'xyz' not found
unknown model
```

**Solutions:**
```bash
# List available models
ollama list

# Pull required model
ollama pull gemini-3-flash-preview:latest

# Try alternative models
ollama pull gemma3:4b
ollama pull qwen2.5:3b
```

#### Permission Issues

**Symptoms:**
```
permission denied while accessing ~/.ollama
Operation not permitted
```

**Solutions:**
```bash
# Fix permissions
sudo chown -R $USER:$USER ~/.ollama
chmod 755 ~/.ollama

# macOS: Grant Full Disk Access
# System Preferences → Security & Privacy → Full Disk Access
```

#### Memory Issues

**Symptoms:**
```
out of memory
cannot allocate
system becomes unresponsive
```

**Solutions:**
```bash
# Use smaller models
ollama pull qwen2.5:1.5b
ollama pull gemma3:2b
ollama pull deepseek-r1:1.5b

# Monitor memory usage
free -h  # Linux
vm_stat  # macOS

# Restart Ollama to clear memory
pkill ollama
ollama serve
```

#### Network Issues

**Symptoms:**
```
connection timeout
network unreachable
```

**Solutions:**
```bash
# Check network connectivity
curl -I http://localhost:11434/api/tags

# Check firewall settings
sudo ufw status  # Linux
# System Preferences → Security → Firewall  # macOS

# Restart network services
sudo systemctl restart networking  # Linux
```

### Diagnostic Scripts

#### Full System Check

```bash
#!/bin/bash
# ollama_diagnostic.sh

echo "=== Ollama Diagnostic Tool ==="

# Check Ollama installation
echo "1. Checking Ollama installation..."
if command -v ollama &> /dev/null; then
    echo "✅ Ollama installed: $(ollama --version)"
else
    echo "❌ Ollama not installed"
    exit 1
fi

# Check service status
echo "2. Checking Ollama service..."
if pgrep -f "ollama serve" > /dev/null; then
    echo "✅ Ollama service running"
else
    echo "❌ Ollama service not running"
    echo "   Start with: ollama serve"
fi

# Check API connectivity
echo "3. Checking API connectivity..."
if curl -s http://localhost:11434/api/tags > /dev/null; then
    echo "✅ API endpoint accessible"
else
    echo "❌ API endpoint not accessible"
fi

# Check models
echo "4. Checking installed models..."
model_count=$(ollama list | wc -l)
if [ $model_count -gt 1 ]; then
    echo "✅ $((model_count-1)) models installed"
    ollama list
else
    echo "❌ No models installed"
    echo "   Install with: ollama pull gemini-3-flash-preview"
fi

# Check disk space
echo "5. Checking disk space..."
disk_usage=$(df -h ~/.ollama 2>/dev/null | awk 'NR==2 {print $5}' | sed 's/%//')
if [ ! -z "$disk_usage" ]; then
    if [ $disk_usage -lt 90 ]; then
        echo "✅ Disk usage: ${disk_usage}%"
    else
        echo "⚠️  High disk usage: ${disk_usage}%"
    fi
else
    echo "❓ Cannot check disk usage"
fi

# Check memory
echo "6. Checking memory..."
if command -v free &> /dev/null; then
    memory_usage=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
    if [ $memory_usage -lt 90 ]; then
        echo "✅ Memory usage: ${memory_usage}%"
    else
        echo "⚠️  High memory usage: ${memory_usage}%"
    fi
fi

echo "=== Diagnostic Complete ==="
```

This comprehensive Ollama integration guide provides everything needed to set up, configure, optimize, and troubleshoot Ollama integration with VEXIS-CLI.
