# Error Handling Guide

## Overview

VEXIS-CLI includes comprehensive error handling with user-friendly guidance, automatic recovery mechanisms, and detailed error reporting. This guide covers all error types, handling strategies, and troubleshooting approaches.

## Error Handling Architecture

### Error Processing Pipeline

```
Error Event
    │
    ▼
┌─────────────────┐
│ Error Detection │ ──► Identify error type and context
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Error Classification│ ──► Categorize by severity and type
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Recovery Engine │ ──► Attempt automatic recovery
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ User Guidance   │ ──► Provide actionable instructions
└─────────────────┘
```

### Error Categories

#### 1. Permission Errors
- **File System**: Access denied, insufficient privileges
- **System**: Administrative rights required
- **Network**: Firewall blocking, port restrictions

#### 2. Model Errors
- **Not Found**: Model not installed or incorrect name
- **Incompatible**: Model version or format issues
- **Corrupted**: Damaged model files

#### 3. Connection Errors
- **Service Unavailable**: Ollama not running
- **Network Issues**: Connection timeout, DNS problems
- **API Errors**: Authentication, rate limiting

#### 4. Execution Errors
- **Command Failed**: Invalid commands, syntax errors
- **Resource Issues**: Memory, CPU, disk space
- **Dependency Missing**: Required tools not installed

#### 5. Configuration Errors
- **Invalid YAML**: Syntax errors, missing fields
- **Wrong Values**: Invalid settings, out of range
- **Environment Issues**: Missing variables, wrong paths

## Ollama Error Handling

### Enhanced Error Handler

The `OllamaErrorHandler` provides user-friendly guidance for common issues:

```python
from ai_agent.utils.ollama_error_handler import OllamaErrorHandler

error_handler = OllamaErrorHandler()
error, should_retry = error_handler.handle_error(
    error_message="permission denied",
    context={"operation": "pull_model"},
    display_to_user=True
)
```

### Permission Error Handling

#### macOS Full Disk Access

**Error Pattern:**
```
permission denied while accessing ~/.ollama
Operation not permitted
```

**Automatic Detection:**
```python
def _detect_permission_error(self, error_msg: str) -> bool:
    patterns = [
        r'permission denied',
        r'operation not permitted',
        r'access denied'
    ]
    return any(re.search(pattern, error_msg, re.IGNORECASE) for pattern in patterns)
```

**User Guidance:**
```bash
🚫 Permission Error Detected
🔧 Resolution Steps:
   1. System Preferences → Security & Privacy → Full Disk Access
   2. Click the lock to unlock (enter password)
   3. Add Terminal.app to the allowed applications
   4. Restart Terminal and try again
   
⚡ Quick Fix:
   sudo chown -R $USER:$USER ~/.ollama
   chmod 755 ~/.ollama
```

#### Linux Permission Issues

**Error Pattern:**
```
Permission denied accessing /usr/local/bin
Cannot create directory: Permission denied
```

**User Guidance:**
```bash
🚫 Linux Permission Error
🔧 Resolution Steps:
   1. Add user to docker group: sudo usermod -a -G docker $USER
   2. Fix ownership: sudo chown -R $USER:$USER ~/.ollama
   3. Set permissions: sudo chmod 755 ~/.ollama
   4. Log out and log back in
   
⚡ Quick Fix:
   sudo chmod -R 755 ~/.ollama
```

#### Windows Admin Rights

**Error Pattern:**
```
Access is denied
Administrator privileges required
```

**User Guidance:**
```bash
🚫 Windows Permission Error
🔧 Resolution Steps:
   1. Right-click Command Prompt
   2. Select "Run as administrator"
   3. Confirm UAC prompt
   4. Try the command again
   
⚡ Quick Fix:
   # Run as Administrator in PowerShell
   Start-Process powershell -Verb RunAs
```

### Model Error Handling

#### Model Not Found

**Error Pattern:**
```
model 'invalid-model' not found
unknown model: xyz
```

**Automatic Detection:**
```python
def _detect_model_not_found(self, error_msg: str) -> bool:
    patterns = [
        r'model .* not found',
        r'unknown model',
        r'model .* does not exist'
    ]
    return any(re.search(pattern, error_msg, re.IGNORECASE) for pattern in patterns)
```

**User Guidance:**
```bash
🤖 Model Not Found
🔧 Resolution Steps:
   1. List available models: ollama list
   2. Pull the required model: ollama pull <model-name>
   3. Try alternative models:
      • gemma3:4b
      • qwen2.5:3b
      • mistral:7b
   
💡 Recommended Models:
   ollama pull gemini-3-flash-preview:latest
   ollama pull gemma3:4b
```

#### Model Download Failures

**Error Pattern:**
```
download failed: connection timeout
pull interrupted: network error
```

**User Guidance:**
```bash
📥 Model Download Failed
🔧 Resolution Steps:
   1. Check internet connection: ping ollama.ai
   2. Verify firewall allows port 443
   3. Check disk space: df -h ~/.ollama
   4. Retry with timeout:
      timeout 300 ollama pull <model-name>
   
⚡ Alternative:
   # Use a different mirror or network
   export OLLAMA_HOST=http://localhost:11434
   ollama pull <model-name>
```

### Connection Error Handling

#### Service Not Running

**Error Pattern:**
```
connection refused
cannot connect to localhost:11434
```

**User Guidance:**
```bash
🔌 Connection Error
🔧 Resolution Steps:
   1. Start Ollama service: ollama serve
   2. Check if running: ps aux | grep ollama
   3. Verify port: netstat -tlnp | grep 11434
   4. Restart if needed:
      pkill ollama && ollama serve
   
⚡ Quick Start:
   # In one terminal
   ollama serve
   
   # In another terminal
   python3 run.py "your instruction"
```

#### API Connection Issues

**Error Pattern:**
```
HTTPConnectionPool: Connection failed
SSL verification failed
```

**User Guidance:**
```bash
🌐 API Connection Error
🔧 Resolution Steps:
   1. Test basic connectivity:
      curl -v http://localhost:11434/api/tags
   2. Check SSL certificates:
      # Update system certificates
   3. Configure proxy if needed:
      export HTTP_PROXY=http://proxy:8080
      export HTTPS_PROXY=http://proxy:8080
   
⚡ Debug Mode:
   python3 run.py "test" --debug
```

### Installation Error Handling

#### Command Not Found

**Error Pattern:**
```
ollama: command not found
python3: command not found
```

**User Guidance:**
```bash
📦 Installation Error
🔧 Resolution Steps:
   1. Install Ollama:
      # macOS
      brew install ollama
      
      # Linux
      curl -fsSL https://ollama.ai/install.sh | sh
      
      # Windows
      # Download from ollama.ai
   
   2. Verify installation:
      which ollama
      ollama --version
   
   3. Add to PATH if needed:
      export PATH=$PATH:/usr/local/bin
```

#### Python Dependencies

**Error Pattern:**
```
ModuleNotFoundError: No module named 'xxx'
ImportError: cannot import name 'yyy'
```

**User Guidance:**
```bash
🐍 Python Dependency Error
🔧 Resolution Steps:
   1. Install requirements:
      pip install -r requirements.txt
   
   2. Use virtual environment:
      python3 -m venv venv
      source venv/bin/activate  # Linux/macOS
      venv\Scripts\activate     # Windows
      pip install -r requirements.txt
   
   3. Upgrade pip:
      pip install --upgrade pip setuptools wheel
   
⚡ Quick Fix:
   pip install -e .
```

## Google Gemini Error Handling

### API Key Issues

#### Invalid API Key

**Error Pattern:**
```
Invalid API key
Authentication failed
401 Unauthorized
```

**User Guidance:**
```bash
🔑 API Key Error
🔧 Resolution Steps:
   1. Get API key from Google AI Studio:
      https://aistudio.google.com
   
   2. Update configuration:
      # In config.yaml
      api:
        google_api_key: "your-actual-api-key"
   
   3. Test API key:
      curl -H "Authorization: Bearer YOUR_API_KEY" \
           https://generativelanguage.googleapis.com/v1/models
   
⚡ Environment Variable:
   export GOOGLE_API_KEY="your-api-key"
```

#### Quota Exceeded

**Error Pattern:**
```
API quota exceeded
Rate limit exceeded
429 Too Many Requests
```

**User Guidance:**
```bash
📊 Quota Error
🔧 Resolution Steps:
   1. Check usage at Google AI Studio
   2. Request quota increase if needed
   3. Implement rate limiting:
      # In config.yaml
      api:
        rate_limit: 10  # requests per minute
   4. Use Ollama as fallback:
      api:
        preferred_provider: "ollama"
   
⚡ Immediate Fix:
   # Switch to local models
   python3 run.py "instruction" --provider ollama
```

### Model Availability

#### Model Not Available

**Error Pattern:**
```
Model 'gemini-3' not available
Unsupported model version
```

**User Guidance:**
```bash
🤖 Model Availability Error
🔧 Resolution Steps:
   1. Check available models:
      curl -H "Authorization: Bearer YOUR_API_KEY" \
           https://generativelanguage.googleapis.com/v1/models
   
   2. Use supported models:
      • gemini-1.5-flash
      • gemini-1.5-pro
   
   3. Update configuration:
      api:
        gemini_model: "gemini-1.5-flash"
   
⚡ Alternative:
   # Use Ollama instead
   python3 run.py "instruction" --provider ollama
```

## Configuration Error Handling

### YAML Syntax Errors

#### Invalid YAML

**Error Pattern:**
```
yaml.scanner.ScannerError
invalid YAML syntax
mapping values are not allowed here
```

**User Guidance:**
```bash
⚙️ YAML Syntax Error
🔧 Resolution Steps:
   1. Validate YAML syntax:
      python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
   
   2. Check common issues:
      • Proper indentation (2 spaces)
      • Quotes around string values
      • Colons after keys
   
   3. Reset from template:
      cp config.yaml.example config.yaml
   
⚡ Quick Validation:
   yamllint config.yaml  # if yamllint installed
```

### Missing Configuration

#### Required Fields Missing

**Error Pattern:**
```
Missing required configuration: api.preferred_provider
Config validation failed
```

**User Guidance:**
```bash
⚙️ Configuration Missing
🔧 Resolution Steps:
   1. Ensure required fields exist:
      api:
        preferred_provider: "ollama"
        local_endpoint: "http://localhost:11434"
   
   2. Check config template:
      cat config.yaml.example
   
   3. Validate configuration:
      python3 run.py --check-config
   
⚡ Reset Config:
   cp config.yaml.example config.yaml
```

## Execution Error Handling

### Command Failures

#### Invalid Commands

**Error Pattern:**
```
Command failed with exit code 127
Invalid command syntax
Operation not permitted
```

**User Guidance:**
```bash
💻 Command Execution Error
🔧 Resolution Steps:
   1. Verify command syntax:
      # Test command manually first
      ls -la /tmp
   
   2. Check permissions:
      # Ensure directory is writable
      ls -ld /target/directory
   
   3. Use debug mode:
      python3 run.py "instruction" --debug
   
⚡ Safe Mode:
   python3 run.py "instruction" --dry-run
```

### Resource Issues

#### Memory Errors

**Error Pattern:**
```
MemoryError
Out of memory
Killed
```

**User Guidance:**
```bash
🧠 Memory Error
🔧 Resolution Steps:
   1. Check available memory:
      free -h  # Linux
      vm_stat  # macOS
   
   2. Use smaller models:
      ollama pull qwen2.5:1.5b
      ollama pull gemma3:2b
   
   3. Increase swap space:
      # Linux
      sudo fallocate -l 4G /swapfile
      sudo chmod 600 /swapfile
      sudo swapon /swapfile
   
⚡ Quick Fix:
   # Restart Ollama to clear memory
   pkill ollama && ollama serve
```

## Recovery Mechanisms

### Automatic Retry Logic

```python
class RetryHandler:
    def __init__(self, max_retries=3, backoff_factor=2):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
    
    def retry_with_backoff(self, func, *args, **kwargs):
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise e
                
                wait_time = self.backoff_factor ** attempt
                time.sleep(wait_time)
```

### Fallback Strategies

#### Provider Fallback

```python
def get_provider_fallback(primary_provider, instruction):
    """Try alternative providers if primary fails"""
    providers = ["ollama", "google"]
    
    for provider in providers:
        if provider != primary_provider:
            try:
                return execute_with_provider(provider, instruction)
            except Exception:
                continue
    
    raise Exception("All providers failed")
```

#### Model Fallback

```python
def get_model_fallback(primary_model, task_type):
    """Try alternative models if primary fails"""
    fallback_models = {
        "generation": ["gemma3:4b", "qwen2.5:3b", "mistral:7b"],
        "parsing": ["qwen2.5:3b", "gemma3:2b"],
        "analysis": ["gemini-3-flash-preview", "gemma3:4b"]
    }
    
    for model in fallback_models.get(task_type, []):
        try:
            return execute_with_model(model)
        except Exception:
            continue
    
    raise Exception("All models failed")
```

## Error Reporting

### Structured Error Information

```python
@dataclass
class ErrorInfo:
    error_type: str
    severity: str  # "low", "medium", "high", "critical"
    message: str
    context: Dict[str, Any]
    suggestions: List[str]
    should_retry: bool
    recovery_actions: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.error_type,
            "severity": self.severity,
            "message": self.message,
            "context": self.context,
            "suggestions": self.suggestions,
            "should_retry": self.should_retry,
            "recovery_actions": self.recovery_actions
        }
```

### Error Logging

```python
class ErrorLogger:
    def __init__(self, log_file="logs/errors.log"):
        self.log_file = log_file
    
    def log_error(self, error_info: ErrorInfo):
        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "error": error_info.to_dict()
        }
        
        with open(self.log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
```

## User Interface Integration

### Error Display

```python
def display_error_to_user(error_info: ErrorInfo):
    """Display error information in user-friendly format"""
    
    # Error header
    print(f"🚫 {error_info.error_type.title()} Error")
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

### Interactive Recovery

```python
def interactive_recovery(error_info: ErrorInfo):
    """Allow user to choose recovery action"""
    
    if not error_info.recovery_actions:
        return False
    
    print("\n🛠️  Choose recovery action:")
    for i, action in enumerate(error_info.recovery_actions, 1):
        print(f"   {i}. {action}")
    print(f"   0. Skip and continue")
    
    choice = input("\nEnter choice (0-{}): ".format(len(error_info.recovery_actions)))
    
    try:
        choice_num = int(choice)
        if choice_num == 0:
            return False
        elif 1 <= choice_num <= len(error_info.recovery_actions):
            return execute_recovery_action(choice_num - 1)
    except ValueError:
        pass
    
    return False
```

## Testing Error Handling

### Error Simulation

```python
class ErrorSimulator:
    def __init__(self):
        self.error_scenarios = {
            "permission_denied": PermissionError("Access denied"),
            "model_not_found": ValueError("Model not found"),
            "connection_error": ConnectionError("Connection refused"),
            "timeout_error": TimeoutError("Operation timed out")
        }
    
    def simulate_error(self, scenario: str):
        """Simulate specific error for testing"""
        if scenario in self.error_scenarios:
            raise self.error_scenarios[scenario]
        else:
            raise ValueError(f"Unknown error scenario: {scenario}")
```

### Error Handler Testing

```python
def test_error_handler():
    """Test error handling mechanisms"""
    
    handler = OllamaErrorHandler()
    simulator = ErrorSimulator()
    
    test_cases = [
        ("permission_denied", True),   # Should provide guidance
        ("model_not_found", True),     # Should provide suggestions
        ("connection_error", True),     # Should provide recovery steps
        ("timeout_error", True)         # Should suggest retry
    ]
    
    for scenario, should_handle in test_cases:
        try:
            simulator.simulate_error(scenario)
        except Exception as e:
            error_info = handler.handle_error(str(e), {"scenario": scenario})
            
            assert error_info is not None, f"Handler failed for {scenario}"
            assert len(error_info.suggestions) > 0, f"No suggestions for {scenario}"
            
            print(f"✅ {scenario}: Handled correctly")
```

## Best Practices

### Error Prevention

1. **Validate Input**: Check user input before processing
2. **Verify Configuration**: Validate settings at startup
3. **Check Dependencies**: Ensure all requirements are met
4. **Monitor Resources**: Watch memory, disk, and network usage

### Graceful Degradation

1. **Fallback Options**: Provide alternative solutions
2. **Partial Functionality**: Continue with limited features
3. **User Choice**: Let users decide how to proceed
4. **Clear Communication**: Explain what's happening

### User Experience

1. **Clear Messages**: Use simple, actionable language
2. **Visual Indicators**: Use icons and colors for clarity
3. **Progress Feedback**: Show what's being done
4. **Recovery Options**: Provide clear next steps

This comprehensive error handling system ensures that VEXIS-CLI-2.2 provides a robust, user-friendly experience even when things go wrong.
