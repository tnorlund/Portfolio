# New modular components
from .agent import MerchantValidationAgent

# Import clustering utilities
from .clustering import get_score
from .handler import MerchantValidationHandler, create_validation_handler

# Import from modular structure
from .merchant_validation import (
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
    choose_canonical_metadata,
    cluster_by_metadata,
    collapse_canonical_aliases,
    extract_candidate_merchant_fields,
    get_receipt_details,
    infer_merchant_with_gpt,
    is_match_found,
    is_valid_google_match,
    list_all_receipt_metadatas,
    list_receipts_for_merchant_validation,
    merge_place_id_aliases_by_address,
    persist_alias_updates,
    query_google_places,
    query_records_by_place_id,
    retry_google_search_with_inferred_data,
    update_items_with_canonical,
    validate_match_with_gpt,
    write_receipt_metadata_to_dynamo,
)
from .result_processor import (
    build_receipt_metadata_from_partial_result,
    extract_best_partial_match,
    sanitize_metadata_strings,
    sanitize_string,
)

# Import utilities from the utils module
from .utils import (
    get_address_similarity,
    get_name_similarity,
    get_phone_similarity,
    normalize_address,
    normalize_phone,
    normalize_text,
    preprocess_for_comparison,
)

__all__ = [
    # Original functions
    "list_receipts_for_merchant_validation",
    "get_receipt_details",
    "extract_candidate_merchant_fields",
    "query_google_places",
    "is_valid_google_match",
    "infer_merchant_with_gpt",
    "validate_match_with_gpt",
    "extract_candidate_merchant_fields",
    "retry_google_search_with_inferred_data",
    "build_receipt_metadata_from_result",
    "build_receipt_metadata_from_result_no_match",
    "write_receipt_metadata_to_dynamo",
    "is_match_found",
    "list_all_receipt_metadatas",
    "update_items_with_canonical",
    "cluster_by_metadata",
    "choose_canonical_metadata",
    "get_score",
    "normalize_address",
    "normalize_phone",
    "normalize_text",
    "preprocess_for_comparison",
    "get_name_similarity",
    "get_address_similarity",
    "get_phone_similarity",
    "query_records_by_place_id",
    "collapse_canonical_aliases",
    "persist_alias_updates",
    "merge_place_id_aliases_by_address",
    # New modular components
    "MerchantValidationAgent",
    "MerchantValidationHandler",
    "create_validation_handler",
    "extract_best_partial_match",
    "build_receipt_metadata_from_partial_result",
    "sanitize_metadata_strings",
    "sanitize_string",
]
