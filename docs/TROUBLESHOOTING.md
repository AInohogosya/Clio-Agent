# Troubleshooting Guide

## Overview

This comprehensive troubleshooting guide covers common issues, their causes, and step-by-step solutions for VEXIS-CLI.

## Quick Diagnosis

### Symptom Checker

| Symptom | Likely Cause | Quick Fix |
|---------|--------------|-----------|
| **"permission denied"** | Insufficient file permissions | Check user permissions |
| **"model not found"** | Model not installed/pulled | Install required model |
| **"connection refused"** | Ollama service not running | Start Ollama service |
| **"command not found"** | Binary not in PATH | Install missing dependency |
| **"timeout"** | Network or model response issues | Increase timeout or retry |

## Common Issues

### 1. Permission Errors

#### macOS Permission Issues

**Symptoms:**
```
Permission denied while accessing ~/.ollama
Operation not permitted
```

**Causes:**
- Full Disk Access not granted to Terminal
- Restricted file system permissions
- SIP (System Integrity Protection) restrictions

**Solutions:**

1. **Grant Full Disk Access:**
   ```bash
   # System Preferences → Security & Privacy → Full Disk Access
   # Add Terminal and any terminal apps you use
   ```

2. **Check File Permissions:**
   ```bash
   ls -la ~/.ollama
   chmod 755 ~/.ollama
   ```

3. **Verify SIP Status:**
   ```bash
   csrutil status
   # If disabled, consider re-enabling for security
   ```

#### Linux Permission Issues

**Symptoms:**
```
Permission denied accessing /usr/local/bin
Cannot create directory: Permission denied
```

**Causes:**
- User not in appropriate groups
- Insufficient directory permissions
- SELinux restrictions

**Solutions:**

1. **Add User to Groups:**
   ```bash
   sudo usermod -a -G docker,adm $USER
   # Log out and back in
   ```

2. **Fix Directory Permissions:**
   ```bash
   sudo chown -R $USER:$USER ~/.ollama
   sudo chmod 755 ~/.ollama
   ```

3. **Check SELinux:**
   ```bash
   getenforce
   # If enforcing, temporarily disable for testing:
   sudo setenforce 0
   ```

#### Windows Permission Issues

**Symptoms:**
```
Access is denied
Administrator privileges required
```

**Causes:**
- Insufficient user privileges
- UAC (User Account Control) restrictions
- Antivirus software blocking

**Solutions:**

1. **Run as Administrator:**
   ```cmd
   # Right-click Command Prompt → "Run as administrator"
   ```

2. **Check UAC Settings:**
   ```cmd
   # Control Panel → User Accounts → Change User Account Control settings
   ```

3. **Configure Antivirus:**
   - Add VEXIS-CLI-2.1 to antivirus exclusions
   - Allow Terminal/PowerShell execution

### 2. Ollama Model Issues

#### Model Not Found

**Symptoms:**
```
model 'gemini-3-flash-preview' not found
unknown model: llama3.2:3b
```

**Causes:**
- Model not installed locally
- Incorrect model name format
- Model corrupted during download

**Solutions:**

1. **List Available Models:**
   ```bash
   ollama list
   ```

2. **Pull Required Model:**
   ```bash
   ollama pull gemma3:4b
   ollama pull qwen2.5:3b
   ollama pull deepseek-r1:7b
   ```

3. **Try Alternative Models:**
   🔧 Resolution Steps:
   1. List available models: ollama list
   2. Pull the required model: ollama pull <model-name>
   3. Try alternative models:
      • gemma3:4b
      • qwen2.5:3b
      • deepseek-r1:7b
      • qwen3:8b
   
💡 Recommended Models:
   ollama pull gemma3:4b
   ollama pull qwen2.5:3b
   ollama pull deepseek-r1:7b

4. **Check Model Status:**
   ```bash
   ollama show gemma3:4b
   ```

#### Model Download Failures

**Symptoms:**
```
download failed: connection timeout
pull interrupted: network error
```

**Causes:**
- Network connectivity issues
- Firewall blocking downloads
- Insufficient disk space

**Solutions:**

1. **Check Network Connection:**
   ```bash
   ping ollama.ai
   curl -I https://ollama.ai
   ```

2. **Configure Firewall:**
   ```bash
   # Allow outbound connections on port 443
   sudo ufw allow out 443
   ```

3. **Check Disk Space:**
   ```bash
   df -h ~/.ollama
   # Ensure at least 10GB free space
   ```

4. **Retry with Timeout:**
   ```bash
   timeout 300 ollama pull gemma3:4b
   ```

### 3. Connection Issues

#### Ollama Service Not Running

**Symptoms:**
```
connection refused
cannot connect to localhost:11434
```

**Causes:**
- Ollama service not started
- Service crashed
- Port conflict

**Solutions:**

1. **Start Ollama Service:**
   ```bash
   # macOS/Linux
   ollama serve
   
   # Windows (as Administrator)
   ollama.exe serve
   ```

2. **Check Service Status:**
   ```bash
   ps aux | grep ollama
   netstat -tlnp | grep 11434
   ```

3. **Restart Service:**
   ```bash
   # Kill existing process
   pkill ollama
   
   # Start fresh
   ollama serve
   ```

4. **Check Port Availability:**
   ```bash
   lsof -i :11434
   # If port in use, kill process or change port
   ```

#### API Connection Issues

**Symptoms:**
```
HTTPConnectionPool: Connection failed
SSL verification failed
```

**Causes:**
- Network connectivity problems
- SSL/TLS certificate issues
- Proxy configuration problems

**Solutions:**

1. **Test Basic Connectivity:**
   ```bash
   curl -v http://localhost:11434/api/tags
   ```

2. **Check SSL Certificates:**
   ```bash
   # Update certificates (macOS)
   brew update && brew upgrade
   
   # Update certificates (Linux)
   sudo update-ca-certificates
   ```

3. **Configure Proxy:**
   ```bash
   export HTTP_PROXY=http://proxy.example.com:8080
   export HTTPS_PROXY=http://proxy.example.com:8080
   ```

### 4. Installation Issues

#### Python Dependencies

**Symptoms:**
```
ModuleNotFoundError: No module named 'xxx'
ImportError: cannot import name 'yyy'
```

**Causes:**
- Missing Python packages
- Version incompatibilities
- Virtual environment issues

**Solutions:**

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

2. **Check Python Version:**
   ```bash
   python3 --version
   # Ensure Python 3.9+
   ```

3. **Use Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/macOS
   # or
   venv\Scripts\activate     # Windows
   pip install -r requirements.txt
   ```

4. **Upgrade pip:**
   ```bash
   pip install --upgrade pip setuptools wheel
   ```

#### System Dependencies

**Symptoms:**
```
Command not found: curl
git: command not found
```

**Causes:**
- Missing system utilities
- PATH configuration issues
- Package manager problems

**Solutions:**

1. **Install Basic Tools:**
   ```bash
   # macOS
   xcode-select --install
   brew install curl git
   
   # Ubuntu/Debian
   sudo apt update
   sudo apt install curl git wget
   
   # CentOS/RHEL
   sudo yum install curl git wget
   ```

2. **Check PATH:**
   ```bash
   echo $PATH
   # Ensure /usr/local/bin, /usr/bin are included
   ```

### 5. Configuration Issues

#### Invalid Configuration

**Symptoms:**
```
Config validation failed
Invalid YAML syntax
Missing required configuration
```

**Causes:**
- YAML syntax errors
- Missing required fields
- Invalid values

**Solutions:**

1. **Validate YAML Syntax:**
   ```bash
   python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```

2. **Reset Configuration:**
   ```bash
   cp config.yaml.example config.yaml
   # Edit with correct values
   ```

3. **Check Required Fields:**
   ```yaml
   api:
     preferred_provider: "ollama"  # Required
     local_endpoint: "http://localhost:11434"  # Required
   ```

#### API Key Issues

**Symptoms:**
```
Invalid API key
Authentication failed
API quota exceeded
```

**Causes:**
- Invalid or expired API key
- Incorrect key format
- Quota limitations

**Solutions:**

1. **Verify API Key:**
   ```bash
   # Get key from Google AI Studio
   curl -H "Content-Type: application/json" \
        -H "Authorization: Bearer YOUR_API_KEY" \
        https://generativelanguage.googleapis.com/v1/models
   ```

2. **Update Configuration:**
   ```yaml
   api:
     google_api_key: "your-actual-api-key"
   ```

3. **Check Quota:**
   - Visit Google AI Studio
   - Check usage and limits
   - Request quota increase if needed

### 6. Performance Issues

#### Slow Response Times

**Symptoms:**
```
Command taking > 30 seconds
Timeout errors
High CPU usage
```

**Causes:**
- Model loading time
- Network latency
- Resource constraints

**Solutions:**

1. **Optimize Model Selection:**
   ```bash
   # Use smaller models for faster response
   ollama pull qwen2.5:1.5b
   ollama pull gemma3:2b
   ```

2. **Increase Timeout:**
   ```yaml
   api:
     timeout: 300  # Increase to 5 minutes
   ```

3. **Monitor Resources:**
   ```bash
   top -p $(pgrep ollama)
   htop
   iostat -x 1
   ```

4. **Use Local Models:**
   ```yaml
   api:
     preferred_provider: "ollama"  # Faster than cloud
   ```

#### Memory Issues

**Symptoms:**
```
Out of memory errors
System becomes unresponsive
Swap usage high
```

**Causes:**
- Large model loading
- Memory leaks
- Insufficient RAM

**Solutions:**

1. **Monitor Memory Usage:**
   ```bash
   free -h  # Linux
   vm_stat  # macOS
   ```

2. **Use Smaller Models:**
   ```bash
   ollama pull qwen2.5:1.5b
   ollama pull gemma3:2b
   ollama pull deepseek-r1:1.5b
   ```

3. **Increase Swap Space:**
   ```bash
   # Linux
   sudo fallocate -l 8G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

4. **Restart Services:**
   ```bash
   # Restart Ollama to clear memory
   pkill ollama
   ollama serve
   ```

## Advanced Troubleshooting

### Debug Mode

Enable comprehensive debugging:

```bash
python3 run.py "your instruction" --debug
```

**Debug Information:**
- Detailed execution logs
- Model response times
- Error stack traces
- Configuration validation

### Log Analysis

#### System Logs

```bash
# View VEXIS logs
tail -f logs/vexis.log

# View Ollama logs
tail -f ~/.ollama/logs/ollama.log

# View system logs
journalctl -u ollama -f  # Linux
log stream --predicate 'process == "ollama"'  # macOS
```

#### Error Patterns

```bash
# Search for common errors
grep -i "error\|exception\|failed" logs/vexis.log

# Search for timeouts
grep -i "timeout" logs/vexis.log

# Search for permission issues
grep -i "permission\|denied" logs/vexis.log
```

### Health Checks

#### System Health

```bash
#!/bin/bash
# health_check.sh

echo "=== VEXIS-CLI-2.1 Health Check ==="

# Check Python
python3 --version
echo "Python: OK"

# Check Ollama
if curl -s http://localhost:11434/api/tags > /dev/null; then
    echo "Ollama: OK"
else
    echo "Ollama: FAILED"
fi

# Check Models
if ollama list | grep -q .; then
    echo "Models: OK"
else
    echo "Models: NONE FOUND"
fi

# Check Config
if python3 -c "import yaml; yaml.safe_load(open('config.yaml'))" 2>/dev/null; then
    echo "Config: OK"
else
    echo "Config: INVALID"
fi

# Check Permissions
if [ -w ~/.ollama ]; then
    echo "Permissions: OK"
else
    echo "Permissions: CHECK NEEDED"
fi

echo "=== Health Check Complete ==="
```

#### Model Health

```bash
#!/bin/bash
# model_health.sh

echo "=== Model Health Check ==="

# Test model availability
models=("gemini-3-flash-preview" "gemma3:4b" "qwen2.5:3b")

for model in "${models[@]}"; do
    echo "Testing $model..."
    if timeout 30 ollama run "$model" "test" 2>/dev/null; then
        echo "✅ $model: OK"
    else
        echo "❌ $model: FAILED"
    fi
done

echo "=== Model Health Complete ==="
```

## Recovery Procedures

### Complete System Reset

When all else fails, perform a clean reset:

```bash
#!/bin/bash
# reset_system.sh

echo "⚠️  This will reset VEXIS-CLI-2.2 completely"
read -p "Continue? (y/N): " confirm

if [[ $confirm == "y" ]]; then
    echo "Stopping services..."
    pkill ollama
    
    echo "Backing up configuration..."
    cp config.yaml config.yaml.backup
    
    echo "Removing Ollama data..."
    rm -rf ~/.ollama
    
    echo "Reinstalling dependencies..."
    pip install -r requirements.txt --force-reinstall
    
    echo "Reset complete. Please:"
    echo "1. Start Ollama: ollama serve"
    echo "2. Pull models: ollama pull gemini-3-flash-preview"
    echo "3. Test: python3 run.py 'hello'"
fi
```

### Configuration Recovery

```bash
#!/bin/bash
# recover_config.sh

echo "=== Configuration Recovery ==="

# Create fresh config from template
if [ -f config.yaml.example ]; then
    cp config.yaml.example config.yaml
    echo "✅ Configuration reset from template"
else
    echo "❌ Template not found"
fi

# Validate new config
if python3 -c "import yaml; yaml.safe_load(open('config.yaml'))" 2>/dev/null; then
    echo "✅ Configuration valid"
else
    echo "❌ Configuration invalid"
fi
```

## Prevention Strategies

### Regular Maintenance

```bash
#!/bin/bash
# maintenance.sh

echo "=== VEXIS-CLI-2.2 Maintenance ==="

# Update models
echo "Updating models..."
ollama pull gemini-3-flash-preview:latest

# Clean old logs
echo "Cleaning logs..."
find logs/ -name "*.log" -mtime +7 -delete

# Check disk space
echo "Checking disk space..."
df -h ~/.ollama

# Verify configuration
echo "Verifying configuration..."
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"

echo "=== Maintenance Complete ==="
```

### Monitoring Setup

```bash
#!/bin/bash
# monitor.sh

# Continuous monitoring
while true; do
    if ! curl -s http://localhost:11434/api/tags > /dev/null; then
        echo "⚠️  Ollama service down - restarting..."
        pkill ollama
        ollama serve &
    fi
    
    if [ $(df ~/.ollama | awk 'NR==2 {print $5}' | sed 's/%//') -gt 90 ]; then
        echo "⚠️  Disk space low - cleaning..."
        ollama rm $(ollama list | tail -n +2 | awk '{print $1}' | tail -1)
    fi
    
    sleep 60
done
```

## Support Resources

### Getting Help

1. **Check Logs First**: Always check logs before reporting issues
2. **Run Health Check**: Use the provided health check scripts
3. **Document the Issue**: Include error messages, steps to reproduce
4. **Provide System Info**: Include OS, Python version, model versions

### Community Support

- **GitHub Issues**: Report bugs and request features
- **Documentation**: Check docs/ directory for detailed guides
- **Examples**: Review example_usage.py for common patterns

### Escalation

If issues persist after troubleshooting:

1. **Collect Diagnostic Information**:
   ```bash
   python3 run.py "test" --debug > debug.log 2>&1
   ```

2. **Create Issue Report**:
   - Include debug.log
   - Describe expected vs actual behavior
   - Include system information

3. **Contact Support**:
   - GitHub Issues for public tracking
   - Direct contact for critical issues
