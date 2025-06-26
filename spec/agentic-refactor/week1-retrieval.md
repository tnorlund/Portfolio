# Week 1: Add Retrieval to Current ReceiptLabeler

## Overview

This week focuses on adding retrieval-augmented labeling to the existing `ReceiptLabeler` without major architectural changes. This provides immediate value by leveraging historical receipt data to improve labeling accuracy.

## Goals

1. Query Pinecone for similar receipts during labeling
2. Pass similar receipts as context to GPT analysis
3. Improve labeling accuracy for repeat merchants
4. Measure impact on validation success rates

## Implementation Plan

### Step 1: Add Retrieval Method to ReceiptLabeler

```python
# In receipt_label/core/labeler.py

def _get_similar_receipts(
    self,
    receipt: Receipt,
    receipt_words: List[ReceiptWord],
    max_results: int = 5
) -> List[Dict]:
    """Retrieve similar receipts from Pinecone for context.

    Args:
        receipt: Current receipt being processed
        receipt_words: Words from the receipt
        max_results: Maximum number of similar receipts to retrieve

    Returns:
        List of similar receipt metadata including labels and merchant info
    """
    if not hasattr(self, 'client_manager') or not self.client_manager.pinecone:
        return []

    try:
        # Create embedding from receipt text
        receipt_text = " ".join([word.text for word in receipt_words])
        embedding = self._create_receipt_embedding(receipt_text)

        # Query Pinecone
        results = self.client_manager.pinecone.query(
            vector=embedding,
            top_k=max_results,
            include_metadata=True,
            filter={
                "type": "receipt",
                "has_validated_labels": True
            }
        )

        # Extract relevant metadata
        similar_receipts = []
        for match in results.matches:
            if match.score > 0.8:  # Only high-confidence matches
                similar_receipts.append({
                    'receipt_id': match.metadata.get('receipt_id'),
                    'merchant_name': match.metadata.get('merchant_name'),
                    'validated_labels': match.metadata.get('validated_labels', {}),
                    'total': match.metadata.get('total'),
                    'date': match.metadata.get('date'),
                    'score': match.score
                })

        return similar_receipts

    except Exception as e:
        logger.warning(f"Failed to retrieve similar receipts: {str(e)}")
        return []
```

### Step 2: Integrate Retrieval into Labeling Flow

```python
# Modify label_receipt method

def label_receipt(
    self,
    receipt: Receipt,
    receipt_words: List[ReceiptWord],
    receipt_lines: List[ReceiptLine],
    enable_validation: Optional[bool] = None,
    enable_places_api: bool = True,
    enable_retrieval: bool = True,  # New parameter
) -> LabelingResult:
    """Label a receipt using various processors.

    Args:
        ... existing args ...
        enable_retrieval: Whether to retrieve similar receipts for context
    """
    # ... existing code ...

    # Get Places API data if enabled
    places_data = None
    if enable_places_api:
        start_time = time.time()
        places_data = self._get_places_data(receipt_words)
        execution_times["places_api"] = time.time() - start_time

    # NEW: Get similar receipts if enabled
    similar_receipts = []
    if enable_retrieval:
        start_time = time.time()
        similar_receipts = self._get_similar_receipts(receipt, receipt_words)
        execution_times["retrieval"] = time.time() - start_time

        # Add to places_data for backward compatibility
        if places_data is None:
            places_data = {}
        places_data['similar_receipts'] = similar_receipts

        logger.info(f"Retrieved {len(similar_receipts)} similar receipts")

    # Continue with existing analysis...
```

### Step 3: Update GPT Prompts to Use Similar Receipts

```python
# In receipt_label/data/gpt.py

def gpt_request_structure_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    places_api_data: Optional[Dict],
    gpt_api_key: str,
) -> Tuple[Dict, str, str]:
    """Request structure analysis from GPT.

    Now includes similar receipts in context for better analysis.
    """
    # ... existing prompt construction ...

    # Add similar receipts to prompt if available
    if places_api_data and 'similar_receipts' in places_api_data:
        similar_receipts = places_api_data['similar_receipts']
        if similar_receipts:
            prompt += "\n\nSimilar receipts from this merchant:\n"
            for sr in similar_receipts[:3]:  # Top 3 most similar
                prompt += f"- Merchant: {sr['merchant_name']}, "
                prompt += f"Total: {sr['total']}, "
                prompt += f"Date: {sr['date']}, "
                prompt += f"Similarity: {sr['score']:.2f}\n"

            prompt += "\nUse these similar receipts as context for better analysis.\n"

    # ... rest of function ...
```

### Step 4: Add ClientManager Injection

```python
# Update ReceiptLabeler constructor

def __init__(
    self,
    places_api_key: Optional[str] = None,
    gpt_api_key: Optional[str] = None,
    dynamodb_table_name: Optional[str] = None,
    validation_level: str = "basic",
    validation_config: Optional[Dict[str, Any]] = None,
    client_manager: Optional[ClientManager] = None,  # NEW
):
    """Initialize the receipt labeler.

    Args:
        ... existing args ...
        client_manager: Optional ClientManager for accessing external services
    """
    # ... existing initialization ...

    # Store client manager for retrieval
    self.client_manager = client_manager

    # If client manager provided, use its clients
    if client_manager:
        self.gpt_api_key = client_manager.config.openai_api_key
        self.places_api_key = places_api_key  # Still allow override
```

## Testing Strategy

### Unit Tests

```python
# test_labeler_retrieval.py

def test_get_similar_receipts(mocker):
    """Test retrieval of similar receipts."""
    # Mock Pinecone response
    mock_pinecone = mocker.Mock()
    mock_pinecone.query.return_value = mocker.Mock(
        matches=[
            mocker.Mock(
                score=0.95,
                metadata={
                    'receipt_id': '123',
                    'merchant_name': 'Whole Foods',
                    'total': '42.50',
                    'validated_labels': {'TOTAL': '42.50', 'MERCHANT': 'Whole Foods'}
                }
            )
        ]
    )

    # Test retrieval
    labeler = ReceiptLabeler(client_manager=mock_client_manager)
    similar = labeler._get_similar_receipts(receipt, receipt_words)

    assert len(similar) == 1
    assert similar[0]['merchant_name'] == 'Whole Foods'
    assert similar[0]['score'] == 0.95
```

### Integration Tests

```python
def test_label_receipt_with_retrieval():
    """Test full labeling flow with retrieval enabled."""
    # Create labeler with mock client manager
    labeler = ReceiptLabeler(
        gpt_api_key="test-key",
        client_manager=mock_client_manager
    )

    # Label receipt with retrieval
    result = labeler.label_receipt(
        receipt=test_receipt,
        receipt_words=test_words,
        receipt_lines=test_lines,
        enable_retrieval=True
    )

    # Verify retrieval was called
    assert 'retrieval' in result.metadata['execution_times']
    assert result.metadata.get('similar_receipts_count', 0) > 0
```

## Metrics to Track

1. **Retrieval Performance**
   - Query latency (p50, p95, p99)
   - Number of similar receipts found
   - Similarity score distribution

2. **Labeling Accuracy Impact**
   - Validation success rate with/without retrieval
   - Label confidence scores
   - Reduction in human review needs

3. **System Performance**
   - Overall labeling latency
   - Pinecone query costs
   - Memory usage with cached embeddings

## Rollout Plan

1. **Day 1-2**: Implement retrieval method and tests
2. **Day 3**: Integrate into labeling flow with feature flag
3. **Day 4**: Deploy to dev environment with 10% of traffic
4. **Day 5**: Monitor metrics and tune similarity thresholds
5. **Day 6-7**: Gradual rollout to 50% then 100% of traffic

## Success Criteria

- Retrieval adds <500ms to labeling latency (p95)
- Validation success rate improves by >5%
- No increase in error rates
- Similar receipts found for >60% of queries

## Future Enhancements

- Cache embeddings for recently processed receipts
- Use merchant-specific vector spaces for better similarity
- Add feedback loop to update embeddings with corrections
- Implement batch retrieval for multiple receipts
