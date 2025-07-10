# Epic #191: Smart GPT Integration - Planning Document

## Overview

This document outlines the planning and strategy for implementing the Smart GPT Integration system that sits at the heart of the efficient receipt labeling pipeline. This epic represents the decision logic that determines when and how to use GPT for optimal cost/accuracy balance.

## Context from Mermaid Diagram Flow

```
Pattern Detection Results → Smart GPT Decision Logic → Essential Labels Check → Noise Word Filtering → DynamoDB Storage
```

The Smart GPT system must make intelligent decisions about:
1. **When to call GPT** vs when to skip
2. **What prompt to use** based on context
3. **How to integrate** pattern detection results
4. **How to prioritize** essential labels

## Current System Analysis

### Existing GPT Prompt Infrastructure

**Strengths to Build On:**
- ✅ **Function Calling Pattern**: Structured OpenAI function calling with schemas
- ✅ **Merchant Context Integration**: Google Places API data already included
- ✅ **Cost Optimization**: Pattern analyzer replaces GPT for some tasks (60-70% → 80-85% accuracy)
- ✅ **Validation Framework**: Comprehensive validation status system with confidence thresholds
- ✅ **Batch Processing**: OpenAI Batch API integration for cost optimization

**Current Prompt Patterns:**
1. **Function-Based Prompting**: Uses structured schemas for validation
2. **Structured JSON Response**: Multiple specialized prompts for different tasks
3. **System + User Message**: Role definition + specific data/instructions

### Existing Decision Logic

**Current Smart Patterns:**
- **Confidence-based decisions**: Numerical thresholds (0.70, 0.80, 0.90)
- **Field-based matching**: 2-of-3 fields or 1-of-3 + confidence threshold
- **Pattern-based analysis**: Enhanced pattern analyzer already replaces some GPT calls
- **Essential labels system**: Uses CORE_LABELS dictionary (36 labels)

## Smart GPT Integration Challenges

### 1. **Prompt Engineering Complexity**

**Challenge**: Determining optimal prompt for Smart GPT decision logic
- Multiple context types: merchant, patterns, spatial, essential labels
- Balance between comprehensive context and token cost
- Dynamic prompt selection based on merchant/receipt type

**Current Analysis Gaps:**
- No A/B testing framework for prompt optimization
- No merchant-specific prompt customization
- No dynamic prompt selection based on pattern detection results

### 2. **Decision Logic Optimization**

**Challenge**: When to call GPT vs when to skip
- Essential labels missing → must call GPT
- Only noise words left → skip GPT
- Threshold for meaningful unlabeled words (current: 5 words)

**Questions to Resolve:**
- How do pattern detection results affect the threshold?
- Should merchant type influence the decision?
- How to handle edge cases (new merchant, poor OCR quality)?

### 3. **Context Integration**

**Challenge**: Integrating results from Epic #189 and #190
- Merchant pattern query results (99% Pinecone query reduction)
- Parallel pattern detection results (4 detectors, < 100ms)
- Spatial context and neighboring words
- Already labeled words for context

**Integration Points:**
- Pattern detection confidence scores
- Merchant-specific known patterns
- Essential labels status
- Noise word filtering results

## Proposed Smart GPT Strategy

### Phase 1: Prompt Strategy Framework

#### A. Context-Aware Prompt Selection

```python
class SmartGPTPrompter:
    def select_prompt_strategy(self, context: ProcessingContext) -> str:
        if context.essential_labels_missing:
            return "essential_focused_prompt"
        elif context.high_confidence_patterns > 0.8:
            return "pattern_enhanced_prompt"
        elif context.merchant_patterns_available:
            return "merchant_specialized_prompt"
        else:
            return "balanced_approach_prompt"
```

#### B. Dynamic Context Integration

```python
def build_smart_context(
    unlabeled_words: List[ReceiptWord],
    pattern_results: ParallelDetectionResult,  # From Epic #190
    merchant_patterns: MerchantPatterns,       # From Epic #189
    merchant_context: ReceiptMetadata,
    already_labeled: List[ReceiptWord]
) -> SmartGPTContext:

    return SmartGPTContext(
        merchant_intelligence=extract_merchant_context(merchant_context),
        pattern_detections=pattern_results.high_confidence_matches,
        essential_status=check_essential_labels(already_labeled),
        spatial_context=get_spatial_relationships(unlabeled_words),
        decision_threshold=calculate_dynamic_threshold(context)
    )
```

### Phase 2: A/B Testing Framework

#### Prompt Variants to Test

1. **Pattern-Enhanced Prompt**
   - Full context with all pattern detection results
   - Detailed merchant intelligence
   - Spatial positioning data

2. **Minimalist Smart Prompt**
   - Essential-focused with pattern summary
   - High-level merchant context only
   - Decision-focused language

3. **Merchant-Specialized Prompt**
   - Heavy merchant context weighting
   - Known patterns emphasis
   - Merchant-specific examples

4. **Confidence-Weighted Prompt**
   - Dynamic confidence thresholds
   - Pattern confidence integration
   - Uncertainty acknowledgment

#### Testing Methodology

```python
class PromptTester:
    def run_prompt_experiment(self, variant: str, test_receipts: List[Receipt]) -> TestResults:
        metrics = {
            "accuracy": measure_labeling_accuracy(),
            "cost": calculate_gpt_costs(),
            "speed": measure_processing_time(),
            "essential_coverage": check_essential_coverage(),
            "pattern_utilization": measure_pattern_usage()
        }
        return TestResults(variant, metrics)
```

### Phase 3: Decision Logic Optimization

#### Smart Decision Tree

```python
def should_use_gpt(context: SmartGPTContext) -> GPTDecision:
    # Essential labels check (highest priority)
    if context.essential_labels_missing:
        return GPTDecision.REQUIRED

    # Noise word filtering (Epic #188 integration)
    meaningful_words = [w for w in context.unlabeled_words if not w.is_noise]
    if len(meaningful_words) == 0:
        return GPTDecision.SKIP

    # Pattern confidence check (Epic #190 integration)
    high_confidence_patterns = context.pattern_results.high_confidence_count
    if high_confidence_patterns >= len(meaningful_words) * 0.8:
        return GPTDecision.SKIP

    # Merchant pattern check (Epic #189 integration)
    if context.merchant_patterns.coverage >= 0.9:
        return GPTDecision.SKIP

    # Threshold check (configurable)
    if len(meaningful_words) < context.threshold:
        return GPTDecision.SKIP

    return GPTDecision.BATCH_PROCESS
```

## Implementation Planning Questions

### 1. **Prompt Engineering Approach**

**Questions:**
- Should we start with existing prompt patterns and enhance them?
- How do we measure prompt effectiveness beyond accuracy?
- What's the right balance between context richness and token cost?

**Proposed Approach:**
- Build incremental improvements on existing function calling pattern
- A/B test with real receipt data from different merchant types
- Monitor cost vs accuracy trade-offs

### 2. **Integration with Pattern Detection**

**Questions:**
- How do we weight pattern detection confidence vs GPT confidence?
- Should patterns override GPT decisions or inform them?
- How do we handle conflicts between pattern detection and GPT results?

**Proposed Approach:**
- Pattern detection informs prompt context but doesn't override GPT
- Use pattern confidence as input to GPT decision threshold
- Implement conflict resolution with confidence scoring

### 3. **Merchant-Specific Optimization**

**Questions:**
- Should different merchant types use different prompt strategies?
- How do we handle new merchants with no historical pattern data?
- What's the learning curve for merchant-specific optimization?

**Proposed Approach:**
- Start with general prompt, evolve to merchant-specific
- Use merchant category (restaurant, grocery, retail) for initial specialization
- Build merchant pattern database over time

## Success Metrics & KPIs

### Technical Metrics
- **Accuracy**: > 90% label correctness (vs current baseline)
- **Cost Reduction**: 50-80% reduction in GPT calls through smart skipping
- **Speed**: < 2 seconds for Smart GPT decision + processing
- **Essential Coverage**: 99% essential label detection rate

### Business Metrics
- **Pattern Utilization**: 80%+ of pattern detection results effectively used
- **Merchant Learning**: Improved accuracy over time per merchant
- **Cost Efficiency**: $ per correctly labeled receipt
- **Processing Throughput**: Receipts processed per hour

## Risk Assessment

### High Risk Areas
1. **Prompt Engineering Complexity**: Too many variables, hard to optimize
2. **Context Integration**: Overwhelming GPT with too much context
3. **Decision Logic**: Over-optimization leading to edge case failures

### Mitigation Strategies
1. **Incremental Development**: Start simple, add complexity gradually
2. **Comprehensive Testing**: A/B testing with real merchant data
3. **Fallback Mechanisms**: Always have simple prompts as backup

## Next Steps for Planning

### Immediate Planning Tasks
1. **Detailed Prompt Design**: Create specific prompt templates for each variant
2. **Testing Framework Design**: Plan A/B testing infrastructure
3. **Integration Specification**: Define exact interfaces with Epic #189/#190
4. **Success Metrics Definition**: Set specific, measurable targets

### Dependencies to Clarify
1. **Epic #189 Integration**: How merchant patterns are provided to Smart GPT
2. **Epic #190 Integration**: Pattern detection result format and confidence scoring
3. **Epic #188 Integration**: Noise word filtering impact on decision logic

### Planning Decisions Needed
1. **Scope Boundary**: What belongs in Epic #191 vs #192 (Integration)
2. **Testing Approach**: Real merchant data vs synthetic testing
3. **Rollout Strategy**: Gradual merchant-by-merchant vs full deployment

---

**Status**: Planning Phase - Requires detailed design decisions before implementation
**Next Review**: After Epic #189 and #190 interfaces are defined
