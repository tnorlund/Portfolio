# Smart Decision Engine

The Smart Decision Engine determines when pattern detection alone is sufficient for receipt labeling versus when GPT assistance is needed, targeting an 84% reduction in AI API costs.

## Overview

This engine implements a pattern-first strategy that:
- Analyzes pattern detection results for coverage and confidence
- Checks for essential labels (MERCHANT_NAME, DATE, GRAND_TOTAL)
- Leverages Pinecone for merchant-specific intelligence
- Makes intelligent decisions to minimize GPT usage while maintaining accuracy

## Architecture

### Phase 1: Baseline Implementation
- Core decision logic with static thresholds
- Essential labels enforcement
- Three-tier outcome system (SKIP/BATCH/REQUIRED)
- Feature flag support for safe rollout

### Phase 2: Adaptive Enhancement
- Dynamic thresholds based on merchant reliability
- Pinecone integration for historical validation
- Spatial context utilization
- Performance optimization (<10ms decision time)

### Phase 3: Production Optimization
- A/B testing infrastructure
- Continuous learning and feedback loops
- Advanced monitoring and analytics
- Production-scale deployment

## Usage

```python
from receipt_label.decision_engine import DecisionEngine, DecisionEngineConfig

# Initialize with configuration
config = DecisionEngineConfig(
    min_meaningful_words=5,
    min_coverage_percentage=90.0,
    require_essential_labels=True
)

engine = DecisionEngine(config, pinecone_client)

# Make decision after pattern detection
decision = await engine.decide(pattern_results, merchant_name)

if decision.action == DecisionOutcome.SKIP:
    # Use pattern-only results
    return finalize_pattern_labels(pattern_results)
elif decision.action == DecisionOutcome.BATCH:
    # Queue for batch GPT processing
    enqueue_for_batch_processing(receipt_id, unlabeled_words)
    return pattern_labels
else:  # REQUIRED
    # Immediate GPT processing needed
    gpt_response = await call_gpt_immediate(unlabeled_words)
    return merge_labels(pattern_results, gpt_response)
```

## Success Metrics

- **84% GPT skip rate**: Process ~5 out of 6 receipts with patterns only
- **>95% labeling accuracy**: Maintain quality while reducing costs
- **100% essential field coverage**: Never miss critical labels
- **<10ms decision latency**: Negligible performance overhead

## Development Phases

### Phase 1: Baseline (Weeks 1-2)
- [ ] Core DecisionEngine class
- [ ] Static threshold configuration
- [ ] Essential labels validation
- [ ] Feature flag integration
- [ ] Unit and integration tests

### Phase 2: Enhancement (Weeks 3-4)
- [ ] Merchant reliability scoring
- [ ] Pinecone historical data integration
- [ ] Dynamic threshold adjustment
- [ ] Spatial context validation
- [ ] Performance optimization

### Phase 3: Production (Weeks 5-6)
- [ ] A/B testing framework
- [ ] Monitoring and alerting
- [ ] Feedback loop implementation
- [ ] Production deployment
- [ ] Continuous improvement

## Integration Points

- **Pattern Detection**: Consumes results from enhanced orchestrator
- **Pinecone**: Queries merchant-specific historical data
- **Monitoring**: Integrates with existing AIUsageTracker
- **Batch Processing**: Leverages existing GPT batch infrastructure
- **Feature Flags**: Uses existing configuration management

## Testing Strategy

- **Unit Tests**: Decision logic with synthetic inputs
- **Integration Tests**: 47 production receipt validation dataset
- **Performance Tests**: <10ms latency requirement
- **A/B Tests**: Production rollout validation

See [Issue #191](https://github.com/tnorlund/Portfolio/issues/191) for detailed implementation strategy.