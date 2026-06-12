"""
Exception classes for AI Agent System
Enhanced exceptions with categorization for 5-Phase Architecture
"""

from enum import Enum, auto
from typing import Optional, Dict, Any
from dataclasses import dataclass


class ErrorCategory(Enum):
    """Error categories for intelligent error handling and retry strategies"""
    TRANSIENT = "transient"           # Temporary errors, retryable (network timeout)
    PERMANENT = "permanent"           # Permanent errors, not retryable (invalid syntax)
    AUTHENTICATION = "authentication"  # Auth errors, require re-authentication
    RATE_LIMIT = "rate_limit"         # Rate limiting, retry with backoff
    VALIDATION = "validation"         # Input validation errors
    RESOURCE = "resource"             # Resource exhaustion (OOM, disk full)
    TIMEOUT = "timeout"               # Timeout errors
    CONFIGURATION = "configuration"   # Configuration errors
    EXTERNAL = "external"             # External service errors (API failures)
    UNKNOWN = "unknown"               # Uncategorized errors


@dataclass
class ErrorContext:
    """Context information for error handling"""
    category: ErrorCategory
    retryable: bool
    max_retries: int
    backoff_seconds: float
    error_code: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    phase: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class AIAgentException(Exception):
    """Base exception for AI Agent system with categorization support"""
    
    def __init__(self, message: str, context: Optional[ErrorContext] = None, **kwargs):
        super().__init__(message)
        self.context = context or ErrorContext(
            category=ErrorCategory.UNKNOWN,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0
        )
        # Store any additional keyword arguments as attributes
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def is_retryable(self) -> bool:
        """Check if this error is retryable"""
        return self.context.retryable if self.context else False
    
    def get_retry_delay(self) -> float:
        """Get recommended retry delay in seconds"""
        return self.context.backoff_seconds if self.context else 0.0


class APIError(AIAgentException):
    """API-related error with automatic categorization"""
    
    def __init__(self, message: str, status_code: Optional[int] = None, **kwargs):
        # Auto-categorize based on status code
        if status_code == 401 or status_code == 403:
            category = ErrorCategory.AUTHENTICATION
            retryable = False
            max_retries = 0
            backoff = 0.0
        elif status_code == 429:
            category = ErrorCategory.RATE_LIMIT
            retryable = True
            max_retries = 5
            backoff = 60.0  # 1 minute
        elif status_code and 500 <= status_code < 600:
            category = ErrorCategory.EXTERNAL
            retryable = True
            max_retries = 3
            backoff = 2.0
        elif status_code and 400 <= status_code < 500:
            category = ErrorCategory.VALIDATION
            retryable = False
            max_retries = 0
            backoff = 0.0
        else:
            category = ErrorCategory.EXTERNAL
            retryable = True
            max_retries = 3
            backoff = 1.0
        
        context = ErrorContext(
            category=category,
            retryable=retryable,
            max_retries=max_retries,
            backoff_seconds=backoff,
            error_code=str(status_code) if status_code else None
        )
        super().__init__(message, context=context, **kwargs)
        self.status_code = status_code


class ValidationError(AIAgentException):
    """Validation error - not retryable"""
    
    def __init__(self, message: str, field: Optional[str] = None, value: Any = None, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.VALIDATION,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0
        )
        super().__init__(message, context=context, **kwargs)
        self.field = field
        self.value = value


class ConfigurationError(AIAgentException):
    """Configuration error - not retryable"""
    
    def __init__(self, message: str, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.CONFIGURATION,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0
        )
        super().__init__(message, context=context, **kwargs)


class PlatformError(AIAgentException):
    """Platform-related error"""
    
    def __init__(self, message: str, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.EXTERNAL,
            retryable=True,
            max_retries=2,
            backoff_seconds=1.0
        )
        super().__init__(message, context=context, **kwargs)


class ScreenshotError(AIAgentException):
    """Screenshot-related error"""
    pass


class ExecutionError(AIAgentException):
    """Execution error - may be retryable depending on command"""
    
    def __init__(self, message: str, command: Optional[str] = None, exit_code: Optional[int] = None, **kwargs):
        # Determine retryability based on exit code
        if exit_code == 130:  # SIGINT
            category = ErrorCategory.PERMANENT
            retryable = False
            max_retries = 0
            backoff = 0.0
        elif exit_code and exit_code > 128:  # Signal termination
            category = ErrorCategory.TRANSIENT
            retryable = True
            max_retries = 2
            backoff = 0.5
        else:
            category = ErrorCategory.EXTERNAL
            retryable = True
            max_retries = 3
            backoff = 1.0
        
        context = ErrorContext(
            category=category,
            retryable=retryable,
            max_retries=max_retries,
            backoff_seconds=backoff,
            error_code=str(exit_code) if exit_code else None
        )
        super().__init__(message, context=context, **kwargs)
        self.command = command
        self.exit_code = exit_code


class TaskGenerationError(AIAgentException):
    """Task generation error - retryable"""
    
    def __init__(self, message: str, instruction: Optional[str] = None, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.TRANSIENT,
            retryable=True,
            max_retries=2,
            backoff_seconds=1.0
        )
        super().__init__(message, context=context, **kwargs)
        self.instruction = instruction


class CommandParsingError(AIAgentException):
    """Command parsing error - not retryable (usually code issue)"""
    
    def __init__(self, message: str, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.PERMANENT,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0
        )
        super().__init__(message, context=context, **kwargs)


class VerificationError(AIAgentException):
    """Task verification error"""
    
    def __init__(self, message: str, task: Optional[str] = None, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.VALIDATION,
            retryable=True,
            max_retries=1,
            backoff_seconds=0.5
        )
        super().__init__(message, context=context, **kwargs)
        self.task = task


class TimeoutError(AIAgentException):
    """Timeout error - retryable with backoff"""
    
    def __init__(self, message: str, timeout_seconds: Optional[float] = None, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.TIMEOUT,
            retryable=True,
            max_retries=3,
            backoff_seconds=5.0
        )
        super().__init__(message, context=context, **kwargs)
        self.timeout_seconds = timeout_seconds


class ResourceExhaustedError(AIAgentException):
    """Resource exhausted error - retryable with longer backoff"""
    
    def __init__(self, message: str, resource_type: Optional[str] = None, **kwargs):
        context = ErrorContext(
            category=ErrorCategory.RESOURCE,
            retryable=True,
            max_retries=2,
            backoff_seconds=30.0  # Longer backoff for resources
        )
        super().__init__(message, context=context, **kwargs)
        self.resource_type = resource_type


class ErrorHandler:
    """Centralized error handling with retry logic"""
    
    @staticmethod
    def classify_error(error: Exception, provider: Optional[str] = None, 
                      phase: Optional[str] = None) -> ErrorContext:
        """Classify an exception and return error context"""
        if isinstance(error, AIAgentException) and error.context:
            # Update context with current provider/phase
            error.context.provider = provider
            error.context.phase = phase
            return error.context
        
        # Default classification for unknown errors
        return ErrorContext(
            category=ErrorCategory.UNKNOWN,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0,
            provider=provider,
            phase=phase
        )
    
    @staticmethod
    def should_retry(error: Exception, attempt: int) -> bool:
        """Determine if error should be retried based on attempt count"""
        context = ErrorHandler.classify_error(error)
        
        if not context.retryable:
            return False
        
        if attempt >= context.max_retries:
            return False
        
        return True
    
    @staticmethod
    def get_retry_delay(error: Exception, attempt: int) -> float:
        """Calculate retry delay with exponential backoff"""
        context = ErrorHandler.classify_error(error)
        
        # Exponential backoff: backoff * (2 ^ attempt)
        delay = context.backoff_seconds * (2 ** attempt)
        
        # Cap at 5 minutes for rate limits, 30 seconds for others
        if context.category == ErrorCategory.RATE_LIMIT:
            return min(delay, 300.0)
        else:
            return min(delay, 30.0)
