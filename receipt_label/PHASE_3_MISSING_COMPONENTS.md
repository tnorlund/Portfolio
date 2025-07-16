# Phase 3: Missing Components for Complete Agentic Workflow

## Overview

While we have solid pattern detection, labeling, and validation workflows, several critical components are needed for a production-ready agentic system.

## 1. Feedback Loop & Continuous Learning

### The Missing Piece: Learning from Corrections

```python
class FeedbackLoop:
    """Agent learns from validation failures and human corrections"""
    
    def __init__(self, dynamo_client, pinecone_store):
        self.dynamo = dynamo_client
        self.pinecone = pinecone_store
        
    async def record_validation_failure(
        self,
        receipt_id: str,
        word_id: int,
        predicted_label: str,
        correct_label: str,
        failure_reason: str
    ):
        """Track when agent gets labels wrong"""
        
        # Store correction in DynamoDB
        correction = LabelCorrection(
            pk=f"CORRECTION#{receipt_id}#{word_id}",
            sk=f"TIMESTAMP#{datetime.utcnow().isoformat()}",
            predicted=predicted_label,
            correct=correct_label,
            reason=failure_reason,
            merchant=await self.get_merchant_name(receipt_id),
            pattern_used=await self.get_pattern_source(receipt_id, word_id)
        )
        
        await self.dynamo.put_item(correction)
        
        # Update pattern confidence
        await self.adjust_pattern_confidence(
            pattern_id=correction.pattern_used,
            success=False
        )
    
    async def learn_from_corrections(self, merchant: str = None):
        """Analyze corrections to improve future predictions"""
        
        # Query corrections
        corrections = await self.dynamo.query_corrections(
            merchant=merchant,
            limit=1000
        )
        
        # Identify patterns in failures
        failure_patterns = self.analyze_failure_patterns(corrections)
        
        # Generate new rules
        new_rules = []
        for pattern in failure_patterns:
            if pattern.frequency > 10 and pattern.confidence > 0.8:
                new_rules.append({
                    "condition": pattern.condition,
                    "action": pattern.suggested_action,
                    "confidence": pattern.confidence
                })
        
        # Store learned rules
        await self.dynamo.put_learned_rules(merchant, new_rules)
        
        return new_rules
```

### Integration with Workflow

```python
# Add to LangGraph workflow
async def apply_learned_rules_node(state: ReceiptProcessingState):
    """Apply previously learned rules before generic patterns"""
    
    # Get merchant-specific learned rules
    merchant = state.get("merchant_name")
    learned_rules = await feedback_loop.get_learned_rules(merchant)
    
    # Apply rules with higher priority than generic patterns
    for rule in learned_rules:
        if rule.matches(state["receipt_words"]):
            state["learned_labels"].update(rule.apply())
    
    return state
```

## 2. Human-in-the-Loop (HITL) Integration

### The Missing Piece: Escalation for Low Confidence

```python
class HumanInTheLoopManager:
    """Manage cases that need human review"""
    
    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        self.pending_reviews = asyncio.Queue()
        
    async def should_escalate(self, state: ReceiptProcessingState) -> bool:
        """Determine if human review is needed"""
        
        # Check confidence scores
        avg_confidence = np.mean([
            label.get("confidence", 0) 
            for label in state["all_labels"].values()
        ])
        
        # Check validation failures
        validation_failures = sum(
            1 for result in state["validation_results"].values() 
            if not result
        )
        
        # Escalation criteria
        if avg_confidence < self.confidence_threshold:
            return True
        if validation_failures > 2:
            return True
        if state.get("retry_count", 0) > 2:
            return True
            
        return False
    
    async def create_review_task(self, state: ReceiptProcessingState):
        """Create human review task with context"""
        
        review_task = HumanReviewTask(
            receipt_id=state["receipt_id"],
            priority=self.calculate_priority(state),
            context={
                "low_confidence_labels": self.get_low_confidence_labels(state),
                "validation_failures": state["validation_results"],
                "agent_notes": state.get("agent_reasoning", [])
            },
            suggested_labels=state["all_labels"],
            deadline=datetime.utcnow() + timedelta(hours=24)
        )
        
        # Store in review queue
        await self.dynamo.put_review_task(review_task)
        
        # Notify human reviewers
        await self.send_review_notification(review_task)
        
        return review_task
```

## 3. Agent Memory & Context Sharing

### The Missing Piece: Cross-Receipt Intelligence

```python
class AgentMemory:
    """Short and long-term memory for the agent"""
    
    def __init__(self):
        self.short_term = {}  # In-memory cache
        self.long_term = None  # DynamoDB/Pinecone
        
    async def remember_merchant_context(
        self,
        merchant: str,
        context: Dict[str, Any]
    ):
        """Store merchant-specific patterns for future use"""
        
        # Short-term (current session)
        self.short_term[merchant] = {
            "common_products": context.get("products", []),
            "typical_layout": context.get("layout"),
            "price_patterns": context.get("price_patterns"),
            "last_seen": datetime.utcnow()
        }
        
        # Long-term (persistent)
        await self.dynamo.update_merchant_patterns(
            merchant,
            context,
            source="agent_learning"
        )
    
    async def get_similar_receipts(
        self,
        receipt_embedding: List[float],
        merchant: str = None
    ) -> List[SimilarReceipt]:
        """Find similar successfully processed receipts"""
        
        # Query Pinecone for similar receipts
        results = await self.pinecone.query(
            vector=receipt_embedding,
            filter={"merchant": merchant} if merchant else None,
            top_k=5,
            include_metadata=True
        )
        
        # Return receipts with high validation scores
        return [
            SimilarReceipt(
                receipt_id=r.id,
                similarity=r.score,
                labels=r.metadata.get("validated_labels"),
                patterns=r.metadata.get("successful_patterns")
            )
            for r in results.matches
            if r.metadata.get("validation_score", 0) > 0.9
        ]
```

## 4. Confidence Score Propagation

### The Missing Piece: Cascading Confidence

```python
class ConfidencePropagation:
    """Track and propagate confidence through the workflow"""
    
    def __init__(self):
        self.confidence_graph = nx.DiGraph()
        
    def add_label_confidence(
        self,
        word_id: int,
        label: str,
        confidence: float,
        source: str,
        dependencies: List[int] = None
    ):
        """Track confidence with dependencies"""
        
        self.confidence_graph.add_node(
            word_id,
            label=label,
            confidence=confidence,
            source=source
        )
        
        # Add edges for dependencies
        if dependencies:
            for dep_id in dependencies:
                self.confidence_graph.add_edge(
                    dep_id,
                    word_id,
                    weight=0.8  # Dependency strength
                )
    
    def propagate_confidence(self) -> Dict[int, float]:
        """Propagate confidence through the graph"""
        
        # Use PageRank-like algorithm
        confidences = nx.pagerank(
            self.confidence_graph,
            weight='weight',
            personalization={
                node: data['confidence']
                for node, data in self.confidence_graph.nodes(data=True)
            }
        )
        
        return confidences
    
    def get_weakest_links(self, threshold: float = 0.5) -> List[int]:
        """Find labels that need improvement"""
        
        final_confidences = self.propagate_confidence()
        return [
            node for node, conf in final_confidences.items()
            if conf < threshold
        ]
```

## 5. Real-time Performance Monitoring

### The Missing Piece: Agent Performance Metrics

```python
class AgentPerformanceMonitor:
    """Track agent performance in real-time"""
    
    def __init__(self):
        self.metrics = {
            "pattern_hit_rate": deque(maxlen=1000),
            "gpt_call_rate": deque(maxlen=1000),
            "validation_success_rate": deque(maxlen=1000),
            "processing_time": deque(maxlen=1000),
            "cost_per_receipt": deque(maxlen=1000)
        }
        
    async def record_receipt_processing(self, state: ReceiptProcessingState):
        """Record metrics for monitoring"""
        
        metrics = {
            "receipt_id": state["receipt_id"],
            "timestamp": datetime.utcnow(),
            "pattern_hit_rate": len(state["pattern_labels"]) / len(state["receipt_words"]),
            "gpt_called": state.get("gpt_called", False),
            "validation_passed": all(state["validation_results"].values()),
            "processing_time_ms": state["total_time_ms"],
            "cost": state.get("total_cost", 0),
            "merchant": state.get("merchant_name"),
            "confidence_avg": np.mean([
                l["confidence"] for l in state["all_labels"].values()
            ])
        }
        
        # Update rolling metrics
        self.update_metrics(metrics)
        
        # Check for anomalies
        if self.detect_performance_degradation():
            await self.alert_performance_issue(metrics)
    
    def get_realtime_dashboard(self) -> Dict:
        """Get current performance snapshot"""
        
        return {
            "last_hour": {
                "receipts_processed": len(self.metrics["pattern_hit_rate"]),
                "avg_pattern_coverage": np.mean(self.metrics["pattern_hit_rate"]),
                "gpt_usage_rate": sum(self.metrics["gpt_call_rate"]) / len(self.metrics["gpt_call_rate"]),
                "validation_success": np.mean(self.metrics["validation_success_rate"]),
                "avg_cost": np.mean(self.metrics["cost_per_receipt"]),
                "p95_processing_time": np.percentile(self.metrics["processing_time"], 95)
            },
            "alerts": self.get_active_alerts()
        }
```

## 6. Fallback & Recovery Strategies

### The Missing Piece: Graceful Degradation

```python
class FallbackStrategy:
    """Handle failures gracefully"""
    
    async def execute_with_fallback(
        self,
        primary_fn,
        fallback_fn,
        state: ReceiptProcessingState
    ):
        """Try primary, fallback if needed"""
        
        try:
            return await primary_fn(state)
        except Exception as e:
            logger.warning(f"Primary failed: {e}, trying fallback")
            
            # Record failure
            await self.record_failure(
                "primary",
                str(e),
                state["receipt_id"]
            )
            
            try:
                # Modify state for fallback
                state["using_fallback"] = True
                state["fallback_reason"] = str(e)
                
                return await fallback_fn(state)
            except Exception as fallback_e:
                # Both failed - use emergency mode
                return await self.emergency_basic_labeling(state)
```

## 7. A/B Testing Framework

### The Missing Piece: Continuous Improvement

```python
class ABTestingFramework:
    """Test different strategies"""
    
    def __init__(self):
        self.active_tests = {}
        
    async def assign_variant(self, receipt_id: str, test_name: str) -> str:
        """Assign receipt to test variant"""
        
        test = self.active_tests.get(test_name)
        if not test:
            return "control"
        
        # Consistent assignment based on receipt_id
        hash_value = int(hashlib.md5(receipt_id.encode()).hexdigest(), 16)
        
        if hash_value % 100 < test.percentage:
            return "treatment"
        return "control"
    
    async def record_outcome(
        self,
        receipt_id: str,
        test_name: str,
        variant: str,
        metrics: Dict
    ):
        """Record test results"""
        
        await self.dynamo.put_test_result({
            "test_name": test_name,
            "variant": variant,
            "receipt_id": receipt_id,
            "metrics": metrics,
            "timestamp": datetime.utcnow()
        })
```

## Integration Points

All these components integrate with your LangGraph workflow:

```python
# Enhanced main workflow
def create_complete_agentic_workflow():
    workflow = StateGraph(ReceiptProcessingState)
    
    # Core labeling nodes
    workflow.add_node("pattern_detection", pattern_detection_node)
    workflow.add_node("apply_learned_rules", apply_learned_rules_node)  # NEW
    workflow.add_node("initial_labeling", initial_labeling_node)
    workflow.add_node("confidence_propagation", propagate_confidence_node)  # NEW
    workflow.add_node("validation", validation_node)
    
    # Recovery and learning nodes
    workflow.add_node("human_escalation", human_escalation_node)  # NEW
    workflow.add_node("record_feedback", record_feedback_node)  # NEW
    workflow.add_node("performance_monitoring", monitor_performance_node)  # NEW
    
    # A/B test routing
    workflow.add_conditional_edges(
        "pattern_detection",
        lambda x: ab_testing.assign_variant(x["receipt_id"], "labeling_strategy"),
        {
            "control": "initial_labeling",
            "treatment": "experimental_labeling"  # NEW
        }
    )
    
    return workflow.compile()
```

## Summary

The missing pieces for a complete agentic workflow:

1. **Learning System**: Learn from mistakes and improve
2. **Human-in-the-Loop**: Escalate low-confidence cases
3. **Agent Memory**: Remember patterns across receipts
4. **Confidence Tracking**: Understand uncertainty propagation
5. **Performance Monitoring**: Real-time health checks
6. **Fallback Strategies**: Graceful degradation
7. **A/B Testing**: Continuous improvement

These components transform your system from a static pipeline to a true learning agent that gets better over time.