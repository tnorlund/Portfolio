"""
Development configuration for local receipt processing.

This module provides configuration settings optimized for local development,
including paths for local data, API stubbing toggles, and caching options.
"""

import os
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, field


@dataclass
class DevelopmentConfig:
    """Configuration for local development environment."""
    
    # Local data settings
    local_data_dir: str = field(default_factory=lambda: os.getenv(
        "RECEIPT_LOCAL_DATA_DIR",
        "./receipt_data"
    ))
    
    # API stubbing settings
    use_stub_apis: bool = field(default_factory=lambda: os.getenv(
        "USE_STUB_APIS",
        "false"
    ).lower() == "true")
    
    stub_openai: bool = field(default_factory=lambda: os.getenv(
        "STUB_OPENAI",
        "false"
    ).lower() == "true")
    
    stub_pinecone: bool = field(default_factory=lambda: os.getenv(
        "STUB_PINECONE",
        "false"
    ).lower() == "true")
    
    stub_places_api: bool = field(default_factory=lambda: os.getenv(
        "STUB_PLACES_API",
        "false"
    ).lower() == "true")
    
    stub_dynamodb: bool = field(default_factory=lambda: os.getenv(
        "STUB_DYNAMODB",
        "false"
    ).lower() == "true")
    
    # Local caching settings
    enable_local_cache: bool = field(default_factory=lambda: os.getenv(
        "ENABLE_LOCAL_CACHE",
        "true"
    ).lower() == "true")
    
    cache_dir: str = field(default_factory=lambda: os.getenv(
        "RECEIPT_CACHE_DIR",
        "./.cache/receipt_label"
    ))
    
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv(
        "CACHE_TTL_SECONDS",
        "3600"  # 1 hour default
    )))
    
    # Cost tracking settings
    track_api_costs: bool = field(default_factory=lambda: os.getenv(
        "TRACK_API_COSTS",
        "true"
    ).lower() == "true")
    
    cost_log_file: str = field(default_factory=lambda: os.getenv(
        "COST_LOG_FILE",
        "./logs/api_costs.log"
    ))
    
    # Development mode settings
    verbose_logging: bool = field(default_factory=lambda: os.getenv(
        "VERBOSE_LOGGING",
        "false"
    ).lower() == "true")
    
    log_api_requests: bool = field(default_factory=lambda: os.getenv(
        "LOG_API_REQUESTS",
        "false"
    ).lower() == "true")
    
    # Performance settings
    max_parallel_patterns: int = field(default_factory=lambda: int(os.getenv(
        "MAX_PARALLEL_PATTERNS",
        "4"
    )))
    
    pattern_timeout_seconds: int = field(default_factory=lambda: int(os.getenv(
        "PATTERN_TIMEOUT_SECONDS",
        "30"
    )))
    
    # Sample data settings
    sample_data_size: int = field(default_factory=lambda: int(os.getenv(
        "SAMPLE_DATA_SIZE",
        "20"
    )))
    
    download_images: bool = field(default_factory=lambda: os.getenv(
        "DOWNLOAD_IMAGES",
        "false"
    ).lower() == "true")
    
    def __post_init__(self):
        """Validate and create necessary directories."""
        # Create directories if they don't exist
        if self.enable_local_cache:
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
            
        if self.track_api_costs:
            Path(self.cost_log_file).parent.mkdir(parents=True, exist_ok=True)
    
    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return os.getenv("ENVIRONMENT", "development").lower() in ["dev", "development", "local"]
    
    @property
    def stub_all_apis(self) -> bool:
        """Check if all APIs should be stubbed."""
        return self.use_stub_apis or all([
            self.stub_openai,
            self.stub_pinecone,
            self.stub_places_api,
            self.stub_dynamodb
        ])
    
    def get_api_stub_config(self) -> Dict[str, bool]:
        """Get configuration for which APIs to stub."""
        if self.use_stub_apis:
            # Override individual settings if master switch is on
            return {
                "openai": True,
                "pinecone": True,
                "places": True,
                "dynamodb": True
            }
        
        return {
            "openai": self.stub_openai,
            "pinecone": self.stub_pinecone,
            "places": self.stub_places_api,
            "dynamodb": self.stub_dynamodb
        }
    
    def get_cache_path(self, key: str) -> Path:
        """Get cache file path for a given key."""
        cache_path = Path(self.cache_dir) / f"{key}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        return cache_path
    
    @classmethod
    def from_env(cls) -> "DevelopmentConfig":
        """Create configuration from environment variables."""
        return cls()
    
    def to_dict(self) -> Dict:
        """Convert configuration to dictionary."""
        return {
            "local_data_dir": self.local_data_dir,
            "use_stub_apis": self.use_stub_apis,
            "api_stubs": self.get_api_stub_config(),
            "cache": {
                "enabled": self.enable_local_cache,
                "directory": self.cache_dir,
                "ttl_seconds": self.cache_ttl_seconds
            },
            "cost_tracking": {
                "enabled": self.track_api_costs,
                "log_file": self.cost_log_file
            },
            "logging": {
                "verbose": self.verbose_logging,
                "log_api_requests": self.log_api_requests
            },
            "performance": {
                "max_parallel_patterns": self.max_parallel_patterns,
                "pattern_timeout_seconds": self.pattern_timeout_seconds
            },
            "sample_data": {
                "size": self.sample_data_size,
                "download_images": self.download_images
            }
        }


# Global instance
_config: Optional[DevelopmentConfig] = None


def get_development_config() -> DevelopmentConfig:
    """Get or create the global development configuration."""
    global _config
    if _config is None:
        _config = DevelopmentConfig.from_env()
    return _config


def reset_config():
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None