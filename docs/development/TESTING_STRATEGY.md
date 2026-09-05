> **Historical note (Sept 2026):** the ChromaDB legs described in this document were retired by the vector-store teardown (see `docs/chroma-removal/`). Vector similarity now runs against native DynamoDB embedding items via `receipt_embeddings`; the `receipt_chroma` package no longer exists.

"""
Comprehensive Testing Strategy for ChromaDB Compaction System

This document outlines what should be tested where to ensure we're testing
real business logic rather than mocked functionality.
"""

# =============================================================================
# BUSINESS LOGIC TESTING BOUNDARIES
# =============================================================================

"""
🎯 CORE PRINCIPLE: Test business logic where it lives, not where it's used.

The compaction lambda is primarily an orchestration layer that calls
receipt_label business logic. We should test:

1. receipt_label business logic → receipt_label package tests
2. Lambda orchestration logic → Lambda tests  
3. Cross-service integration → Integration tests
"""

# =============================================================================
# WHAT BELONGS IN RECEIPT_LABEL PACKAGE TESTS
# =============================================================================

"""
✅ ChromaDB Operations (receipt_label.utils.chroma_client.ChromaDBClient)
   - Collection management
   - Metadata updates
   - Vector operations
   - Query operations
   - Error handling for ChromaDB failures

✅ S3 Atomic Operations (receipt_label.utils.chroma_s3_helpers)
   - download_snapshot_atomic()
   - upload_snapshot_atomic()
   - Snapshot integrity verification
   - Atomic operation guarantees
   - S3 error handling and retries

✅ ChromaDB Compactor (receipt_label.utils.chroma_compactor.ChromaCompactor)
   - Delta compaction algorithms
   - Collection merging logic
   - Distributed locking
   - Snapshot management
   - Performance characteristics

✅ Receipt Labeling Logic (receipt_label.core.labeler.ReceiptLabeler)
   - AI service integrations
   - Label validation
   - Merchant validation
   - Decision engine logic
"""

# =============================================================================
# WHAT BELONGS IN LAMBDA TESTS
# =============================================================================

"""
✅ Lambda Orchestration Logic
   - SQS message processing
   - DynamoDB query orchestration
   - Error handling and retries
   - Lambda-specific concerns (timeouts, memory)

✅ AWS Service Integration
   - SQS message routing
   - DynamoDB query patterns
   - Environment variable handling
   - Lambda response formatting

✅ Cross-Service Workflows
   - DynamoDB → SQS → ChromaDB pipeline
   - Error propagation between services
   - Retry mechanisms
   - Partial failure handling
"""

# =============================================================================
# WHAT BELONGS IN INTEGRATION TESTS
# =============================================================================

"""
✅ End-to-End Workflows
   - Complete DynamoDB → SQS → S3 → ChromaDB pipeline
   - Real data patterns and volumes
   - Performance characteristics
   - Data consistency guarantees

✅ Cross-Service Error Scenarios
   - S3 failures during snapshot operations
   - DynamoDB failures during queries
   - ChromaDB failures during updates
   - Network failures and timeouts

✅ Production-Like Scenarios
   - Concurrent access patterns
   - Large dataset handling
   - Memory and CPU constraints
   - Real-world error conditions
"""

# =============================================================================
# CURRENT TESTING GAPS
# =============================================================================

"""
🚨 Critical Gaps in Current Testing:

1. ChromaDB Business Logic
   - We're mocking ChromaDBClient instead of testing real operations
   - Missing tests for metadata update patterns
   - Missing tests for collection state management
   - Missing tests for vector operations

2. S3 Atomic Operations
   - We're mocking S3 operations instead of testing real atomic guarantees
   - Missing tests for snapshot integrity verification
   - Missing tests for concurrent access scenarios
   - Missing tests for S3 error handling

3. Cross-Service Integration
   - Missing tests for DynamoDB → ChromaDB ID construction
   - Missing tests for S3 → ChromaDB snapshot loading
   - Missing tests for error propagation between services
   - Missing tests for retry mechanisms

4. Production Scenarios
   - Missing tests for real data patterns
   - Missing tests for performance characteristics
   - Missing tests for memory constraints
   - Missing tests for concurrent access
"""

# =============================================================================
# RECOMMENDED TESTING IMPLEMENTATION
# =============================================================================

"""
🎯 Phase 1: Fix receipt_label Package Tests
   - Test ChromaDBClient with real ChromaDB operations
   - Test S3 atomic operations with real S3 (using moto)
   - Test ChromaCompactor with real compaction scenarios
   - Use real data patterns that match production

🎯 Phase 2: Fix Lambda Tests
   - Test Lambda orchestration with real receipt_label calls
   - Test AWS service integration with real services (using moto)
   - Test error handling and retry mechanisms
   - Test Lambda-specific concerns (timeouts, memory)

🎯 Phase 3: Add Integration Tests
   - Test end-to-end workflows with real services
   - Test cross-service error scenarios
   - Test production-like scenarios
   - Test performance characteristics

🎯 Phase 4: Add Contract Tests
   - Test DynamoDB query contracts
   - Test S3 operation contracts
   - Test ChromaDB API contracts
   - Validate data transformations
"""

# =============================================================================
# EXAMPLE: PROPER TESTING APPROACH
# =============================================================================

"""
# ❌ BAD: Testing mocked functionality
def test_chromadb_update():
    mock_collection = MagicMock()
    mock_collection.update.return_value = True
    result = update_metadata(mock_collection, ...)
    assert result == True  # Testing nothing real!

# ✅ GOOD: Testing real business logic
@mock_aws
def test_chromadb_update():
    # Create real ChromaDB instance with moto S3
    chroma_client = ChromaDBClient(persist_directory=temp_dir)
    
    # Insert real test data
    chroma_client.add_collection("test_collection")
    chroma_client.add(
        ids=["test_id"],
        embeddings=[[0.1, 0.2, 0.3]],
        documents=["test document"],
        metadatas=[{"merchant": "Target"}]
    )
    
    # Test real metadata update
    result = update_metadata(chroma_client.get_collection("test_collection"), ...)
    
    # Verify real changes
    updated = chroma_client.get_collection("test_collection").get(ids=["test_id"])
    assert updated["metadatas"][0]["merchant"] == "Updated Target"
"""

# =============================================================================
# CONCLUSION
# =============================================================================

"""
The key insight is that we need to test REAL business logic, not mocked functionality.

- receipt_label package should test its own business logic with real operations
- Lambda tests should test orchestration with real receipt_label calls
- Integration tests should test cross-service workflows with real services

This ensures we're testing what actually happens in production, not what we think happens.
"""
