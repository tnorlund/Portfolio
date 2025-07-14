# Prompt: Design a Smart Decision Engine for Receipt Labeling

## Context

You are tasked with designing a Smart Decision Engine that determines when pattern detection alone is sufficient for labeling receipt words versus when GPT/LLM assistance is needed. The goal is to achieve an 84% reduction in AI API costs while maintaining high labeling quality.

Please read the accompanying context document (`decision-engine-design-context.md`) which provides:
- Current pattern detection capabilities
- Core labels that need to be assigned
- Cost-benefit analysis
- Integration requirements
- Examples and edge cases

## Your Task

Design a comprehensive Decision Engine that includes:

### 1. Architecture Design
- Class structure and interfaces
- Data flow between components
- Integration with existing pattern detection system
- Error handling and fallback mechanisms

### 2. Decision Algorithm
- Detailed logic for when to skip vs. use GPT
- Confidence scoring methodology
- Handling of edge cases and special scenarios
- Adaptive thresholds based on merchant or receipt type

### 3. Implementation Details
```python
class SmartDecisionEngine:
    """Your design here"""
    
    def evaluate(self, receipt_words, pattern_results):
        """Core decision logic"""
        pass
    
    def prepare_gpt_context(self, unlabeled_words, pattern_labels):
        """If GPT needed, optimize the prompt"""
        pass
```

### 4. Optimization Strategies
- How to minimize GPT token usage when it IS needed
- Caching strategies for common patterns
- Learning from GPT corrections to improve patterns

### 5. Monitoring and Metrics
- What metrics to track
- How to measure success
- A/B testing framework for decision strategies

## Key Constraints

1. **Performance**: Decision must be made in <10ms
2. **Accuracy**: Must maintain >95% labeling accuracy
3. **Cost**: Achieve 84% reduction in GPT usage
4. **Flexibility**: Thresholds must be configurable
5. **Reliability**: Graceful fallback if pattern detection fails

## Deliverables

1. **Detailed Design Document** including:
   - System architecture diagram
   - Decision flow chart
   - Class and method specifications
   - Configuration schema

2. **Implementation Plan** with:
   - Development phases
   - Testing strategy
   - Rollout plan
   - Risk mitigation

3. **Measurement Framework**:
   - Success metrics
   - Monitoring approach
   - Feedback loops for improvement

## Additional Considerations

1. **Future Extensibility**: How can the engine adapt as new pattern detectors are added?

2. **Multi-language Support**: How would the engine handle receipts in different languages?

3. **Real-time Learning**: Can the engine improve its decisions based on GPT corrections?

4. **Cost Optimization**: Beyond the yes/no decision, how can we minimize costs when GPT IS used?

5. **Quality Assurance**: How do we ensure labeling quality without manual review?

## Example Scenarios to Address

Please design how your engine would handle:

1. A pristine Walmart receipt with clear patterns matching 95% of words
2. A crumpled restaurant receipt with handwritten tips and poor OCR
3. A receipt in Spanish from a Mexican grocery store
4. A minimal gas station receipt with only 10 words total
5. A complex invoice-style receipt with multiple tax rates and discounts

## Success Criteria

Your design will be evaluated on:
- Feasibility of achieving 84% cost reduction
- Robustness of decision logic
- Quality maintenance strategies
- Implementation complexity
- Monitoring and improvement capabilities

Please provide a comprehensive design that the development team can implement to achieve these goals.