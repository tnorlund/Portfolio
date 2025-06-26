"""
Merchant validation orchestration module.

This module provides the main orchestration functions and imports from specialized modules.
It maintains backward compatibility while providing a cleaner modular structure.
"""

# Import all functions from specialized modules
from .clustering import (choose_canonical_metadata, cluster_by_metadata,
                         collapse_canonical_aliases,
                         merge_place_id_aliases_by_address,
                         update_items_with_canonical)
from .data_access import (get_receipt_details, list_all_receipt_metadatas,
                          list_receipt_metadatas,
                          list_receipts_for_merchant_validation,
                          persist_alias_updates, query_records_by_place_id,
                          write_receipt_metadata_to_dynamo)
from .field_extraction import extract_candidate_merchant_fields
from .google_places import (is_match_found, is_valid_google_match,
                            query_google_places,
                            retry_google_search_with_inferred_data)
from .gpt_integration import infer_merchant_with_gpt, validate_match_with_gpt
from .metadata_builder import (build_receipt_metadata_from_result,
                               build_receipt_metadata_from_result_no_match)

# Re-export all functions for backward compatibility
__all__ = [
    # Data access functions
    "list_receipt_metadatas",
    "list_receipts_for_merchant_validation",
    "get_receipt_details",
    "write_receipt_metadata_to_dynamo",
    "query_records_by_place_id",
    "list_all_receipt_metadatas",
    "persist_alias_updates",
    # Field extraction
    "extract_candidate_merchant_fields",
    # Google Places integration
    "query_google_places",
    "is_match_found",
    "is_valid_google_match",
    "retry_google_search_with_inferred_data",
    # GPT integration
    "validate_match_with_gpt",
    "infer_merchant_with_gpt",
    # Metadata builders
    "build_receipt_metadata_from_result",
    "build_receipt_metadata_from_result_no_match",
    # Clustering and canonicalization
    "cluster_by_metadata",
    "choose_canonical_metadata",
    "update_items_with_canonical",
    "collapse_canonical_aliases",
    "merge_place_id_aliases_by_address",
]
