"""
Configuration settings for receipt_agent.

Uses pydantic-settings for environment variable management with validation.
"""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="RECEIPT_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM Configuration (Ollama Cloud)
    ollama_base_url: str = Field(
        default="https://api.ollama.ai",
        description="Base URL for Ollama Cloud API",
    )
    ollama_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key for Ollama Cloud",
    )
    ollama_model: str = Field(
        default="llama3.1:8b",
        description="Model to use for agent reasoning",
    )

    # Alternative: OpenAI for embeddings
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="OpenAI API key for embeddings",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model",
    )

    # ChromaDB Configuration
    chroma_persist_directory: Optional[str] = Field(
        default=None,
        description="Local ChromaDB persistence directory",
    )
    chroma_http_url: Optional[str] = Field(
        default=None,
        description="ChromaDB HTTP server URL",
    )

    # DynamoDB Configuration
    dynamo_table_name: str = Field(
        default="receipts",
        description="DynamoDB table name",
    )
    aws_region: str = Field(
        default="us-west-2",
        description="AWS region for DynamoDB",
    )

    # Google Places API
    google_places_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Google Places API key for verification",
    )

    # LangSmith Configuration
    langsmith_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="LangSmith API key for tracing",
        alias="LANGCHAIN_API_KEY",  # LangSmith uses this env var
    )
    langsmith_project: str = Field(
        default="receipt-agent",
        description="LangSmith project name",
        alias="LANGCHAIN_PROJECT",
    )
    langsmith_tracing: bool = Field(
        default=True,
        description="Enable LangSmith tracing",
        alias="LANGCHAIN_TRACING_V2",
    )

    # Agent Configuration
    max_iterations: int = Field(
        default=10,
        description="Maximum agent iterations",
        ge=1,
        le=50,
    )
    similarity_threshold: float = Field(
        default=0.75,
        description="Minimum similarity for validation",
        ge=0.0,
        le=1.0,
    )
    min_matches_for_validation: int = Field(
        default=3,
        description="Minimum similar receipts needed",
        ge=1,
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

