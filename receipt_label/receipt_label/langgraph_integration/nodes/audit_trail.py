"""Audit trail node for comprehensive workflow tracking.

This node captures all decisions, costs, and outcomes for learning
and debugging. Implements the audit trail design from PHASE_3_AUDIT_TRAIL_DESIGN.md.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import defaultdict

from ..state import ReceiptProcessingState

logger = logging.getLogger(__name__)


async def audit_trail_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Write comprehensive audit trail to DynamoDB.
    
    This node runs at the end to capture:
    1. Workflow execution summary
    2. Node-level decisions
    3. Pattern effectiveness
    4. GPT usage details
    5. Validation failures
    
    Context Engineering:
    - COMPRESS: Summarize state for storage
    - WRITE: Audit records to DynamoDB
    - ISOLATE: Only auditing, no business logic
    """
    logger.info(f"Writing audit trail for receipt {state['receipt_id']}")
    
    # Prepare audit records
    audit_records = []
    current_date = datetime.now().strftime('%Y-%m-%d')
    current_time = datetime.now().isoformat()
    
    # Calculate workflow duration
    start_time = None
    end_time = None
    if state.get("decisions"):
        start_time = state["decisions"][0].get("timestamp")
        end_time = state["decisions"][-1].get("timestamp")
    
    # 1. Workflow Execution Record
    workflow_record = create_workflow_record(state, current_date, current_time)
    audit_records.append(workflow_record)
    
    # 2. Node-Level Records
    node_records = create_node_records(state, current_date)
    audit_records.extend(node_records)
    
    # 3. Pattern Effectiveness Records
    if state.get("labels"):
        pattern_records = create_pattern_effectiveness_records(state, current_date)
        audit_records.extend(pattern_records)
    
    # 4. GPT Usage Records
    if state.get("gpt_responses"):
        gpt_records = create_gpt_usage_records(state, current_date)
        audit_records.extend(gpt_records)
    
    # 5. Validation Failure Records
    if state.get("validation_results"):
        validation_records = create_validation_records(state, current_date)
        audit_records.extend(validation_records)
    
    # Store audit records in state for persistence node
    state["audit_records"] = audit_records
    
    # Track audit metrics
    state["decisions"].append({
        "node": "audit_trail",
        "action": "created_audit_records",
        "record_count": len(audit_records),
        "record_types": count_record_types(audit_records),
        "timestamp": current_time,
    })
    
    logger.info(f"Created {len(audit_records)} audit records")
    
    return state


def create_workflow_record(
    state: ReceiptProcessingState, 
    current_date: str, 
    current_time: str
) -> Dict[str, Any]:
    """Create high-level workflow execution record."""
    
    # Extract workflow path
    workflow_path = [d["node"] for d in state.get("decisions", [])]
    
    # Determine final status
    has_errors = bool(state.get("errors"))
    validation_passed = all(
        v.get("passed", False) 
        for v in state.get("validation_results", {}).values()
    )
    final_status = "failed" if has_errors else "needs_review" if state.get("needs_review") else "success"
    
    return {
        "pk": f"AUDIT#{current_date}",
        "sk": f"WORKFLOW#{state['receipt_id']}#{current_time}",
        "gsi1pk": f"RECEIPT#{state['receipt_id']}",
        "gsi2pk": f"MERCHANT#{state.get('merchant_name', 'unknown')}",
        
        # Core identifiers
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"],
        "merchant_name": state.get("merchant_name"),
        
        # Execution summary
        "workflow_path": workflow_path,
        "gpt_used": state.get("needs_gpt", False),
        "decision_outcome": state.get("decision_outcome"),
        "final_status": final_status,
        "needs_review": state.get("needs_review", False),
        
        # Cost tracking
        "costs": {
            "gpt_usd": state["metrics"].get("gpt_cost_usd", 0),
            "pinecone_queries": state["metrics"].get("pinecone_queries", 0),
            "dynamodb_reads": len([d for d in state.get("decisions", []) if "loaded" in d.get("action", "")]),
            "total_usd": state["metrics"].get("total_cost_usd", 0)
        },
        
        # Coverage metrics
        "coverage": {
            "total_words": state.get("total_words", 0),
            "labeled_words": state.get("labeled_words", 0),
            "pattern_labeled": len([l for l in state.get("labels", {}).values() if l.get("source") == "pattern"]),
            "gpt_labeled": len([l for l in state.get("labels", {}).values() if l.get("source") == "gpt"]),
            "coverage_percentage": state.get("coverage_percentage", 0)
        },
        
        # Performance
        "duration_ms": state["metrics"].get("total_processing_ms", 0),
        
        # TTL for cost management (30 days)
        "ttl": int((datetime.now() + timedelta(days=30)).timestamp())
    }


def create_node_records(
    state: ReceiptProcessingState,
    current_date: str
) -> List[Dict[str, Any]]:
    """Create detailed records for each node execution."""
    
    records = []
    
    for i, decision in enumerate(state.get("decisions", [])):
        node_name = decision.get("node", "unknown")
        
        # Extract relevant state changes for this node
        state_changes = {}
        if node_name == "load_merchant" and "merchant_patterns" in state:
            state_changes["merchant_patterns"] = len(state.get("merchant_patterns", []))
        elif node_name == "pattern_labeling" and "labels" in state:
            state_changes["labels_created"] = decision.get("labels_applied", 0)
        elif node_name == "decision_engine":
            state_changes["decision"] = state.get("decision_outcome")
            state_changes["needs_gpt"] = state.get("needs_gpt")
        
        record = {
            "pk": f"AUDIT#{current_date}",
            "sk": f"NODE#{state['receipt_id']}#{i:03d}#{node_name}",
            "gsi1pk": f"NODE_TYPE#{node_name}",
            
            "node_name": node_name,
            "timestamp": decision.get("timestamp"),
            "sequence": i,
            
            # Node decisions
            "decisions": {
                k: v for k, v in decision.items() 
                if k not in ["node", "timestamp"]
            },
            
            # State changes
            "state_changes": state_changes,
            
            # Status
            "status": "success",  # Could be enhanced with error tracking
            
            "ttl": int((datetime.now() + timedelta(days=30)).timestamp())
        }
        
        records.append(record)
    
    return records


def create_pattern_effectiveness_records(
    state: ReceiptProcessingState,
    current_date: str
) -> List[Dict[str, Any]]:
    """Track which patterns worked for labeling."""
    
    records = []
    merchant_name = state.get("merchant_name", "unknown")
    
    # Group labels by pattern source
    pattern_labels = defaultdict(list)
    
    for word_id, label_info in state.get("labels", {}).items():
        if label_info.get("source") == "pattern":
            pattern_type = label_info.get("label")
            pattern_labels[pattern_type].append(label_info)
    
    # Create effectiveness record for each pattern type
    for pattern_type, labels in pattern_labels.items():
        record = {
            "pk": f"PATTERN_STATS#{merchant_name}",
            "sk": f"PATTERN#{pattern_type}#{current_date[:7]}",  # Monthly
            
            "pattern_type": pattern_type,
            "month": current_date[:7],
            "usage_count": len(labels),
            "avg_confidence": sum(l.get("confidence", 0) for l in labels) / len(labels),
            
            # This would be updated incrementally in production
            "success_count": len(labels),  # Assume success until proven otherwise
            "success_rate": 1.0,
            
            # No TTL - valuable learning data
        }
        records.append(record)
    
    return records


def create_gpt_usage_records(
    state: ReceiptProcessingState,
    current_date: str
) -> List[Dict[str, Any]]:
    """Track every GPT call for cost analysis."""
    
    records = []
    
    for gpt_resp in state.get("gpt_responses", []):
        # Determine what was found
        found_labels = {}
        if state.get("gpt_spatial_context"):
            # Map back to what GPT found
            for context in state["gpt_spatial_context"]:
                target = context.get("target_field")
                # Check if this field was labeled after GPT
                for word_id, label in state.get("labels", {}).items():
                    if label.get("source") == "gpt" and label.get("label") == target:
                        found_labels[target] = {
                            "word_id": word_id,
                            "confidence": label.get("confidence", 0)
                        }
        
        record = {
            "pk": f"GPT_USAGE#{current_date}",
            "sk": f"CALL#{gpt_resp.get('timestamp', current_date)}#{state['receipt_id']}",
            "gsi1pk": f"MERCHANT#{state.get('merchant_name', 'unknown')}",
            
            # What triggered GPT
            "trigger": {
                "missing_essentials": state.get("missing_essentials", []),
                "coverage_before": state.get("coverage_percentage", 0),
                "decision": state.get("decision_outcome"),
                "decision_reasoning": state.get("decision_reasoning")
            },
            
            # Prompt details
            "prompt_summary": {
                "context_sections": len(state.get("gpt_spatial_context", [])),
                "target_fields": [ctx.get("target_field") for ctx in state.get("gpt_spatial_context", [])],
                "context_type": state.get("gpt_context_type")
            },
            
            # Results
            "response": {
                "found_labels": found_labels,
                "tokens_used": gpt_resp.get("tokens", 0),
                "cost_usd": gpt_resp.get("cost", 0)
            },
            
            # Effectiveness
            "validation_passed": all(
                v.get("passed", False) 
                for v in state.get("validation_results", {}).values()
            ),
            
            # 90-day retention for learning
            "ttl": int((datetime.now() + timedelta(days=90)).timestamp())
        }
        records.append(record)
    
    return records


def create_validation_records(
    state: ReceiptProcessingState,
    current_date: str
) -> List[Dict[str, Any]]:
    """Track validation failures for learning."""
    
    records = []
    
    for val_type, val_result in state.get("validation_results", {}).items():
        if not val_result.get("passed", True):
            record = {
                "pk": f"VALIDATION_FAIL#{current_date[:7]}",  # Monthly
                "sk": f"FAIL#{datetime.now().isoformat()}#{state['receipt_id']}",
                "gsi1pk": f"FAIL_TYPE#{val_type}",
                
                "validation_type": val_type,
                "receipt_id": state["receipt_id"],
                "merchant_name": state.get("merchant_name", "unknown"),
                
                # Failure details
                "failure": {
                    "errors": val_result.get("errors", []),
                    "warnings": val_result.get("warnings", [])
                },
                
                # Context for learning
                "receipt_context": {
                    "total_words": state.get("total_words", 0),
                    "coverage": state.get("coverage_percentage", 0),
                    "had_gpt": state.get("needs_gpt", False),
                    "pattern_types_found": list(set(
                        l.get("label") for l in state.get("labels", {}).values()
                    ))
                },
                
                # No TTL - valuable for learning
            }
            records.append(record)
    
    return records


def count_record_types(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count records by type for metrics."""
    type_counts = defaultdict(int)
    
    for record in records:
        sk = record.get("sk", "")
        if sk.startswith("WORKFLOW#"):
            type_counts["workflow"] += 1
        elif sk.startswith("NODE#"):
            type_counts["node"] += 1
        elif sk.startswith("PATTERN#"):
            type_counts["pattern"] += 1
        elif sk.startswith("CALL#"):
            type_counts["gpt"] += 1
        elif sk.startswith("FAIL#"):
            type_counts["validation"] += 1
    
    return dict(type_counts)


# Utility functions for querying audit trail

async def get_receipt_audit_trail(dynamo_client, receipt_id: str) -> List[Dict]:
    """Get complete audit trail for a receipt."""
    response = await dynamo_client.query(
        IndexName="gsi1",
        KeyConditionExpression="gsi1pk = :pk",
        ExpressionAttributeValues={
            ":pk": f"RECEIPT#{receipt_id}"
        }
    )
    return sorted(response.get('Items', []), key=lambda x: x.get('sk', ''))


async def get_daily_costs(dynamo_client, date: str) -> Dict[str, float]:
    """Get costs broken down by merchant for a specific date."""
    response = await dynamo_client.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
        ExpressionAttributeValues={
            ":pk": f"AUDIT#{date}",
            ":sk_prefix": "WORKFLOW#"
        }
    )
    
    costs_by_merchant = defaultdict(float)
    for item in response.get('Items', []):
        merchant = item.get('merchant_name', 'unknown')
        total_cost = item.get('costs', {}).get('total_usd', 0)
        costs_by_merchant[merchant] += total_cost
    
    return dict(costs_by_merchant)


async def get_pattern_effectiveness(
    dynamo_client, 
    merchant_name: str
) -> List[Dict]:
    """Get pattern effectiveness stats for a merchant."""
    response = await dynamo_client.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={
            ":pk": f"PATTERN_STATS#{merchant_name}"
        },
        ScanIndexForward=False  # Sort by success rate descending
    )
    
    return [
        item for item in response.get('Items', [])
        if item.get('success_rate', 0) > 0.9  # High-performing patterns
    ]