"""
Structured Logging for VEXIS-CLI
JSON-formatted logs for better observability and log analysis
"""

import json
import logging
import sys
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path
from logging.handlers import RotatingFileHandler


class StructuredLogFormatter(logging.Formatter):
    """
    JSON formatter for structured logging
    
    Output format:
    {
        "timestamp": "2026-04-18T10:30:00.123Z",
        "level": "INFO",
        "logger": "vexis.engine",
        "message": "Execution started",
        "context": {
            "phase": "phase1",
            "provider": "groq"
        },
        "source": {
            "file": "autonomous_loop_engine.py",
            "line": 123,
            "function": "_run_phase1"
        }
    }
    """
    
    def __init__(self, indent: Optional[int] = None):
        super().__init__()
        self.indent = indent
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info) if record.exc_info else None
            }
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in [
                'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
                'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
                'thread', 'threadName', 'processName', 'process', 'getMessage'
            ]:
                if 'context' not in log_data:
                    log_data['context'] = {}
                log_data['context'][key] = value
        
        # Add source information
        log_data["source"] = {
            "file": record.filename,
            "line": record.lineno,
            "function": record.funcName
        }
        
        # Output as JSON
        return json.dumps(log_data, indent=self.indent, default=str)


class StructuredLogger:
    """
    Structured logger wrapper providing both console and JSON file output
    """
    
    def __init__(
        self,
        name: str = "vexis",
        log_level: int = logging.INFO,
        log_dir: Optional[str] = None,
        json_output: bool = True,
        console_output: bool = True,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5
    ):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)
        self.logger.handlers = []  # Clear existing handlers
        
        # Console handler (human-readable)
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level)
            console_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
        
        # File handler (JSON structured)
        if json_output and log_dir:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            
            json_file = log_path / "vexis_structured.log"
            file_handler = RotatingFileHandler(
                json_file,
                maxBytes=max_file_size,
                backupCount=backup_count
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(StructuredLogFormatter())
            self.logger.addHandler(file_handler)
            
            # Also add standard log file
            standard_file = log_path / "vexis.log"
            standard_handler = RotatingFileHandler(
                standard_file,
                maxBytes=max_file_size,
                backupCount=backup_count
            )
            standard_handler.setLevel(log_level)
            standard_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            self.logger.addHandler(standard_handler)
    
    def debug(self, message: str, **kwargs):
        """Log debug message with structured context"""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message with structured context"""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message with structured context"""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message with structured context"""
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Log critical message with structured context"""
        self._log(logging.CRITICAL, message, **kwargs)
    
    def _log(self, level: int, message: str, **kwargs):
        """Internal log method with context"""
        # Create extra dict for context
        extra = kwargs if kwargs else {}
        
        # Log with extra context
        self.logger.log(level, message, extra=extra)


class TelemetryCollector:
    """
    Collects and exports telemetry data for observability
    """
    
    def __init__(self, export_interval: int = 60):
        self.export_interval = export_interval
        self.metrics: Dict[str, Any] = {
            "counters": {},
            "gauges": {},
            "histograms": {}
        }
        self.logger = logging.getLogger("vexis.telemetry")
    
    def increment_counter(self, name: str, value: int = 1, labels: Optional[Dict] = None):
        """Increment a counter metric"""
        key = f"{name}:{json.dumps(labels, sort_keys=True) if labels else ''}"
        self.metrics["counters"][key] = self.metrics["counters"].get(key, 0) + value
    
    def set_gauge(self, name: str, value: float, labels: Optional[Dict] = None):
        """Set a gauge metric"""
        key = f"{name}:{json.dumps(labels, sort_keys=True) if labels else ''}"
        self.metrics["gauges"][key] = value
    
    def record_histogram(self, name: str, value: float, labels: Optional[Dict] = None):
        """Record a histogram observation"""
        key = f"{name}:{json.dumps(labels, sort_keys=True) if labels else ''}"
        if key not in self.metrics["histograms"]:
            self.metrics["histograms"][key] = []
        self.metrics["histograms"][key].append(value)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics snapshot"""
        # Calculate histogram statistics
        histogram_stats = {}
        for key, values in self.metrics["histograms"].items():
            if values:
                histogram_stats[key] = {
                    "count": len(values),
                    "sum": sum(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values)
                }
        
        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "counters": self.metrics["counters"],
            "gauges": self.metrics["gauges"],
            "histograms": histogram_stats
        }
    
    def export_metrics(self) -> str:
        """Export metrics as JSON string"""
        return json.dumps(self.get_metrics(), indent=2)
    
    def reset(self):
        """Reset all metrics"""
        self.metrics = {
            "counters": {},
            "gauges": {},
            "histograms": {}
        }


# Global instances
_structured_logger: Optional[StructuredLogger] = None
_telemetry: Optional[TelemetryCollector] = None


def get_structured_logger(
    name: str = "vexis",
    log_level: int = logging.INFO,
    log_dir: Optional[str] = None,
    json_output: bool = True
) -> StructuredLogger:
    """Get or create global structured logger"""
    global _structured_logger
    
    if _structured_logger is None:
        _structured_logger = StructuredLogger(
            name=name,
            log_level=log_level,
            log_dir=log_dir or str(Path.home() / ".vexis" / "logs"),
            json_output=json_output
        )
    
    return _structured_logger


def get_telemetry() -> TelemetryCollector:
    """Get global telemetry collector"""
    global _telemetry
    
    if _telemetry is None:
        _telemetry = TelemetryCollector()
    
    return _telemetry


def configure_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_dir: Optional[str] = None
):
    """
    Configure structured logging for the application
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: Enable JSON structured logging to file
        log_dir: Directory for log files (default: ~/.vexis/logs)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Set up structured logger
    logger = get_structured_logger(
        name="vexis",
        log_level=log_level,
        log_dir=log_dir,
        json_output=json_output
    )
    
    # Log configuration
    logger.info(
        "Structured logging configured",
        level=level,
        json_output=json_output,
        log_dir=log_dir or str(Path.home() / ".vexis" / "logs")
    )
    
    return logger


def log_execution_metric(
    metric_name: str,
    value: float,
    provider: str,
    model: str,
    phase: Optional[str] = None
):
    """Log an execution metric with structured context"""
    telemetry = get_telemetry()
    labels = {
        "provider": provider,
        "model": model,
        "phase": phase or "unknown"
    }
    telemetry.record_histogram(metric_name, value, labels)
    
    # Also log via structured logger
    logger = get_structured_logger()
    logger.info(
        f"Execution metric: {metric_name}={value:.3f}",
        metric_name=metric_name,
        value=value,
        provider=provider,
        model=model,
        phase=phase
    )
