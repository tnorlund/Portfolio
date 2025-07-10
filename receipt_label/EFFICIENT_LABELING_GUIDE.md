# Efficient Receipt Word Labeling Guide

## Overview
<!-- Brief description of the efficient labeling approach and its benefits -->

## Architecture
### System Components
<!-- List of key components and their responsibilities -->

### Data Flow
<!-- High-level flow from receipt input to labeled output -->

## Prerequisites
### 1. Merchant Metadata
<!-- Requirements for ReceiptMetadata entity -->
<!-- How to handle missing metadata -->

### 2. Pinecone Embeddings
<!-- Requirement for embedded lines and words -->
<!-- Embedding schema and metadata structure -->

### 3. CORE_LABELS Definition
<!-- Reference to constants.py -->
<!-- List of 18 core labels -->

## Implementation Steps

### Step 1: Metadata Retrieval
<!-- Query ReceiptMetadata from DynamoDB -->
<!-- Extract merchant_name and category -->
<!-- Handle missing metadata scenarios -->

### Step 2: Embedding Verification
<!-- Check if lines/words are embedded -->
<!-- Embed if missing -->
<!-- Metadata requirements -->

### Step 3: Parallel Pattern Detection
#### 3.1 Currency Pattern Detection
<!-- Regex patterns for currency -->
<!-- Examples of currency formats -->

#### 3.2 DateTime Pattern Detection
<!-- Common date/time formats -->
<!-- Pattern matching approach -->

#### 3.3 Contact Pattern Detection
<!-- Phone, email, website patterns -->
<!-- Validation rules -->

#### 3.4 Quantity Pattern Detection
<!-- Quantity indicators (@ , x, Qty:) -->
<!-- Association with prices -->

#### 3.5 Merchant Pattern Query
**Purpose**: Retrieve proven labeling patterns from the same merchant in a single query

**Query Strategy**:
- Query Pinecone for words with `validated_labels` from the same `merchant_name`
- Use metadata-only query (dummy vector) for efficiency
- Retrieve up to 1000 validated words to build pattern dictionary

**Pattern Extraction**:
- Group results by word text (case-insensitive)
- Track label frequency for each word
- Build confidence scores based on occurrence count
- Return dictionary: `{word_text: {label: confidence}}`

**Key Benefits**:
- ONE query instead of N queries (99% reduction)
- Leverages historical labeling data
- Merchant-specific patterns (e.g., "Big Mac" at McDonald's)
- Self-improving system (patterns get better over time)

### Step 4: Currency Classification Rules
#### Position-Based Rules
<!-- Bottom 20% = likely totals -->
<!-- Middle section = likely line items -->

#### Keyword-Based Rules
<!-- Mapping keywords to labels -->
<!-- Priority of rules -->

#### Context-Based Rules
<!-- Quantity patterns nearby -->
<!-- Line structure analysis -->

### Step 5: Apply Merchant Patterns
<!-- Using patterns from Pinecone query -->
<!-- Confidence scoring -->
<!-- Pattern precedence -->

### Step 6: Batch GPT Labeling
#### Grouping Strategy
<!-- Group by line for context -->
<!-- Batch size optimization -->

#### Prompt Engineering
<!-- Include merchant context -->
<!-- Provide labeled examples -->
<!-- Constrain to CORE_LABELS -->

#### Response Processing
<!-- Parse structured output -->
<!-- Validate against CORE_LABELS -->

### Step 7: Storage and Learning
#### Store Labels
<!-- Create ReceiptWordLabel entities -->
<!-- Batch write to DynamoDB -->

#### Update Pattern Cache
<!-- Extract successful patterns -->
<!-- Update Pinecone metadata -->
<!-- Confidence tracking -->

## Code Examples

### Pattern Detection Functions
```python
# Currency detection example
```

### Merchant Pattern Query
```python
# Pinecone query implementation
```

### Smart Classification
```python
# Classification rules implementation
```

### Batch GPT Labeling
```python
# GPT batch call example
```

## Performance Metrics
### Cost Analysis
<!-- Comparison table: old vs new approach -->
<!-- API call reduction -->
<!-- Processing time improvement -->

### Accuracy Metrics
<!-- Pattern matching accuracy -->
<!-- GPT labeling accuracy -->
<!-- Overall system accuracy -->

## Best Practices
### Error Handling
<!-- Graceful degradation -->
<!-- Fallback strategies -->

### Monitoring
<!-- Key metrics to track -->
<!-- Alert thresholds -->

### Optimization Tips
<!-- Caching strategies -->
<!-- Batch size tuning -->
<!-- Pattern confidence thresholds -->

## Migration Guide
### From Per-Word to Efficient Labeling
<!-- Step-by-step migration -->
<!-- Backward compatibility -->
<!-- Testing approach -->

## Troubleshooting
### Common Issues
<!-- Missing merchant metadata -->
<!-- Low pattern confidence -->
<!-- GPT rate limits -->

### Debugging Tips
<!-- Logging recommendations -->
<!-- Test data sets -->
<!-- Validation tools -->

## Future Enhancements
### Planned Improvements
<!-- Multi-language support -->
<!-- Advanced pattern learning -->
<!-- Real-time pattern updates -->

## Appendices
### A. CORE_LABELS Reference
<!-- Complete list with descriptions -->

### B. Pattern Examples by Merchant Type
<!-- Restaurant patterns -->
<!-- Retail patterns -->
<!-- Service patterns -->

### C. API Reference
<!-- Function signatures -->
<!-- Parameter descriptions -->
<!-- Return values -->
