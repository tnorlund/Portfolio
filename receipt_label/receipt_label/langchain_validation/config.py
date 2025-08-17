"""
LangChain Validation Configuration Module
=========================================

This module provides comprehensive configuration management for the LangChain
validation system, supporting multiple LLM providers (OpenAI, Ollama) with
environment-based configuration and validation.

Features:
- Multi-provider LLM configuration
- Environment variable validation
- Cost optimization settings
- Monitoring and observability setup
- Production-ready defaults
"""

import os
import warnings
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import json


class LLMProvider(Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    OLLAMA = "ollama"


class ValidationMode(Enum):
    """Validation processing modes"""
    REALTIME = "realtime"
    BATCH = "batch"
    SMART_BATCH = "smart_batch"


@dataclass
class OpenAIConfig:
    """OpenAI-specific configuration"""
    api_key: str
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: Optional[int] = 1000
    timeout: int = 30
    max_retries: int = 3
    
    # Cost controls
    max_tokens_per_minute: Optional[int] = None
    max_requests_per_minute: Optional[int] = None
    
    def validate(self) -> List[str]:
        """Validate OpenAI configuration"""
        errors = []
        
        if not self.api_key:
            errors.append("OpenAI API key is required")
        elif not self.api_key.startswith(('sk-', 'sk-proj-')):
            errors.append("OpenAI API key format appears invalid")
        
        if self.temperature < 0 or self.temperature > 2:
            errors.append("Temperature must be between 0 and 2")
        
        if self.max_tokens and self.max_tokens <= 0:
            errors.append("Max tokens must be positive")
        
        return errors


@dataclass  
class OllamaConfig:
    """Ollama-specific configuration"""
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    temperature: float = 0.0
    timeout: int = 60  # Ollama can be slower
    max_retries: int = 3
    
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
        
        return errors


@dataclass
class BatchConfig:
    """Batch processing configuration"""
    enabled: bool = True
    batch_size: int = 10
    max_concurrent: int = 5
    delay_between_batches: float = 1.0
    
    # Smart batching
    urgent_threshold: int = 1  # Process immediately if <= this many items
    cost_optimization: bool = True
    
    def validate(self) -> List[str]:
        """Validate batch configuration"""
        errors = []
        
        if self.batch_size <= 0:
            errors.append("Batch size must be positive")
        
        if self.max_concurrent <= 0:
            errors.append("Max concurrent must be positive")
        
        if self.delay_between_batches < 0:
            errors.append("Delay between batches cannot be negative")
        
        return errors


@dataclass
class CacheConfig:
    """Caching configuration"""
    enabled: bool = True
    max_entries: int = 1000
    ttl_seconds: int = 3600  # 1 hour
    cache_successful_only: bool = True
    
    def validate(self) -> List[str]:
        """Validate cache configuration"""
        errors = []
        
        if self.max_entries <= 0:
            errors.append("Max cache entries must be positive")
        
        if self.ttl_seconds <= 0:
            errors.append("Cache TTL must be positive")
        
        return errors


@dataclass
class MonitoringConfig:
    """Monitoring and observability configuration"""
    enabled: bool = True
    
    # LangSmith configuration
    langsmith_enabled: bool = False
    langsmith_api_key: Optional[str] = None
    langsmith_project: str = "receipt-validation"
    
    # Metrics collection
    collect_metrics: bool = True
    metrics_file: Optional[str] = None
    
    # CloudWatch (AWS)
    cloudwatch_enabled: bool = False
    cloudwatch_namespace: str = "ReceiptValidation"
    
    def validate(self) -> List[str]:
        """Validate monitoring configuration"""
        errors = []
        
        if self.langsmith_enabled and not self.langsmith_api_key:
            errors.append("LangSmith API key required when LangSmith is enabled")
        
        return errors


@dataclass
class ValidationConfig:
    """Main configuration for LangChain validation system"""
    
    # Provider selection
    provider: LLMProvider = LLMProvider.OPENAI
    mode: ValidationMode = ValidationMode.SMART_BATCH
    
    # Provider-specific configs
    openai: OpenAIConfig = field(default_factory=lambda: OpenAIConfig(""))
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    
    # Feature configs
    batch: BatchConfig = field(default_factory=BatchConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Database configuration
    dynamodb_table_name: str = "receipt-dev"
    chroma_persist_path: Optional[str] = "./chroma_db"
    
    # Global settings
    debug: bool = False
    environment: str = "development"
    
    @classmethod
    def from_env(cls) -> "ValidationConfig":
        """Load configuration from environment variables"""
        
        # Provider selection
        provider_str = os.getenv("LLM_PROVIDER", "openai").lower()
        try:
            provider = LLMProvider(provider_str)
        except ValueError:
            warnings.warn(f"Invalid LLM provider '{provider_str}', defaulting to OpenAI")
            provider = LLMProvider.OPENAI
        
        # Validation mode
        mode_str = os.getenv("VALIDATION_MODE", "smart_batch").lower()
        try:
            mode = ValidationMode(mode_str)
        except ValueError:
            warnings.warn(f"Invalid validation mode '{mode_str}', defaulting to smart_batch")
            mode = ValidationMode.SMART_BATCH
        
        # OpenAI configuration
        openai_config = OpenAIConfig(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.0")),
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "1000")) if os.getenv("OPENAI_MAX_TOKENS") else None,
            timeout=int(os.getenv("OPENAI_TIMEOUT", "30")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "3")),
            max_tokens_per_minute=int(os.getenv("OPENAI_MAX_TOKENS_PER_MINUTE")) if os.getenv("OPENAI_MAX_TOKENS_PER_MINUTE") else None,
            max_requests_per_minute=int(os.getenv("OPENAI_MAX_REQUESTS_PER_MINUTE")) if os.getenv("OPENAI_MAX_REQUESTS_PER_MINUTE") else None,
        )
        
        # Ollama configuration
        ollama_config = OllamaConfig(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.0")),
            timeout=int(os.getenv("OLLAMA_TIMEOUT", "60")),
            max_retries=int(os.getenv("OLLAMA_MAX_RETRIES", "3")),
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX")) if os.getenv("OLLAMA_NUM_CTX") else None,
            num_predict=int(os.getenv("OLLAMA_NUM_PREDICT")) if os.getenv("OLLAMA_NUM_PREDICT") else None,
            top_k=int(os.getenv("OLLAMA_TOP_K")) if os.getenv("OLLAMA_TOP_K") else None,
            top_p=float(os.getenv("OLLAMA_TOP_P")) if os.getenv("OLLAMA_TOP_P") else None,
        )
        
        # Batch configuration
        batch_config = BatchConfig(
            enabled=os.getenv("BATCH_ENABLED", "true").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "10")),
            max_concurrent=int(os.getenv("BATCH_MAX_CONCURRENT", "5")),
            delay_between_batches=float(os.getenv("BATCH_DELAY", "1.0")),
            urgent_threshold=int(os.getenv("BATCH_URGENT_THRESHOLD", "1")),
            cost_optimization=os.getenv("BATCH_COST_OPTIMIZATION", "true").lower() == "true",
        )
        
        # Cache configuration
        cache_config = CacheConfig(
            enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
            max_entries=int(os.getenv("CACHE_MAX_ENTRIES", "1000")),
            ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
            cache_successful_only=os.getenv("CACHE_SUCCESSFUL_ONLY", "true").lower() == "true",
        )
        
        # Monitoring configuration  
        monitoring_config = MonitoringConfig(
            enabled=os.getenv("MONITORING_ENABLED", "true").lower() == "true",
            langsmith_enabled=os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true",
            langsmith_api_key=os.getenv("LANGCHAIN_API_KEY"),
            langsmith_project=os.getenv("LANGCHAIN_PROJECT", "receipt-validation"),
            collect_metrics=os.getenv("COLLECT_METRICS", "true").lower() == "true",
            metrics_file=os.getenv("METRICS_FILE"),
            cloudwatch_enabled=os.getenv("CLOUDWATCH_ENABLED", "false").lower() == "true",
            cloudwatch_namespace=os.getenv("CLOUDWATCH_NAMESPACE", "ReceiptValidation"),
        )
        
        return cls(
            provider=provider,
            mode=mode,
            openai=openai_config,
            ollama=ollama_config,
            batch=batch_config,
            cache=cache_config,
            monitoring=monitoring_config,
            dynamodb_table_name=os.getenv("DYNAMODB_TABLE_NAME", "receipt-dev"),
            chroma_persist_path=os.getenv("CHROMA_PERSIST_PATH", "./chroma_db"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            environment=os.getenv("ENVIRONMENT", "development"),
        )
    
    def validate(self) -> List[str]:
        """Validate the entire configuration"""
        errors = []
        
        # Validate provider-specific configs
        if self.provider == LLMProvider.OPENAI:
            errors.extend(self.openai.validate())
        elif self.provider == LLMProvider.OLLAMA:
            errors.extend(self.ollama.validate())
        
        # Validate feature configs
        errors.extend(self.batch.validate())
        errors.extend(self.cache.validate())
        errors.extend(self.monitoring.validate())
        
        # Validate database config
        if not self.dynamodb_table_name:
            errors.append("DynamoDB table name is required")
        
        return errors
    
    def get_active_llm_config(self) -> Dict[str, Any]:
        """Get configuration for the active LLM provider"""
        if self.provider == LLMProvider.OPENAI:
            return {
                "provider": "openai",
                "model": self.openai.model,
                "api_key": self.openai.api_key[:8] + "..." if self.openai.api_key else "",
                "temperature": self.openai.temperature,
                "max_tokens": self.openai.max_tokens,
                "timeout": self.openai.timeout,
            }
        else:
            return {
                "provider": "ollama",
                "model": self.ollama.model,
                "base_url": self.ollama.base_url,
                "temperature": self.ollama.temperature,
                "timeout": self.ollama.timeout,
            }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for serialization"""
        return {
            "provider": self.provider.value,
            "mode": self.mode.value,
            "openai": {
                "model": self.openai.model,
                "temperature": self.openai.temperature,
                "max_tokens": self.openai.max_tokens,
                "timeout": self.openai.timeout,
                "api_key_set": bool(self.openai.api_key),
            },
            "ollama": {
                "base_url": self.ollama.base_url,
                "model": self.ollama.model,
                "temperature": self.ollama.temperature,
                "timeout": self.ollama.timeout,
            },
            "batch": {
                "enabled": self.batch.enabled,
                "batch_size": self.batch.batch_size,
                "max_concurrent": self.batch.max_concurrent,
                "cost_optimization": self.batch.cost_optimization,
            },
            "cache": {
                "enabled": self.cache.enabled,
                "max_entries": self.cache.max_entries,
                "ttl_seconds": self.cache.ttl_seconds,
            },
            "monitoring": {
                "enabled": self.monitoring.enabled,
                "langsmith_enabled": self.monitoring.langsmith_enabled,
                "collect_metrics": self.monitoring.collect_metrics,
            },
            "database": {
                "dynamodb_table": self.dynamodb_table_name,
                "chroma_persist_path": self.chroma_persist_path,
            },
            "environment": self.environment,
            "debug": self.debug,
        }
    
    def save_to_file(self, filepath: str):
        """Save configuration to JSON file"""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load_from_file(cls, filepath: str) -> "ValidationConfig":
        """Load configuration from JSON file (supplemented with env vars)"""
        with open(filepath, 'r') as f:
            config_dict = json.load(f)
        
        # Start with environment-based config
        config = cls.from_env()
        
        # Override with file values where present
        if "provider" in config_dict:
            config.provider = LLMProvider(config_dict["provider"])
        
        if "mode" in config_dict:
            config.mode = ValidationMode(config_dict["mode"])
        
        # Update nested configs as needed...
        # (Implementation would depend on specific override requirements)
        
        return config


def get_config() -> ValidationConfig:
    """Get validated configuration instance"""
    config = ValidationConfig.from_env()
    
    # Validate configuration
    errors = config.validate()
    if errors:
        error_msg = "\n".join([f"  - {error}" for error in errors])
        raise ValueError(f"Configuration validation failed:\n{error_msg}")
    
    return config


def print_config_summary(config: Optional[ValidationConfig] = None):
    """Print a summary of the current configuration"""
    if config is None:
        config = get_config()
    
    print("=== LangChain Validation Configuration ===")
    print(f"Environment: {config.environment}")
    print(f"Debug: {config.debug}")
    print(f"Provider: {config.provider.value}")
    print(f"Mode: {config.mode.value}")
    
    print("\n--- LLM Configuration ---")
    llm_config = config.get_active_llm_config()
    for key, value in llm_config.items():
        print(f"{key}: {value}")
    
    print("\n--- Feature Configuration ---")
    print(f"Batch processing: {config.batch.enabled}")
    print(f"Caching: {config.cache.enabled}")
    print(f"Monitoring: {config.monitoring.enabled}")
    print(f"LangSmith: {config.monitoring.langsmith_enabled}")
    
    print("\n--- Database Configuration ---")
    print(f"DynamoDB table: {config.dynamodb_table_name}")
    print(f"ChromaDB path: {config.chroma_persist_path}")


# Example usage
if __name__ == "__main__":
    try:
        config = get_config()
        print_config_summary(config)
        
        # Demonstrate config serialization
        print("\n--- Full Configuration ---")
        print(json.dumps(config.to_dict(), indent=2))
        
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("\nPlease check your environment variables.")