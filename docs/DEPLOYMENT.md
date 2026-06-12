# Deployment Guide

## Overview

This guide covers deployment strategies, configurations, and best practices for VEXIS-CLI in various environments from development to production.

## Deployment Architectures

### Single Machine Deployment

```
┌─────────────────────────────────────────┐
│           Single Machine               │
├─────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────────┐   │
│  │  VEXIS-CLI  │  │   Ollama       │   │
│  │   Engine    │  │   Server       │   │
│  └─────────────┘  └─────────────────┘   │
│  ┌─────────────┐  ┌─────────────────┐   │
│  │   Config    │  │    Models      │   │
│  │   Files     │  │   Storage      │   │
│  └─────────────┘  └─────────────────┘   │
└─────────────────────────────────────────┘
```

**Use Cases:**
- Development environments
- Personal workstations
- Small team deployments
- Testing and staging

### Distributed Deployment

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Client 1      │    │   Client 2      │    │   Client N      │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌─────────────┴─────────────┐
                    │     Load Balancer         │
                    └─────────────┬─────────────┘
                                 │
                    ┌─────────────┴─────────────┐
                    │   VEXIS-CLI Cluster      │
                    │  ┌─────────────────────┐ │
                    │  │   Application Node   │ │
                    │  └─────────────────────┘ │
                    │  ┌─────────────────────┐ │
                    │  │   Ollama Cluster    │ │
                    │  └─────────────────────┘ │
                    └───────────────────────────┘
```

**Use Cases:**
- Production environments
- High-availability requirements
- Load balancing needs
- Multi-user scenarios

### Containerized Deployment

```
┌─────────────────────────────────────────┐
│           Docker Host                   │
├─────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────────┐   │
│  │   Container │  │   Container     │   │
│  │  VEXIS-CLI │  │    Ollama      │   │
│  └─────────────┘  └─────────────────┘   │
│  ┌─────────────┐  ┌─────────────────┐   │
│  │   Container │  │   Container     │   │
│  │    Redis    │  │   Monitoring    │   │
│  └─────────────┘  └─────────────────┘   │
└─────────────────────────────────────────┘
```

**Use Cases:**
- Cloud deployments
- Microservices architecture
- Scalable infrastructure
- CI/CD pipelines

## Environment Setup

### Development Environment

#### Prerequisites

```bash
# System requirements
Python 3.9+
Git
Ollama (optional, for local models)

# Check Python version
python3 --version

# Check Git
git --version

# Check Ollama (if using local models)
ollama --version
```

#### Installation Steps

```bash
# 1. Clone repository
git clone https://github.com/AInohogosya/VEXIS-CLI.git
cd VEXIS-CLI

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -e .  # Development mode

# 4. Install development dependencies
pip install -r requirements-dev.txt

# 5. Set up configuration
cp config.yaml.example config.yaml
# Edit config.yaml with your settings

# 6. Verify installation
python3 run.py --check-config
python3 run.py "test instruction"
```

#### Development Configuration

```yaml
# config.dev.yaml
api:
  preferred_provider: "ollama"
  local_endpoint: "http://localhost:11434"
  local_model: "gemini-3-flash-preview:latest"
  timeout: 60
  max_retries: 2

execution:
  safety_mode: true
  dry_run: false
  verify_commands: true

logging:
  level: "DEBUG"
  console: true
  file: "logs/dev.log"

development:
  debug_mode: true
  verbose_errors: true
  mock_responses: false
```

### Staging Environment

#### System Requirements

- **CPU**: 4+ cores
- **RAM**: 16GB+
- **Storage**: 50GB+
- **Network**: Stable internet connection

#### Configuration

```yaml
# config.staging.yaml
api:
  preferred_provider: "google"  # Test cloud provider
  google_api_key: "${GOOGLE_API_KEY}"
  gemini_model: "gemini-2.5-flash"  # Latest stable model
  timeout: 120
  max_retries: 3

execution:
  safety_mode: true
  dry_run: false
  verify_commands: true

logging:
  level: "INFO"
  console: false
  file: "/var/log/vexis/staging.log"
  rotation: true

security:
  safety_mode: true
  blacklist_mode: true

monitoring:
  enabled: true
  metrics_port: 9090
```

#### Deployment Script

```bash
#!/bin/bash
# deploy_staging.sh

set -e

echo "Deploying VEXIS-CLI to staging..."

# 1. Update code
git pull origin main

# 2. Install dependencies
pip install -r requirements.txt
pip install -e .

# 3. Set up configuration
export VEXIS_CONFIG="config.staging.yaml"
export GOOGLE_API_KEY="${STAGING_API_KEY}"

# 4. Run health checks
python3 run.py --check-config
python3 run.py --health-check

# 5. Start services
if command -v systemctl &> /dev/null; then
    sudo systemctl restart vexis-staging
else
    # Fallback to manual start
    nohup python3 run.py --daemon --config config.staging.yaml > /var/log/vexis/staging.out 2>&1 &
fi

echo "Staging deployment complete!"
```

### Production Environment

#### System Requirements

- **CPU**: 8+ cores
- **RAM**: 32GB+
- **Storage**: 100GB+ SSD
- **Network**: High-speed, redundant
- **OS**: Ubuntu 20.04+ LTS / CentOS 8+ / RHEL 8+

#### Production Configuration

```yaml
# config.prod.yaml
api:
  preferred_provider: "google"
  google_api_key: "${GOOGLE_API_KEY}"
  gemini_model: "gemini-2.5-flash"  # Latest stable model
  timeout: 180
  max_retries: 5
  rate_limit: 20

execution:
  safety_mode: true
  dry_run: false
  verify_commands: true
  parallel_execution: true
  max_concurrent: 5

logging:
  level: "INFO"
  console: false
  file: "/var/log/vexis/production.log"
  rotation: true
  max_size: "100MB"
  backup_count: 10

security:
  safety_mode: true
  blacklist_mode: true
  restricted_paths: ["/etc", "/usr/bin", "/root"]

monitoring:
  enabled: true
  metrics_port: 9090
  health_check_interval: 30

performance:
  cache_responses: true
  cache_size: 1000
  connection_pool_size: 10
```

#### Production Deployment Script

```bash
#!/bin/bash
# deploy_production.sh

set -e
set -u

# Configuration
APP_USER="vexis"
APP_DIR="/opt/vexis"
SERVICE_NAME="vexis-production"
BACKUP_DIR="/opt/vexis/backups"

echo "=== Production Deployment ==="

# 1. Pre-deployment checks
echo "Running pre-deployment checks..."

# Check system resources
MEMORY_GB=$(free -g | awk 'NR==2{print $2}')
if [ $MEMORY_GB -lt 32 ]; then
    echo "⚠️  Warning: System has less than 32GB RAM"
fi

# Check disk space
DISK_GB=$(df -BG /opt | awk 'NR==2{print $4}' | sed 's/G//')
if [ $DISK_GB -lt 100 ]; then
    echo "❌ Error: Insufficient disk space (need 100GB+, have ${DISK_GB}GB)"
    exit 1
fi

# 2. Backup current deployment
echo "Creating backup..."
BACKUP_FILE="${BACKUP_DIR}/vexis-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$BACKUP_FILE" -C "$APP_DIR" .

# 3. Update application
echo "Updating application..."
sudo -u "$APP_USER" git -C "$APP_DIR" pull origin main

# 4. Install dependencies
echo "Installing dependencies..."
sudo -u "$APP_USER" pip install -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" pip install -e "$APP_DIR"

# 5. Configuration
echo "Setting up configuration..."
sudo -u "$APP_USER" cp "$APP_DIR/config.prod.yaml" "$APP_DIR/config.yaml"
sudo -u "$APP_USER" chmod 600 "$APP_DIR/config.yaml"

# 6. Health checks
echo "Running health checks..."
sudo -u "$APP_USER" python3 "$APP_DIR/run.py" --check-config
if [ $? -ne 0 ]; then
    echo "❌ Configuration validation failed"
    exit 1
fi

sudo -u "$APP_USER" python3 "$APP_DIR/run.py" --health-check
if [ $? -ne 0 ]; then
    echo "❌ Health check failed"
    exit 1
fi

# 7. Restart service
echo "Restarting service..."
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl enable "$SERVICE_NAME"

# 8. Post-deployment verification
echo "Verifying deployment..."
sleep 10

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ Service is running"
else
    echo "❌ Service failed to start"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -l
    exit 1
fi

# Test API endpoint
curl -f http://localhost:8080/health || {
    echo "❌ Health endpoint failed"
    exit 1
}

echo "✅ Production deployment successful!"
echo "Backup saved to: $BACKUP_FILE"
```

## Container Deployment

### Docker Configuration

#### Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VEXIS_CONFIG=/app/config.yaml

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install application
RUN pip install -e .

# Create non-root user
RUN useradd --create-home --shell /bin/bash vexis
RUN chown -R vexis:vexis /app
USER vexis

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 run.py --health-check || exit 1

# Start command
CMD ["python3", "run.py", "--server", "--port", "8080"]
```

#### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  vexis:
    build: .
    ports:
      - "8080:8080"
    environment:
      - VEXIS_CONFIG=/app/config.yaml
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./logs:/app/logs
      - ollama_models:/home/vexis/.ollama
    depends_on:
      - ollama
      - redis
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python3", "run.py", "--health-check"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_models:/root/.ollama
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - vexis
    restart: unless-stopped

volumes:
  ollama_models:
  redis_data:
```

#### Multi-Stage Dockerfile

```dockerfile
# Dockerfile.prod
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Install application
RUN pip install -e .

# Create non-root user
RUN useradd --create-home --shell /bin/bash vexis
RUN chown -R vexis:vexis /app
USER vexis

EXPOSE 8080

CMD ["python3", "run.py", "--server", "--port", "8080"]
```

### Kubernetes Deployment

#### Deployment Manifest

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vexis-deployment
  labels:
    app: vexis
spec:
  replicas: 3
  selector:
    matchLabels:
      app: vexis
  template:
    metadata:
      labels:
        app: vexis
    spec:
      containers:
      - name: vexis
        image: vexis-cli:latest
        ports:
        - containerPort: 8080
        env:
        - name: VEXIS_CONFIG
          value: "/app/config.yaml"
        - name: GOOGLE_API_KEY
          valueFrom:
            secretKeyRef:
              name: vexis-secrets
              key: google-api-key
        volumeMounts:
        - name: config
          mountPath: /app/config.yaml
          subPath: config.yaml
        - name: logs
          mountPath: /app/logs
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: config
        configMap:
          name: vexis-config
      - name: logs
        emptyDir: {}
```

#### Service Manifest

```yaml
# k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: vexis-service
spec:
  selector:
    app: vexis
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8080
  type: LoadBalancer
```

#### ConfigMap

```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: vexis-config
data:
  config.yaml: |
    api:
      preferred_provider: "google"
      google_api_key: "${GOOGLE_API_KEY}"
      gemini_model: "gemini-1.5-pro"
      timeout: 180
      max_retries: 5
    
    execution:
      safety_mode: true
      dry_run: false
      verify_commands: true
    
    logging:
      level: "INFO"
      console: false
      file: "/app/logs/production.log"
    
    monitoring:
      enabled: true
      metrics_port: 9090
```

#### Secret

```yaml
# k8s/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: vexis-secrets
type: Opaque
data:
  google-api-key: <base64-encoded-api-key>
```

## Cloud Deployment

### AWS Deployment

#### EC2 Instance Setup

```bash
#!/bin/bash
# aws_setup.sh

# Update system
sudo yum update -y

# Install Docker
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -a -G docker ec2-user

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Create application directory
sudo mkdir -p /opt/vexis
sudo chown ec2-user:ec2-user /opt/vexis

# Clone repository
cd /opt/vexis
git clone https://github.com/AInohogosya/VEXIS-CLI.git .

# Set up environment
echo "GOOGLE_API_KEY=${GOOGLE_API_KEY}" > .env

# Start services
docker-compose up -d

# Setup log rotation
sudo tee /etc/logrotate.d/vexis << EOF
/opt/vexis/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 ec2-user ec2-user
}
EOF
```

#### ECS Task Definition

```json
{
  "family": "vexis-task",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::account:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::account:role/ecsTaskRole",
  "containerDefinitions": [
    {
      "name": "vexis",
      "image": "your-account.dkr.ecr.region.amazonaws.com/vexis:latest",
      "portMappings": [
        {
          "containerPort": 8080,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "VEXIS_CONFIG",
          "value": "/app/config.yaml"
        }
      ],
      "secrets": [
        {
          "name": "GOOGLE_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:vexis/google-api-key"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/vexis",
          "awslogs-region": "us-west-2",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "python3 run.py --health-check"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

### Google Cloud Deployment

#### Cloud Run Service

```yaml
# cloudrun/service.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: vexis-service
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/cpu-throttling: "false"
        run.googleapis.com/memory: "2Gi"
    spec:
      containerConcurrency: 10
      timeoutSeconds: 300
      containers:
      - image: gcr.io/project-id/vexis:latest
        ports:
        - containerPort: 8080
        env:
        - name: VEXIS_CONFIG
          value: "/app/config.yaml"
        - name: GOOGLE_API_KEY
          valueFrom:
            secretKeyRef:
              name: vexis-secrets
              key: google-api-key
        resources:
          limits:
            cpu: "1000m"
            memory: "2Gi"
        startupProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 60
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
```

#### Deployment Script

```bash
#!/bin/bash
# gcp_deploy.sh

PROJECT_ID="your-project-id"
REGION="us-central1"
SERVICE_NAME="vexis-service"

# Build and push image
gcloud builds submit --tag gcr.io/${PROJECT_ID}/vexis:latest .

# Deploy to Cloud Run
gcloud run deploy ${SERVICE_NAME} \
  --image gcr.io/${PROJECT_ID}/vexis:latest \
  --region ${REGION} \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --timeout 300s \
  --set-env-vars VEXIS_CONFIG=/app/config.yaml \
  --set-secrets GOOGLE_API_KEY=vexis-secrets:google-api-key

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
  --region ${REGION} \
  --format 'value(status.url)')

echo "Service deployed at: ${SERVICE_URL}"
```

## Monitoring and Observability

### Health Checks

#### Health Check Endpoint

```python
# src/ai_agent/monitoring/health.py
from fastapi import FastAPI, HTTPException
from ai_agent.core_processing.two_phase_engine import TwoPhaseEngine
from ai_agent.utils.config import load_config
import psutil
import asyncio

app = FastAPI()

@app.get("/health")
async def health_check():
    """Comprehensive health check endpoint."""
    
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {}
    }
    
    # Check configuration
    try:
        config = load_config()
        health_status["checks"]["config"] = {"status": "healthy"}
    except Exception as e:
        health_status["checks"]["config"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "unhealthy"
    
    # Check AI providers
    try:
        engine = TwoPhaseEngine(config)
        
        # Check Ollama
        ollama_available = await engine.model_runner.is_provider_available("ollama")
        health_status["checks"]["ollama"] = {
            "status": "healthy" if ollama_available else "unhealthy",
            "available": ollama_available
        }
        
        # Check Gemini
        gemini_available = engine.model_runner.is_provider_available("google")
        health_status["checks"]["gemini"] = {
            "status": "healthy" if gemini_available else "unhealthy",
            "available": gemini_available
        }
        
        if not (ollama_available or gemini_available):
            health_status["status"] = "unhealthy"
            
    except Exception as e:
        health_status["checks"]["providers"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "unhealthy"
    
    # Check system resources
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        health_status["checks"]["resources"] = {
            "status": "healthy",
            "memory_percent": memory.percent,
            "disk_percent": (disk.total - disk.free) / disk.total * 100
        }
        
        if memory.percent > 90 or (disk.total - disk.free) / disk.total > 90:
            health_status["checks"]["resources"]["status"] = "warning"
            
    except Exception as e:
        health_status["checks"]["resources"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # Return appropriate status code
    status_code = 200 if health_status["status"] == "healthy" else 503
    return health_status, status_code

@app.get("/ready")
async def readiness_check():
    """Readiness check for Kubernetes."""
    
    # Check if application is ready to serve requests
    try:
        config = load_config()
        engine = TwoPhaseEngine(config)
        
        # Test primary provider
        primary_provider = config["api"]["preferred_provider"]
        available = await engine.model_runner.is_provider_available(primary_provider)
        
        if available:
            return {"status": "ready"}
        else:
            raise HTTPException(status_code=503, detail="Primary provider unavailable")
            
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
```

### Metrics Collection

#### Prometheus Metrics

```python
# src/ai_agent/monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest
import time
import psutil

# Define metrics
REQUEST_COUNT = Counter('vexis_requests_total', 'Total requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('vexis_request_duration_seconds', 'Request duration')
ACTIVE_CONNECTIONS = Gauge('vexis_active_connections', 'Active connections')
MEMORY_USAGE = Gauge('vexis_memory_usage_bytes', 'Memory usage')
CPU_USAGE = Gauge('vexis_cpu_usage_percent', 'CPU usage')

class MetricsMiddleware:
    """Middleware to collect request metrics."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            start_time = time.time()
            
            # Increment active connections
            ACTIVE_CONNECTIONS.inc()
            
            try:
                await self.app(scope, receive, send)
                status = "200"  # This would be set by the actual response
            except Exception as e:
                status = "500"
                raise
            finally:
                # Record metrics
                duration = time.time() - start_time
                REQUEST_DURATION.observe(duration)
                REQUEST_COUNT.labels(
                    method=scope["method"],
                    endpoint=scope["path"],
                    status=status
                ).inc()
                
                # Decrement active connections
                ACTIVE_CONNECTIONS.dec()
        else:
            await self.app(scope, receive, send)

def update_system_metrics():
    """Update system resource metrics."""
    
    # Memory usage
    memory = psutil.virtual_memory()
    MEMORY_USAGE.set(memory.used)
    
    # CPU usage
    cpu_percent = psutil.cpu_percent()
    CPU_USAGE.set(cpu_percent)

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    update_system_metrics()
    return Response(generate_latest(), media_type="text/plain")
```

### Logging Configuration

#### Structured Logging

```python
# src/ai_agent/monitoring/logging.py
import json
import logging
from datetime import datetime
from typing import Dict, Any

class StructuredLogger:
    """Structured logger for production monitoring."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        # Create formatter
        formatter = StructuredFormatter()
        
        # Create handler
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
    
    def info(self, message: str, **kwargs):
        """Log info message with structured data."""
        self.logger.info(message, extra=kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message with structured data."""
        self.logger.error(message, extra=kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message with structured data."""
        self.logger.warning(message, extra=kwargs)

class StructuredFormatter(logging.Formatter):
    """Formatter for structured JSON logging."""
    
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 
                              'pathname', 'filename', 'module', 'lineno', 
                              'funcName', 'created', 'msecs', 'relativeCreated', 
                              'thread', 'threadName', 'processName', 'process', 
                              'getMessage', 'exc_info', 'exc_text', 'stack_info']:
                    log_entry[key] = value
        
        return json.dumps(log_entry)

# Usage example
logger = StructuredLogger("vexis.production")

logger.info(
    "Instruction processed",
    instruction="list files",
    provider="ollama",
    model="gemini-3-flash-preview",
    duration=1.23,
    tokens_used=150
)
```

## Security Considerations

### Security Configuration

```yaml
# security.yaml
security:
  # Command restrictions
  blacklist_mode: true
  blacklist_commands: [
    "rm -rf /",
    "sudo rm",
    "dd if=/dev/zero",
    "mkfs",
    "fdisk",
    "format"
  ]
  
  # File system restrictions
  restricted_paths: [
    "/etc",
    "/usr/bin",
    "/sbin",
    "/boot",
    "/root",
    "/System"
  ]
  
  # Network restrictions
  allowed_domains: [
    "ollama.ai",
    "googleapis.com",
    "generativelanguage.googleapis.com"
  ]
  
  # API security
  api_key_rotation: true
  api_key_expiry: 86400  # 24 hours
  
  # Authentication
  require_auth: true
  auth_method: "api_key"
  rate_limiting:
    requests_per_minute: 60
    burst_size: 10
```

### Container Security

#### Docker Security

```dockerfile
# Dockerfile.secure
FROM python:3.11-slim as builder

# Build as root, then run as non-root
RUN addgroup --system vexis && adduser --system --group vexis

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN addgroup --system vexis && adduser --system --group vexis

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=vexis:vexis . .

# Install application
RUN pip install -e .

# Set permissions
RUN chown -R vexis:vexis /app
RUN chmod -R 755 /app
RUN chmod 600 /app/config.yaml

# Switch to non-root user
USER vexis

# Security scanning
RUN python3 -m pip audit

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 run.py --health-check || exit 1

EXPOSE 8080

CMD ["python3", "run.py", "--server", "--port", "8080"]
```

### Network Security

#### Nginx Configuration

```nginx
# nginx.conf
events {
    worker_connections 1024;
}

http {
    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains";
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    
    upstream vexis_backend {
        server vexis:8080;
    }
    
    server {
        listen 80;
        server_name your-domain.com;
        
        # Redirect to HTTPS
        return 301 https://$server_name$request_uri;
    }
    
    server {
        listen 443 ssl http2;
        server_name your-domain.com;
        
        # SSL configuration
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
        ssl_prefer_server_ciphers off;
        
        # Apply rate limiting
        limit_req zone=api burst=20 nodelay;
        
        location / {
            proxy_pass http://vexis_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # Timeouts
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }
        
        # Health check endpoint (no rate limiting)
        location /health {
            limit_req zone=api burst=5 nodelay;
            proxy_pass http://vexis_backend;
        }
    }
}
```

## Backup and Recovery

### Backup Strategy

#### Automated Backup Script

```bash
#!/bin/bash
# backup.sh

set -e

# Configuration
BACKUP_DIR="/opt/vexis/backups"
APP_DIR="/opt/vexis"
RETENTION_DAYS=30
S3_BUCKET="vexis-backups"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate backup filename
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="vexis-backup-${TIMESTAMP}.tar.gz"

# Create backup
echo "Creating backup: $BACKUP_FILE"

tar -czf "$BACKUP_DIR/$BACKUP_FILE" \
    --exclude="$APP_DIR/.git" \
    --exclude="$APP_DIR/__pycache__" \
    --exclude="$APP_DIR/.pytest_cache" \
    --exclude="$APP_DIR/node_modules" \
    -C "$APP_DIR" .

# Upload to S3 (if configured)
if command -v aws &> /dev/null && [ ! -z "$S3_BUCKET" ]; then
    echo "Uploading to S3..."
    aws s3 cp "$BACKUP_DIR/$BACKUP_FILE" "s3://$S3_BUCKET/backups/"
fi

# Clean old backups
echo "Cleaning old backups..."
find "$BACKUP_DIR" -name "vexis-backup-*.tar.gz" -mtime +$RETENTION_DAYS -delete

# Clean S3 backups
if command -v aws &> /dev/null && [ ! -z "$S3_BUCKET" ]; then
    aws s3 ls "s3://$S3_BUCKET/backups/" | \
    while read -r line; do
        createDate=$(echo $line | awk '{print $1" "$2}')
        createDate=$(date -d"$createDate" +%s)
        olderThan=$(date -d"$RETENTION_DAYS days ago" +%s)
        
        if [[ $createDate -lt $olderThan ]]; then
            fileName=$(echo $line | awk '{print $4}')
            aws s3 rm "s3://$S3_BUCKET/backups/$fileName"
        fi
    done
fi

echo "Backup completed: $BACKUP_FILE"
```

#### Recovery Script

```bash
#!/bin/bash
# recovery.sh

set -e

# Configuration
BACKUP_DIR="/opt/vexis/backups"
APP_DIR="/opt/vexis"
SERVICE_NAME="vexis-production"

# Function to list available backups
list_backups() {
    echo "Available backups:"
    ls -la "$BACKUP_DIR"/vexis-backup-*.tar.gz | awk '{print $9}' | while read backup; do
        basename "$backup"
    done
}

# Function to restore from backup
restore_backup() {
    local backup_file=$1
    
    if [ ! -f "$BACKUP_DIR/$backup_file" ]; then
        echo "Backup file not found: $backup_file"
        exit 1
    fi
    
    echo "Stopping service..."
    sudo systemctl stop "$SERVICE_NAME"
    
    echo "Creating current backup before restore..."
    ./backup.sh
    
    echo "Restoring from backup: $backup_file"
    
    # Remove existing application (excluding backups)
    sudo find "$APP_DIR" -mindepth 1 -not -path "$BACKUP_DIR" -delete
    
    # Restore from backup
    sudo tar -xzf "$BACKUP_DIR/$backup_file" -C "$APP_DIR"
    
    # Fix permissions
    sudo chown -R vexis:vexis "$APP_DIR"
    
    # Reinstall dependencies
    sudo -u vexis pip install -r "$APP_DIR/requirements.txt"
    sudo -u vexis pip install -e "$APP_DIR"
    
    echo "Starting service..."
    sudo systemctl start "$SERVICE_NAME"
    
    # Verify service
    sleep 10
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "✅ Service restored successfully"
    else
        echo "❌ Service failed to start"
        sudo journalctl -u "$SERVICE_NAME" --no-pager -l
        exit 1
    fi
}

# Main script
if [ $# -eq 0 ]; then
    list_backups
    echo "Usage: $0 <backup_file>"
    exit 1
fi

restore_backup "$1"
echo "Recovery completed successfully"
```

This comprehensive deployment guide provides everything needed to deploy VEXIS-CLI-2.2 in various environments, from development setups to production-scale deployments with proper monitoring, security, and backup strategies.
