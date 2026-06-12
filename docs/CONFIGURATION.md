# Configuration Guide

## Overview

VEXIS-CLI uses a flexible configuration system that supports 16+ AI providers, customization options, and environment-specific settings. This guide covers all configuration aspects from basic setup to advanced customization.

## Supported Providers

### Local Providers
- **Ollama**: Local models with privacy-first design

### Cloud Providers (API Key Required)
- **Groq**: Fast inference with Llama/Mixtral models
- **Google Gemini**: Enterprise-grade cloud models
- **OpenAI**: GPT models with advanced capabilities
- **Anthropic**: Claude models with strong reasoning
- **xAI**: Grok models for real-time knowledge
- **Meta**: Llama models via Meta API
- **Mistral AI**: Advanced multilingual models
- **Microsoft Azure**: GPT models via Azure
- **Amazon Bedrock**: Titan/Nova models via AWS
- **Cohere**: Command models for enterprise
- **DeepSeek**: Advanced reasoning models
- **Together AI**: Open-source model hosting

## Configuration Files

### Primary Configuration

**File**: `config.yaml`

```yaml
# VEXIS-CLI Configuration File
# Version: 2.1.0

# AI Provider Configuration
api:
  # Primary AI provider: "ollama", "groq", "google", "openai", "anthropic", "xai", "meta", "mistral", "microsoft", "amazon", "cohere", "deepseek", "together"
  preferred_provider: "ollama"
  
  # Ollama Configuration
  local_endpoint: "http://localhost:11434"
  local_model: "gemini-3-flash-preview:latest"
  
  # Google Gemini Configuration
  google_api_key: null  # Set via environment variable or here
  gemini_model: "gemini-1.5-flash"
  
  # OpenAI Configuration
  openai_api_key: null  # Set via environment variable or here
  openai_model: "gpt-5.4"  # Options: gpt-5.4, gpt-5.4-pro, gpt-5.4-mini (New), gpt-5.4-nano (New), gpt-4o
  
  # Anthropic Configuration
  anthropic_api_key: null  # Set via environment variable or here
  anthropic_model: "claude-opus-4-6-20251120"
  
  # Groq Configuration
  groq_api_key: null  # Set via environment variable or here
  groq_model: "llama-3.3-70b-versatile"
  
  # API Settings
  timeout: 120          # Request timeout in seconds
  max_retries: 3        # Maximum retry attempts
  rate_limit: 10        # Requests per minute (Google only)

# Execution Configuration
execution:
  # Safety Settings
  safety_mode: true          # Enable safety checks
  dry_run: false           # Show commands without executing
  verify_commands: true    # Validate commands before execution
  
  # Performance Settings
  parallel_execution: false    # Execute commands in parallel
  max_concurrent: 3            # Maximum parallel commands
  
  # Resource Limits
  max_memory_mb: 2048      # Maximum memory usage
  max_execution_time: 300  # Maximum execution time (seconds)

# Logging Configuration
logging:
  level: "INFO"           # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: "structured"    # "simple" or "structured"
  file: "logs/vexis.log"  # Log file path
  console: true           # Enable console logging
  rotation: true          # Enable log rotation
  
  # Structured Logging Fields
  include_timestamp: true
  include_level: true
  include_module: true
  include_function: true

# Security Configuration
security:
  # Command Blocking Settings (NEW in v2.1)
  # All settings are DISABLED by default for user freedom
  # Enable these to add safety checks
  
  ## Block dangerous commands (rm -rf /, fork bomb, etc.)
  # Set to true to enable command blocking
  enable_command_blocking: false
  
  ## Require confirmation for risky commands
  # Set to true to prompt before executing rm -rf, sudo, etc.
  enable_confirmation_prompts: false
  
  ## Show warning for sudo commands
  enable_sudo_warning: false
  
  ## Show warning for pipe-to-shell commands (curl ... | bash)
  enable_shell_pipe_warning: false
  
  ## Use sandbox tools (firejail, bubblewrap) when available
  enable_sandbox: true
  
  # Legacy Settings (for backward compatibility)
  blacklist_mode: false    # Deprecated: Use enable_command_blocking
  whitelist_mode: false    # Deprecated
  
  # File System Restrictions
  restricted_paths: [
    "/etc",
    "/usr/bin",
    "/System"
  ]
  
  # Network Restrictions
  allow_network: true
  allowed_domains: [
    "ollama.ai",
    "googleapis.com"
  ]

# Model Configuration
models:
  # Ollama Models
  ollama:
    primary: "gemini-3-flash-preview:latest"
    fallbacks: [
      "gemma3:4b",
      "qwen2.5:3b",
      "mistral:7b"
    ]
    
    # Model Settings
    temperature: 1.0
    max_tokens: 5000
    top_p: 0.9
    
  # Gemini Models
  gemini:
    primary: "gemini-1.5-flash"
    fallbacks: [
      "gemini-1.5-pro"
    ]
    
    # Model Settings
    temperature: 1.0
    max_tokens: 5000
    top_p: 0.9
    top_k: 40

# User Interface Configuration
ui:
  # Display Settings
  color_output: true
  show_progress: true
  show_timing: true
  
  # Menu Settings
  menu_style: "yellow"     # "yellow", "simple", "detailed"
  auto_select: false       # Auto-select single options
  
  # Interaction Settings
  confirm_dangerous: true
  show_suggestions: true

# Development Configuration
development:
  # Debug Settings
  debug_mode: false
  verbose_errors: true
  stack_traces: false
  
  # Testing Settings
  test_mode: false
  mock_responses: false
  
  # Performance Monitoring
  profile_performance: false
  benchmark_mode: false
```

### Environment Variables

**File**: `.env` (optional)

```bash
# API Keys
GOOGLE_API_KEY=your-google-api-key-here
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
XAI_API_KEY=your-xai-api-key-here
META_API_KEY=your-meta-api-key-here
MISTRAL_API_KEY=your-mistral-api-key-here
AZURE_OPENAI_API_KEY=your-azure-api-key-here
AZURE_OPENAI_ENDPOINT=your-azure-endpoint
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
COHERE_API_KEY=your-cohere-api-key-here
DEEPSEEK_API_KEY=your-deepseek-api-key-here
GROQ_API_KEY=your-groq-api-key-here
TOGETHER_API_KEY=your-together-api-key-here

# Endpoints
OLLAMA_HOST=http://localhost:11434
GOOGLE_ENDPOINT=https://generativelanguage.googleapis.com

# Logging
LOG_LEVEL=DEBUG
LOG_FILE=logs/vexis.log

# Security
VEXIS_SAFE_MODE=true
VEXIS_DRY_RUN=false

# Development
VEXIS_DEBUG=true
VEXIS_TEST_MODE=false
```

## Configuration Sections

### API Provider Configuration

#### Ollama Configuration

```yaml
api:
  preferred_provider: "ollama"
  local_endpoint: "http://localhost:11434"
  local_model: "gemini-3-flash-preview:latest"
  timeout: 120
  max_retries: 3
```

**Settings Explained:**

- **preferred_provider**: Choose between "ollama" (local) and "google" (cloud)
- **local_endpoint**: Ollama server URL (default: http://localhost:11434)
- **local_model**: Default model to use (must be installed)
- **timeout**: Request timeout in seconds
- **max_retries**: Maximum retry attempts for failed requests

#### Google Gemini Configuration

```yaml
api:
  google_api_key: null  # Set in environment for security
  gemini_model: "gemini-2.5-flash"  # Latest stable model
  rate_limit: 10
```

**Settings Explained:**

- **google_api_key**: Your Google AI Studio API key
- **gemini_model**: Gemini model version
- **rate_limit**: Requests per minute (to avoid quota limits)

**API Key Setup:**

1. **Get API Key**: Visit [Google AI Studio](https://aistudio.google.com)
2. **Set Environment Variable**:
   ```bash
   export GOOGLE_API_KEY="your-api-key-here"
   ```
3. **Or Set in Config** (less secure):
   ```yaml
   api:
     google_api_key: "your-api-key-here"
   ```

### Execution Configuration

#### Safety Settings

```yaml
execution:
  safety_mode: true          # Enable safety checks
  dry_run: false           # Show commands without executing
  verify_commands: true    # Validate commands before execution
```

**Safety Features:**

- **Command Validation**: Check commands against safety rules
- **Permission Checking**: Verify file system permissions
- **Dangerous Operation Warnings**: Alert for risky operations

#### Performance Settings

```yaml
execution:
  parallel_execution: false    # Execute commands in parallel
  max_concurrent: 3            # Maximum parallel commands
  max_memory_mb: 2048         # Memory limit
  max_execution_time: 300     # Time limit
```

**Performance Optimization:**

- **Parallel Execution**: Run independent commands simultaneously
- **Resource Limits**: Prevent resource exhaustion
- **Timeout Protection**: Stop long-running operations

### Logging Configuration

#### Log Levels

```yaml
logging:
  level: "INFO"           # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: "structured"    # "simple" or "structured"
  file: "logs/vexis.log"
  console: true
  rotation: true
```

**Log Level Guide:**

- **DEBUG**: Detailed debugging information
- **INFO**: General information (default)
- **WARNING**: Warning messages
- **ERROR**: Error messages
- **CRITICAL**: Critical errors

#### Structured Logging

```yaml
logging:
  include_timestamp: true
  include_level: true
  include_module: true
  include_function: true
```

**Structured Log Format:**
```json
{
  "timestamp": "2026-03-06T12:00:00Z",
  "level": "INFO",
  "module": "model_runner",
  "function": "execute_instruction",
  "message": "Instruction processed successfully",
  "context": {
    "instruction": "list files",
    "provider": "ollama",
    "model": "gemini-3-flash-preview"
  }
}
```

### Security Configuration

#### Command Blocking (New in v2.1)

VEXIS-CLI now offers configurable command blocking. All safety features are **disabled by default** to give users full control.

**Configuration via config.yaml:**
```yaml
security:
  # Enable blocking of dangerous commands (rm -rf /, fork bomb, etc.)
  enable_command_blocking: false
  
  # Require confirmation before executing risky commands
  enable_confirmation_prompts: false
  
  # Show warnings for sudo commands
  enable_sudo_warning: false
  
  # Show warnings for pipe-to-shell (curl | bash)
  enable_shell_pipe_warning: false
  
  # Use sandbox tools when available (firejail, bubblewrap)
  enable_sandbox: true
```

**Configuration via Environment Variables:**
```bash
# Enable command blocking
export VEXIS_ENABLE_COMMAND_BLOCKING=true

# Enable confirmation prompts
export VEXIS_ENABLE_CONFIRMATION_PROMPTS=true

# Enable sudo warnings
export VEXIS_ENABLE_SUDO_WARNING=true

# Enable shell pipe warnings
export VEXIS_ENABLE_SHELL_PIPE_WARNING=true

# Disable sandbox (if needed)
export VEXIS_ENABLE_SANDBOX=false
```

**Priority Order:**
1. Explicit code configuration
2. Environment variables (VEXIS_*)
3. config.yaml settings
4. Defaults (all disabled)

**Blocked Commands (when enabled):**
- `rm -rf /` - System destruction
- `rm -rf ~` - Home directory deletion
- `:(){ :|:& };:` - Fork bomb
- `dd if=/dev/zero of=/dev/sda` - Disk overwrite
- `mkfs.ext4 /dev/sdX` - Filesystem format
- `> /dev/sda` - Disk corruption

**Confirmation Required (when enabled):**
- `rm -rf` - Recursive delete
- `sudo` - Privilege escalation
- `curl ... | bash` - Remote code execution
- `chmod 777` - Permission changes
- `kill -9` - Force kill
- System info (`ps`, `top`, `df`)
- Network tools (`ping`, `curl`, `wget`)

#### File System Restrictions

```yaml
security:
  restricted_paths: [
    "/etc",
    "/usr/bin",
    "/System"
  ]
```

**Protected Directories:**
- System configuration files
- Binary directories
- Critical system files

### Model Configuration

#### Ollama Models

```yaml
models:
  ollama:
    primary: "gemini-3-flash-preview:latest"
    fallbacks: [
      "gemma3:4b",
      "qwen2.5:3b",
      "mistral:7b"
    ]
    temperature: 1.0
    max_tokens: 5000
    top_p: 0.9
```

**Model Parameters:**

- **temperature**: Randomness (0.0-2.0, lower = more deterministic)
- **max_tokens**: Maximum response length
- **top_p**: Nucleus sampling (0.0-1.0)
- **top_k**: Top-k sampling (1-100)

#### Recommended Models

**For General Use:**
- `gemini-3-flash-preview:latest` - Fast, capable (if available)
- `gemma3:4b` - Good balance of speed and capability
- `qwen2.5:3b` - Lightweight, fast
- `deepseek-r1:7b` - Strong reasoning capabilities

**For Complex Tasks:**
- `qwen2.5:14b` - More capable, still reasonable size
- `qwen3:8b` - Latest generation with enhanced capabilities
- `llama3.2:3b` - Good for reasoning
- `mistral:7b` - General purpose

**For Coding:**
- `qwen2.5-coder:3b` - Code-specific tasks
- `deepseek-coder:1.3b` - Lightweight coding
- `codegemma:7b` - Code generation

### User Interface Configuration

#### Display Settings

```yaml
ui:
  color_output: true
  show_progress: true
  show_timing: true
  menu_style: "yellow"
  auto_select: false
```

**UI Options:**

- **color_output**: Colored terminal output
- **show_progress**: Progress bars for long operations
- **show_timing**: Show execution times
- **menu_style**: Menu appearance style

#### Interaction Settings

```yaml
ui:
  confirm_dangerous: true
  show_suggestions: true
```

**Interaction Features:**

- **Danger Confirmation**: Prompt for risky operations
- **Suggestions**: Show alternative commands
- **Auto-completion**: Suggest command completions

## Configuration Management

### Validation

#### Check Configuration Validity

```bash
python3 run.py --check-config
```

**Validation Checks:**
- YAML syntax correctness
- Required fields presence
- Value ranges and types
- File permissions
- Network connectivity

#### Manual Validation

```python
import yaml
from pathlib import Path

def validate_config(config_path="config.yaml"):
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Check required fields
        required_fields = ['api', 'execution', 'logging']
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Missing required field: {field}")
        
        # Validate API configuration
        api_config = config['api']
        if api_config['preferred_provider'] not in ['ollama', 'google']:
            raise ValueError("Invalid preferred_provider")
        
        print("✅ Configuration is valid")
        return True
        
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False
```

### Environment-Specific Configuration

#### Development Environment

```yaml
# config.dev.yaml
api:
  preferred_provider: "ollama"
  timeout: 60

logging:
  level: "DEBUG"
  console: true

development:
  debug_mode: true
  verbose_errors: true
```

#### Production Environment

```yaml
# config.prod.yaml
api:
  preferred_provider: "google"
  timeout: 180

logging:
  level: "INFO"
  file: "/var/log/vexis/vexis.log"

security:
  # Enable command safety features for production
  enable_command_blocking: true
  enable_confirmation_prompts: true
  enable_sudo_warning: true
  enable_shell_pipe_warning: true
  enable_sandbox: true
```

#### Testing Environment

```yaml
# config.test.yaml
api:
  preferred_provider: "ollama"
  timeout: 30

development:
  test_mode: true
  mock_responses: true

execution:
  dry_run: true
```

### Configuration Loading

#### Priority Order

1. **Command Line Arguments** (highest priority)
2. **Environment Variables**
3. **Configuration File**
4. **Default Values** (lowest priority)

#### Loading Different Configs

```bash
# Use specific config file
python3 run.py --config config.dev.yaml "instruction"

# Use environment
export VEXIS_CONFIG=config.prod.yaml
python3 run.py "instruction"
```

## Advanced Configuration

### Custom Model Settings

#### Task-Specific Models

```yaml
models:
  task_specific:
    code_generation:
      model: "codellama:7b"
      temperature: 0.1
      max_tokens: 4000
    
    data_analysis:
      model: "qwen2.5:7b"
      temperature: 0.3
      max_tokens: 3000
    
    creative_writing:
      model: "gemini-3-flash-preview"
      temperature: 1.2
      max_tokens: 6000
```

### Plugin Configuration

#### Custom Plugins

```yaml
plugins:
  enabled: true
  directory: "plugins"
  
  custom_commands:
    - name: "docker_manager"
      path: "plugins/docker_manager.py"
      config:
        default_registry: "docker.io"
        
  custom_models:
    - name: "local_llm"
      provider: "custom"
      endpoint: "http://localhost:8080"
      model: "custom-model"
```

### Monitoring Configuration

#### Performance Monitoring

```yaml
monitoring:
  enabled: true
  metrics_port: 9090
  
  metrics:
    - response_time
    - success_rate
    - memory_usage
    - cpu_usage
    
  alerts:
    high_latency: 5.0      # seconds
    error_rate: 0.1        # 10%
    memory_usage: 0.8      # 80%
```

#### Health Checks

```yaml
health_checks:
  enabled: true
  interval: 60            # seconds
  
  checks:
    - name: "ollama_service"
      type: "http"
      endpoint: "http://localhost:11434/api/tags"
      
    - name: "model_availability"
      type: "command"
      command: "ollama list"
      
    - name: "disk_space"
      type: "system"
      threshold: 0.9        # 90%
```

## Troubleshooting Configuration

### Common Issues

#### YAML Syntax Errors

**Problem**: Invalid YAML syntax
**Solution**: Use YAML validator or linter

```bash
# Validate YAML
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Use yamllint (if installed)
yamllint config.yaml
```

#### Missing Required Fields

**Problem**: Configuration missing required fields
**Solution**: Copy from template

```bash
# Reset from template
cp config.yaml.example config.yaml
```

#### Permission Issues

**Problem**: Cannot read/write config file
**Solution**: Fix file permissions

```bash
# Fix permissions
chmod 644 config.yaml
chown $USER:$USER config.yaml
```

### Debug Configuration

#### Enable Debug Mode

```yaml
development:
  debug_mode: true
  verbose_errors: true
  stack_traces: true
```

#### Test Configuration

```bash
# Test with debug
python3 run.py --debug "test instruction"

# Check configuration
python3 run.py --check-config

# Test providers
python3 run.py --test-providers
```

## Best Practices

### Security

1. **Use Environment Variables** for API keys
2. **Restrict File Access** with proper permissions
3. **Enable Safety Mode** in production
4. **Regularly Rotate** API keys

### Performance

1. **Choose Appropriate Models** for tasks
2. **Configure Timeouts** appropriately
3. **Enable Parallel Execution** for independent tasks
4. **Monitor Resource Usage**

### Maintenance

1. **Backup Configuration** regularly
2. **Version Control** config files
3. **Document Changes** with comments
4. **Test Changes** before deployment

### Environment Management

1. **Separate Configs** for different environments
2. **Use Templates** for consistency
3. **Validate Configs** before use
4. **Monitor Configuration** changes

This comprehensive configuration system provides flexibility while maintaining security and performance standards for VEXIS-CLI deployments.
