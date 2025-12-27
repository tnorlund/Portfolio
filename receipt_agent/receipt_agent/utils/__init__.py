"""Receipt Agent utility modules."""

from .chroma_helpers import (
    LabelDistributionStats,
    MerchantBreakdown,
    SimilarityDistribution,
    SimilarWordEvidence,
    ValidationRecord,
    build_word_chroma_id,
    compute_label_distribution,
    compute_merchant_breakdown,
    compute_similarity_distribution,
    describe_position,
    enrich_evidence_with_dynamo_reasoning,
    load_dual_chroma_from_s3,
    parse_chroma_id,
    query_similar_words,
)
from .llm_factory import (
    BothProvidersFailedError,
    LLMProvider,
    ResilientLLM,
    create_llm,
    create_llm_from_settings,
    create_production_invoker,
    create_resilient_llm,
    get_default_provider,
    is_fallback_error,
)
from .ollama_rate_limit import (
    OllamaCircuitBreaker,
    OllamaRateLimitError,
    RateLimitedLLMInvoker,
    is_rate_limit_error,
    is_server_error,
    is_timeout_error,
)

__all__ = [
    # ChromaDB utilities
    "LabelDistributionStats",
    "MerchantBreakdown",
    "SimilarityDistribution",
    "SimilarWordEvidence",
    "ValidationRecord",
    "build_word_chroma_id",
    "compute_label_distribution",
    "compute_merchant_breakdown",
    "compute_similarity_distribution",
    "describe_position",
    "enrich_evidence_with_dynamo_reasoning",
    "load_dual_chroma_from_s3",
    "parse_chroma_id",
    "query_similar_words",
    # LLM Factory
    "BothProvidersFailedError",
    "LLMProvider",
    "ResilientLLM",
    "create_llm",
    "create_llm_from_settings",
    "create_production_invoker",
    "create_resilient_llm",
    "get_default_provider",
    "is_fallback_error",
    # Rate limit utilities
    "OllamaCircuitBreaker",
    "OllamaRateLimitError",
    "RateLimitedLLMInvoker",
    "is_rate_limit_error",
    "is_server_error",
    "is_timeout_error",
]
