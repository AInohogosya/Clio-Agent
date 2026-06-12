# Self-Healing Architecture Documentation

## Overview

This document describes the comprehensive self-healing architecture implemented in VEXIS-CLI AI Agent. The system is designed to **never stop working** - it automatically recovers from any error, crash, or failure condition.

## Architecture Layers

The self-healing system consists of multiple layers of defense:

```
┌─────────────────────────────────────────────────────────────────┐
│                    EXTERNAL MONITORING                          │
│                  (Optional: systemd, docker)                    │
├─────────────────────────────────────────────────────────────────┤
│                   ETERNAL SUPERVISOR                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  • Process-level watchdog                               │   │
│  │  • Auto-restart with exponential backoff + jitter       │   │
│  │  • State persistence for crash recovery                 │   │
│  │  • Health monitoring (disk, memory, CPU)                │   │
│  │  • Circuit breaker (prevents restart loops)             │   │
│  └─────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│                    AGENT PROCESS                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                 AUTONOMOUS LOOP ENGINE                  │   │
│  │  ┌─────────────────────────────────────────────────┐   │   │
│  │  │  • Enhanced error recovery                      │   │   │
│  │  │  • Self-healing execution                       │   │   │
│  │  │  • Provider failover with circuit breaker       │   │   │
│  │  │  • Graceful degradation                         │   │   │
│  │  │  • State persistence (exit_state.json)          │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  │  ┌─────────────────────────────────────────────────┐   │   │
│  │  │  • Resilience Engine                            │   │   │
│  │  │  • Global exception hook                        │   │   │
│  │  │  • Error classification                         │   │   │
│  │  │  • Retry with exponential backoff               │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│                    SYSTEM LEVEL                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  • Signal handlers (SIGINT, SIGTERM, SIGHUP)            │   │
│  │  • Emergency context save on crash                      │   │
│  │  • Atomic file writes (prevent corruption)              │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Layer 1: Eternal Supervisor

**File:** `src/ai_agent/utils/eternal_supervisor.py`

The Eternal Supervisor is a separate process that monitors the agent process and automatically restarts it if it crashes or hangs.

### Features

- **Process Monitoring**: Detects if the agent process has died or is hanging
- **Automatic Restart**: Restarts the agent with exponential backoff + jitter
- **Circuit Breaker**: Prevents restart loops by opening the circuit after too many failures
- **State Persistence**: Saves supervisor state for crash recovery
- **Health Monitoring**: Periodic checks of disk space, memory usage
- **Graceful Shutdown**: Properly handles SIGINT/SIGTERM for clean shutdown

### Usage

```bash
# Start with eternal supervisor (maximum resilience)
python3 run.py --supervisor "your instruction"

# Start with supervisor in Telegram mode
python3 run.py --supervisor --telegram
```

### Configuration

```python
config = EternalSupervisorConfig(
    heartbeat_interval=30.0,       # Seconds between heartbeats
    heartbeat_timeout=600.0,       # Seconds before declaring hang
    max_restarts_per_hour=10,      # Max restarts before extended cooldown
    max_restarts_per_day=50,       # Max daily restarts
    initial_restart_delay=2.0,     # Initial delay before restart
    max_restart_delay=300.0,       # Max delay (5 minutes)
    circuit_breaker_threshold=5,   # Failures before opening circuit
    circuit_breaker_timeout=60.0,   # Seconds before half-open
)
```

### State Files

The supervisor maintains several state files in `.context/`:

- `supervisor_heartbeat.json` - Current supervisor heartbeat
- `supervisor_state.json` - Supervisor state for crash recovery
- `supervisor_restarts.json` - Restart counter
- `supervisor_health.json` - Health report

## Layer 2: Enhanced Autonomous Loop Engine

**File:** `src/ai_agent/core_processing/autonomous_loop_engine.py`

The Autonomous Loop Engine has been enhanced with comprehensive error recovery capabilities.

### Error Recovery Strategies

The engine uses different recovery strategies based on error type:

| Error Type | Strategy | Behavior |
|------------|----------|----------|
| Network Error | Exponential backoff | Wait with increasing delay |
| Rate Limit | Longer backoff | Wait longer for rate limits |
| Auth Error | Provider switch | Switch to fallback provider |
| Timeout Error | Retry with backoff | Wait and retry |
| Resource Error | Cleanup + sleep | Free resources, force sleep |
| Model Error | Provider switch | Switch to fallback provider |
| Command Error | Log and continue | Skip failed command |
| Unknown Error | Adaptive | Try various strategies |

### Degraded Mode

When too many consecutive errors occur, the agent enters "degraded mode":

- Continues working with reduced functionality
- Periodically attempts to exit degraded mode
- Automatically recovers when successful iterations resume

### Self-Diagnostic

Every 50 iterations, the agent runs a self-diagnostic:

- Checks time since last successful iteration
- Monitors memory usage
- Checks disk space
- Attempts automatic cleanup if resources are low
- Exits degraded mode if conditions improve

### Heartbeat System

The agent writes heartbeats every 30 seconds:

```json
{
  "pid": 12345,
  "iteration": 42,
  "status": "alive",
  "timestamp": 1234567890.123,
  "platform": "Linux"
}
```

The supervisor monitors this heartbeat to detect hangs.

## Layer 3: Resilience Engine

**File:** `src/ai_agent/utils/resilience_engine.py`

The Resilience Engine provides the foundation for error handling throughout the system.

### Features

- **Global Exception Hook**: Catches uncaught exceptions in all threads
- **Error Classification**: Intelligently classifies errors for appropriate handling
- **Self-Healing Commands**: Auto-fixes common command errors
- **Circuit Breaker**: Prevents cascading failures
- **Telegram Notifications**: Notifies user of errors and recoveries

### Error Categories

```python
class ErrorCategory(Enum):
    TRANSIENT = "transient"           # Temporary errors, retryable
    PERMANENT = "permanent"           # Permanent errors, not retryable
    AUTHENTICATION = "authentication" # Auth errors
    RATE_LIMIT = "rate_limit"         # Rate limiting
    VALIDATION = "validation"         # Input validation
    RESOURCE = "resource"             # Resource exhaustion
    TIMEOUT = "timeout"               # Timeout errors
    EXTERNAL = "external"             # External service errors
```

## Layer 4: State Persistence

The system uses multiple state files for crash recovery:

### sleep_state.json

Created when the agent executes the `sleep` command:

```json
{
  "status": "Restarting due to execution of the sleep command",
  "goal": "...",
  "iteration_count": 42,
  "compressed_context": "...",
  "execution_log": [...],
  "timestamp": 1234567890.123,
  "auxiliary": {
    "git_diff": "...",
    "metadata": "...",
    "errors": "...",
    "log_tail_lines": 100
  }
}
```

### exit_state.json

Created when the agent exits (via `exit` command or signal):

```json
{
  "status": "Exited gracefully — context saved for next session",
  "goal": "...",
  "iteration_count": 42,
  "compressed_context": "...",
  "execution_log": [...],
  "timestamp": 1234567890.123
}
```

### Context Recovery Flow

```
Process Start
     │
     ▼
┌─────────────────────┐
│ Check for exit_state│
│ or sleep_state      │
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│ Load compressed     │
│ context             │
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│ Build resume        │
│ instruction         │
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│ Start autonomous    │
│ loop with context   │
└─────────────────────┘
```

## Layer 5: Signal Handling

The system handles various signals for graceful shutdown:

- **SIGINT** (Ctrl+C): Save context and exit
- **SIGTERM**: Save context and exit
- **SIGHUP**: Ignore (daemon mode)

### Signal Handler Implementation

```python
def _emergency_save_context(signum, frame):
    """Save context on SIGINT/SIGTERM before exiting."""
    # Save compressed context to exit_state.json
    # Then re-raise the signal
```

## Usage Examples

### Basic Usage with Supervisor

```bash
# Start agent with maximum resilience
python3 run.py --supervisor "Monitor system and report issues"

# Start in background
nohup python3 run.py --supervisor "task" > agent.log 2>&1 &

# Start with custom supervisor config
python3 supervisor.py --heartbeat-timeout 300 --max-restarts-per-hour 20
```

### Running as a Service

#### systemd Service

```ini
[Unit]
Description=VEXIS-CLI AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/ClioAgent1
ExecStart=/path/to/venv/bin/python3 run.py --supervisor "default task"
Restart=always
RestartSec=10
WatchdogSec=600

[Install]
WantedBy=multi-user.target
```

#### Docker

```dockerfile
FROM python:3.11
WORKDIR /app
COPY . .
RUN python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
CMD ["./venv/bin/python3", "run.py", "--supervisor", "default task"]
```

### Health Check

```bash
# Run a self-diagnostic
python3 run.py --health-check

# Check supervisor status
cat .context/supervisor_health.json

# View recent restarts
cat .context/supervisor_restarts.json
```

## Monitoring

### Log Files

- `logs/supervisor.log` - Eternal supervisor logs
- `logs/vexis.log` - Main agent logs
- `logs/vexis_structured.log` - Structured JSON logs
- `logs/resilience_errors.jsonl` - Resilience engine error log

### Health Status

```bash
# View current health
cat .context/supervisor_health.json | python3 -m json.tool

# View supervisor heartbeat
cat .context/supulator_heartbeat.json | python3 -m json.tool
```

## Error Recovery Scenarios

### Scenario 1: Network Error

```
1. Agent gets network error from API
2. Exponential backoff (2s, 4s, 8s, 16s, 32s...)
3. After 10 consecutive failures, enter degraded mode
4. In degraded mode, retry less frequently
5. When network recovers, exit degraded mode
```

### Scenario 2: Process Crash

```
1. Agent process crashes (OOM, segfault, etc.)
2. Heartbeat stops updating
3. Supervisor detects heartbeat timeout
4. Supervisor kills stale process (if any)
5. Wait for backoff delay
6. Restart agent process
7. Agent loads exit_state.json and resumes
```

### Scenario 3: API Provider Failure

```
1. Primary API provider returns 500 error
2. Resilience engine classifies as EXTERNAL error
3. Circuit breaker opens after 5 failures
4. Fallback to next available provider
5. Continue with reduced capabilities if no fallback
6. Periodically retry primary provider
```

### Scenario 4: Disk Full

1. Self-diagnostic detects low disk space
2. Emergency cleanup of temp files and old logs
3. If still critical, force sleep to compress context
4. After sleep and restart, normal operation resumes

## Configuration Options

### config.yaml Options

```yaml
execution:
  command_timeout: 1800
  task_timeout: 7200
  show_thought_log: true

engine:
  max_retries: 3
  base_delay: 2.0
  backoff_factor: 2.0
  enable_self_healing: true
```

### Environment Variables

```bash
# Disable watchdog (for development)
export VEXIS_WATCHDOG_DISABLED=1

# Custom heartbeat timeout
export VEXIS_WATCHDOG_TIMEOUT=300

# Supervisor mode
export VEXIS_SUPERVISED=1
```

## Best Practices

1. **Always use `--supervisor` for production deployments**
2. **Monitor `logs/supervisor.log` for restart patterns**
3. **Set up alerts for excessive restarts (>10/hour)**
4. **Keep disk space above 5GB for context files**
5. **Use Telegram mode for remote monitoring**
6. **Regularly backup `.context/` directory**

## Troubleshooting

### Agent keeps restarting

Check `logs/supervisor.log` for the restart reason:
- Network errors: Check internet connection
- Auth errors: Verify API keys
- Resource errors: Free up disk/memory

### Agent is not responding

Check heartbeat:
```bash
cat .context/watchdog_heartbeat.json
```

If heartbeat is stale (>10 minutes), the agent may be hung.

### Context not loading

Verify state files exist:
```bash
ls -la .context/
cat .context/exit_state.json
cat .context/sleep_state.json
```
