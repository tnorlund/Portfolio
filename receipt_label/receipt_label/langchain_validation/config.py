"""
Ollama-Only Configuration for LangChain Validation
===================================================

Simplified configuration module supporting only Ollama (local or Turbo)
for receipt validation without OpenAI dependencies.
"""

import os
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum


class ValidationMode(Enum):
    """Validation processing modes"""

    REALTIME = "realtime"
    BATCH = "batch"
    SMART_BATCH = "smart_batch"


@dataclass
class OllamaConfig:
    """Ollama-specific configuration (supports both local and Turbo)"""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    api_key: Optional[str] = None  # For Ollama Turbo authentication
    temperature: float = 0.0
    timeout: int = 60  # Ollama can be slower
    max_retries: int = 3
    is_turbo: bool = False  # Whether using Ollama Turbo (hosted)

    # Ollama-specific options
    num_ctx: Optional[int] = None  # Context length
    num_predict: Optional[int] = None  # Max tokens to predict
    top_k: Optional[int] = None
    top_p: Optional[float] = None

    def validate(self) -> List[str]:
        """Validate Ollama configuration"""
        errors = []

        if not self.base_url:
            errors.append("Ollama base URL is required")

        if not self.model:
            errors.append("Ollama model name is required")

        if self.temperature < 0 or self.temperature > 2:
            errors.append("Temperature must be between 0 and 2")

        if self.top_p and (self.top_p <= 0 or self.top_p > 1):
            errors.append("top_p must be between 0 and 1")

        if self.is_turbo and not self.api_key:
            errors.append("API key is required for Ollama Turbo")

        return errors


@dataclass
class BatchConfig:
    """Batch processing configuration"""

    enabled: bool = True
    batch_size: int = 10
    max_concurrent: int = 5
    delay_between_batches: float = 1.0
    urgent_threshold: int = 1
    cost_optimization: bool = True


@dataclass
class CacheConfig:
    """Caching configuration"""

    enabled: bool = True
    max_entries: int = 1000
    ttl_seconds: int = 3600
    cache_successful_only: bool = True


@dataclass
class MonitoringConfig:
    """Monitoring and observability configuration"""

    enabled: bool = True
    langsmith_enabled: bool = False
    langsmith_api_key: Optional[str] = None
    langsmith_project: str = "receipt-validation"
    collect_metrics: bool = True
    metrics_file: Optional[str] = None


@dataclass
class ValidationConfig:
    """Main validation configuration"""

    mode: ValidationMode = ValidationMode.SMART_BATCH
    ollama: OllamaConfig = None
    batch: BatchConfig = None
    cache: CacheConfig = None
    monitoring: MonitoringConfig = None

    # Data sources
    dynamodb_table_name: str = "receipt-dev"

    # Debugging
    debug: bool = False
    environment: str = "development"

    @classmethod
    def from_env(cls) -> "ValidationConfig":
        """Load configuration from environment variables"""

        # Validation mode
        mode_str = os.getenv("VALIDATION_MODE", "smart_batch").lower()
        try:
            mode = ValidationMode(mode_str)
        except ValueError:
            mode = ValidationMode.SMART_BATCH

        # Ollama Turbo configuration
        base_url = "https://ollama.com"
        api_key = os.getenv("OLLAMA_API_KEY")
        is_turbo = True

        ollama_config = OllamaConfig(
            base_url=base_url,
            model="gpt-oss:120b",
            api_key=api_key,
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.0")),
            timeout=int(os.getenv("OLLAMA_TIMEOUT", "60")),
            max_retries=int(os.getenv("OLLAMA_MAX_RETRIES", "3")),
            is_turbo=is_turbo,
            num_ctx=(
                int(os.getenv("OLLAMA_NUM_CTX"))
                if os.getenv("OLLAMA_NUM_CTX")
                else None
            ),
            num_predict=(
                int(os.getenv("OLLAMA_NUM_PREDICT"))
                if os.getenv("OLLAMA_NUM_PREDICT")
                else None
            ),
            top_k=(
                int(os.getenv("OLLAMA_TOP_K"))
                if os.getenv("OLLAMA_TOP_K")
                else None
            ),
            top_p=(
                float(os.getenv("OLLAMA_TOP_P"))
                if os.getenv("OLLAMA_TOP_P")
                else None
            ),
        )

        # Batch configuration
        batch_config = BatchConfig(
            enabled=os.getenv("BATCH_ENABLED", "true").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "10")),
            max_concurrent=int(os.getenv("BATCH_MAX_CONCURRENT", "5")),
            delay_between_batches=float(os.getenv("BATCH_DELAY", "1.0")),
            urgent_threshold=int(os.getenv("BATCH_URGENT_THRESHOLD", "1")),
            cost_optimization=os.getenv(
                "BATCH_COST_OPTIMIZATION", "true"
            ).lower()
            == "true",
        )

        # Cache configuration
        cache_config = CacheConfig(
            enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
            max_entries=int(os.getenv("CACHE_MAX_ENTRIES", "1000")),
            ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
            cache_successful_only=os.getenv(
                "CACHE_SUCCESSFUL_ONLY", "true"
            ).lower()
            == "true",
        )

        # Monitoring configuration
        monitoring_config = MonitoringConfig(
            enabled=os.getenv("MONITORING_ENABLED", "true").lower() == "true",
            langsmith_enabled=os.getenv(
                "LANGCHAIN_TRACING_V2", "false"
            ).lower()
            == "true",
            langsmith_api_key=os.getenv("LANGCHAIN_API_KEY"),
            langsmith_project=os.getenv(
                "LANGCHAIN_PROJECT", "receipt-validation"
            ),
            collect_metrics=os.getenv("COLLECT_METRICS", "true").lower()
            == "true",
            metrics_file=os.getenv("METRICS_FILE"),
        )

        return cls(
            mode=mode,
            ollama=ollama_config,
            batch=batch_config,
            cache=cache_config,
            monitoring=monitoring_config,
            dynamodb_table_name=os.getenv(
                "DYNAMODB_TABLE_NAME", "receipt-dev"
            ),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            environment=os.getenv("ENVIRONMENT", "development"),
        )

    def validate(self) -> List[str]:
        """Validate the entire configuration"""
        errors = []

        # Validate Ollama config
        if self.ollama:
            errors.extend(self.ollama.validate())
        else:
            errors.append("Ollama configuration is required")

        # Validate monitoring
        if self.monitoring and self.monitoring.langsmith_enabled:
            if not self.monitoring.langsmith_api_key:
                errors.append(
                    "LangSmith API key required when tracing is enabled"
                )

        return errors

    def to_dict(self) -> dict:
        """Convert configuration to dictionary"""
        return {
            "mode": self.mode.value,
            "ollama": (
                {
                    "base_url": self.ollama.base_url,
                    "model": self.ollama.model,
                    "is_turbo": self.ollama.is_turbo,
                    "temperature": self.ollama.temperature,
                    "timeout": self.ollama.timeout,
                    "api_key_set": bool(self.ollama.api_key),
                }
                if self.ollama
                else None
            ),
            "batch": (
                {
                    "enabled": self.batch.enabled,
                    "batch_size": self.batch.batch_size,
                    "max_concurrent": self.batch.max_concurrent,
                }
                if self.batch
                else None
            ),
            "cache": (
                {
                    "enabled": self.cache.enabled,
                    "max_entries": self.cache.max_entries,
                    "ttl_seconds": self.cache.ttl_seconds,
                }
                if self.cache
                else None
            ),
            "monitoring": (
                {
                    "enabled": self.monitoring.enabled,
                    "langsmith_enabled": self.monitoring.langsmith_enabled,
                    "langsmith_project": self.monitoring.langsmith_project,
                }
                if self.monitoring
                else None
            ),
            "environment": self.environment,
            "debug": self.debug,
        }


# Convenience function
def get_ollama_config() -> ValidationConfig:
    """Get validation configuration from environment"""
    return ValidationConfig.from_env()


# For backward compatibility with old imports
def get_config() -> ValidationConfig:
    """Get validated configuration instance"""
    config = ValidationConfig.from_env()

    # Validate configuration
    errors = config.validate()
    if errors:
        error_msg = "\n".join([f"  - {error}" for error in errors])
        raise ValueError(f"Configuration validation failed:\n{error_msg}")

    return config
