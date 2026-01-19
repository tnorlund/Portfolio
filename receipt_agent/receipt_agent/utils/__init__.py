"""Receipt Agent utility modules."""

from .chroma_helpers import (
    LabelDistributionStats,
    LabelEvidence,
    MerchantBreakdown,
    SimilarityDistribution,
    SimilarWordEvidence,
    ValidationRecord,
    build_word_chroma_id,
    compute_label_consensus,
    compute_label_distribution,
    compute_merchant_breakdown,
    compute_similarity_distribution,
    describe_position,
    enrich_evidence_with_dynamo_reasoning,
    format_label_evidence_for_prompt,
    load_dual_chroma_from_s3,
    parse_chroma_id,
    query_label_evidence,
    query_similar_words,
)
from .llm_factory import (
    # Primary exports
    LLMRateLimitError,
    LLMInvoker,
    EmptyResponseError,
    create_llm,
    create_llm_invoker,
    create_llm_from_settings,
    create_production_invoker,
    is_rate_limit_error,
    is_service_error,
    is_timeout_error,
    is_retriable_error,
    # Backward compatibility aliases (kept for existing code)
    RateLimitedLLMInvoker,  # alias for LLMInvoker
    ResilientLLM,  # alias for LLMInvoker
    create_resilient_llm,  # alias for create_llm_invoker
    is_fallback_error,  # alias for is_retriable_error
    is_server_error,  # alias for is_service_error
)

__all__ = [
    # ChromaDB utilities
    "LabelDistributionStats",
    "LabelEvidence",
    "MerchantBreakdown",
    "SimilarityDistribution",
    "SimilarWordEvidence",
    "ValidationRecord",
    "build_word_chroma_id",
    "compute_label_consensus",
    "compute_label_distribution",
    "compute_merchant_breakdown",
    "compute_similarity_distribution",
    "describe_position",
    "enrich_evidence_with_dynamo_reasoning",
    "format_label_evidence_for_prompt",
    "load_dual_chroma_from_s3",
    "parse_chroma_id",
    "query_label_evidence",
    "query_similar_words",
    # LLM Factory - Primary
    "LLMRateLimitError",
    "LLMInvoker",
    "EmptyResponseError",
    "create_llm",
    "create_llm_invoker",
    "create_llm_from_settings",
    "create_production_invoker",
    "is_rate_limit_error",
    "is_service_error",
    "is_timeout_error",
    "is_retriable_error",
    # LLM Factory - Backward Compatibility Aliases
    "RateLimitedLLMInvoker",
    "ResilientLLM",
    "create_resilient_llm",
    "is_fallback_error",
    "is_server_error",
]
