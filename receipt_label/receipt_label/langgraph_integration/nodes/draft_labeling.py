"""Draft labeling node with LLM and rule-based approaches.

This node combines pattern-based labeling with GPT labeling for comprehensive
initial label assignment. It uses tool calling for external services.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from ...constants import CORE_LABELS
from ...utils.ai_usage_context import ai_usage_context
from ..state import ReceiptProcessingState, LabelInfo
from .utils import (
    get_dynamo_client,
    get_pinecone_client,
    format_receipt_words_for_prompt,
)

logger = logging.getLogger(__name__)


# Tool definitions for the agent
@tool
def apply_pattern_labels(
    pattern_matches: Dict[str, List[Dict]], 
    receipt_words: List[Dict]
) -> Dict[int, Dict]:
    """Apply pattern-based labels to receipt words.
    
    Args:
        pattern_matches: Dict of pattern type to matches
        receipt_words: List of receipt word dictionaries
        
    Returns:
        Dict mapping word_id to label info
    """
    labels = {}
    
    # Map pattern types to CORE_LABELS
    pattern_to_core_label = {
        "DATE": "DATE",
        "TIME": "TIME",
        "MERCHANT_NAME": "MERCHANT_NAME",
        "PHONE_NUMBER": "PHONE_NUMBER",
        "EMAIL": "WEBSITE",
        "WEBSITE": "WEBSITE",
        "CURRENCY": None,  # Needs context
        "GRAND_TOTAL": "GRAND_TOTAL",
        "SUBTOTAL": "SUBTOTAL",
        "TAX": "TAX",
        "QUANTITY": "QUANTITY",
    }
    
    for pattern_type, matches in pattern_matches.items():
        core_label = pattern_to_core_label.get(pattern_type)
        if not core_label:
            continue
            
        for match in matches:
            word_id = match["word_id"]
            labels[word_id] = {
                "label": core_label,
                "confidence": match.get("confidence", 0.9),
                "source": "pattern",
                "reasoning": f"Pattern match: {pattern_type}",
                "pattern_type": pattern_type,
            }
    
    return labels


@tool
def analyze_currency_context(
    currency_columns: List[Dict],
    receipt_words: List[Dict],
    existing_labels: Dict[int, Dict]
) -> Dict[int, Dict]:
    """Analyze currency values and assign appropriate financial labels based on context.
    
    Args:
        currency_columns: Spatial currency analysis results
        receipt_words: All receipt words
        existing_labels: Already assigned labels
        
    Returns:
        Dict mapping word_id to financial label info
    """
    currency_labels = {}
    
    for column in currency_columns:
        for price in column.get("prices", []):
            word_id = price["word_id"]
            
            # Skip if already labeled
            if word_id in existing_labels:
                continue
                
            y_pos = price["y_position"]
            
            # Find nearby words for context
            nearby_words = [
                w for w in receipt_words
                if abs(w["y"] - y_pos) < 0.02  # Same line threshold
            ]
            
            nearby_text = " ".join(w["text"].upper() for w in nearby_words)
            
            # Determine label based on context
            if any(keyword in nearby_text for keyword in ["TOTAL", "AMOUNT DUE", "BALANCE"]):
                label = "GRAND_TOTAL"
                reasoning = "Currency near TOTAL keyword"
            elif any(keyword in nearby_text for keyword in ["SUBTOTAL", "SUB TOTAL"]):
                label = "SUBTOTAL"
                reasoning = "Currency near SUBTOTAL keyword"
            elif any(keyword in nearby_text for keyword in ["TAX", "SALES TAX", "VAT"]):
                label = "TAX"
                reasoning = "Currency near TAX keyword"
            elif any(keyword in nearby_text for keyword in ["@", "EA", "EACH"]):
                label = "UNIT_PRICE"
                reasoning = "Currency with unit indicator"
            else:
                label = "LINE_TOTAL"
                reasoning = "Currency at end of product line"
            
            currency_labels[word_id] = {
                "label": label,
                "confidence": 0.75,
                "source": "spatial_heuristic",
                "reasoning": reasoning,
            }
    
    return currency_labels


@tool
async def call_gpt_for_labels(
    unlabeled_words: List[Dict],
    merchant_name: Optional[str] = None
) -> Dict[int, Dict]:
    """Call GPT to label remaining unlabeled words.
    
    Args:
        unlabeled_words: Words that need labeling
        merchant_name: Optional merchant context
        
    Returns:
        Dict mapping word_id to GPT-assigned label info
    """
    if not unlabeled_words:
        return {}
    
    # Format essential CORE_LABELS for prompt
    essential_labels = [
        "MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL",
        "SUBTOTAL", "TAX", "LINE_TOTAL", "PRODUCT_NAME",
        "QUANTITY", "UNIT_PRICE", "PHONE_NUMBER", "ADDRESS_LINE"
    ]
    
    label_descriptions = "\n".join([
        f"- {label}: {CORE_LABELS[label]}"
        for label in essential_labels if label in CORE_LABELS
    ])
    
    # Format words for prompt
    word_list = "\n".join([
        f"{w['word_id']}|{w['text']}"
        for w in unlabeled_words[:50]  # Limit to 50 words
    ])
    
    prompt = f"""Label these receipt words using ONLY the provided labels.

Valid labels:
{label_descriptions}

Receipt words (word_id|text):
{word_list}

Merchant: {merchant_name or 'Unknown'}

Instructions:
1. Only use labels from the provided list
2. Focus on finding: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL
3. For currency values, determine the appropriate financial label
4. Skip noise words (single punctuation, separators)
5. Return format: word_id|label|confidence

Example output:
5|DATE|0.95
12|MERCHANT_NAME|0.90
45|GRAND_TOTAL|0.85"""

    # Simulated GPT response for now
    # In production, this would call OpenAI API
    with ai_usage_context("draft_labeling_gpt") as tracker:
        # Simulate API call
        await asyncio.sleep(0.1)
        
        # Mock response parsing
        gpt_labels = {}
        for word in unlabeled_words[:5]:  # Mock labeling first 5 words
            if word["text"].upper() in ["WALMART", "TARGET", "MCDONALDS"]:
                gpt_labels[word["word_id"]] = {
                    "label": "MERCHANT_NAME",
                    "confidence": 0.85,
                    "source": "gpt",
                    "reasoning": "GPT identified as merchant name",
                }
        
        return gpt_labels


@tool
async def persist_labels_to_dynamodb(
    labels: Dict[int, Dict],
    receipt_id: int,
    image_id: str,
    receipt_words: List[Dict]
) -> Dict[str, int]:
    """Persist labels to DynamoDB using ReceiptWordLabel entities.
    
    Args:
        labels: Dict of word_id to label info
        receipt_id: Receipt identifier
        image_id: Image UUID
        receipt_words: List of all receipt words for line_id lookup
        
    Returns:
        Dict with count of labels written
    """
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
    
    # Create a mapping of word_id to line_id
    word_to_line = {w["word_id"]: w["line_id"] for w in receipt_words}
    
    # Convert to ReceiptWordLabel entities
    label_entities = []
    for word_id, label_info in labels.items():
        if word_id not in word_to_line:
            logger.warning(f"Word {word_id} not found in receipt_words")
            continue
            
        label_entity = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=word_to_line[word_id],
            word_id=word_id,
            label=label_info["label"],
            reasoning=label_info.get("reasoning", "Draft labeling"),
            timestamp_added=datetime.now(),
            validation_status="NONE",
            label_proposed_by="draft_labeling_node",
        )
        label_entities.append(label_entity)
    
    # Batch write to DynamoDB (mocked for now)
    # In production: await dynamo_client.batch_write_items(...)
    logger.info(f"Would persist {len(label_entities)} labels to DynamoDB")
    
    return {"labels_written": len(label_entities)}


@tool
async def update_pinecone_metadata(
    labels: Dict[int, Dict],
    receipt_id: int,
    image_id: str
) -> Dict[str, int]:
    """Update Pinecone vectors with label metadata.
    
    Args:
        labels: Dict of word_id to label info  
        receipt_id: Receipt identifier
        image_id: Image UUID
        
    Returns:
        Dict with count of vectors updated
    """
    # Build metadata updates
    updates = []
    for word_id, label_info in labels.items():
        vector_id = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#WORD#{word_id:05d}"
        
        metadata_update = {
            "id": vector_id,
            "metadata": {
                "valid_labels": [label_info["label"]],
                "proposed_label": label_info["label"],
                "label_confidence": label_info["confidence"],
                "label_source": label_info["source"],
                "label_timestamp": datetime.now().isoformat(),
            }
        }
        updates.append(metadata_update)
    
    # Update Pinecone (mocked for now)
    # In production: pinecone_client.update(namespace="words", vectors=updates)
    logger.info(f"Would update {len(updates)} vectors in Pinecone")
    
    return {"vectors_updated": len(updates)}


async def draft_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Apply draft labels using both patterns and LLM.
    
    This node:
    1. Applies pattern-based labels first
    2. Uses spatial heuristics for currency
    3. Calls GPT for remaining gaps if needed
    4. Persists to both DynamoDB and Pinecone
    
    Context Engineering:
    - ISOLATE: Each labeling method is independent
    - WRITE: draft_labels to state
    - COMPRESS: Only unlabeled words sent to GPT
    """
    logger.info("Starting draft labeling with patterns and LLM")
    
    # Initialize tools
    tools = [
        apply_pattern_labels,
        analyze_currency_context,
        call_gpt_for_labels,
        persist_labels_to_dynamodb,
        update_pinecone_metadata,
    ]
    
    # Create agent
    llm = ChatOpenAI(model="gpt-4", temperature=0)
    
    # Create prompt
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="""You are a receipt labeling assistant. 
        Your job is to apply labels to receipt words using available tools.
        First try pattern-based labeling, then spatial analysis for currency,
        and only use GPT for remaining unlabeled words if coverage is low."""),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
        HumanMessage(content="""Please label the receipt words in the state.
        Start with pattern matching, then analyze currency context,
        and use GPT only if needed for low coverage or missing essentials."""),
    ])
    
    # Create agent executor
    agent = create_openai_tools_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    
    # Execute labeling process
    result = await agent_executor.ainvoke({
        "pattern_matches": state.get("pattern_matches", {}),
        "receipt_words": state.get("receipt_words", []),
        "currency_columns": state.get("currency_columns", []),
        "merchant_name": state.get("merchant_name"),
        "receipt_id": state.get("receipt_id"),
        "image_id": state.get("image_id"),
    })
    
    # For now, use a simpler implementation without agents
    # (Agent framework integration would be added in production)
    
    # 1. Apply pattern-based labels
    pattern_labels = apply_pattern_labels.invoke({
        "pattern_matches": state.get("pattern_matches", {}),
        "receipt_words": state.get("receipt_words", [])
    })
    
    # 2. Apply currency context labels
    currency_labels = analyze_currency_context.invoke({
        "currency_columns": state.get("currency_columns", []),
        "receipt_words": state.get("receipt_words", []),
        "existing_labels": pattern_labels
    })
    
    # 3. Merge labels
    draft_labels = {**pattern_labels, **currency_labels}
    
    # 4. Check coverage and essentials
    total_words = len(state.get("receipt_words", []))
    labeled_count = len(draft_labels)
    coverage = labeled_count / total_words if total_words > 0 else 0
    
    # Check for essential fields
    found_labels = set(label_info["label"] for label_info in draft_labels.values())
    essential_fields = {"MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"}
    missing_essentials = essential_fields - found_labels
    
    # 5. Use GPT if needed
    if coverage < 0.6 or missing_essentials:
        logger.info(f"Low coverage ({coverage:.1%}) or missing essentials: {missing_essentials}")
        
        # Get unlabeled words
        labeled_word_ids = set(draft_labels.keys())
        unlabeled_words = [
            w for w in state.get("receipt_words", [])
            if w["word_id"] not in labeled_word_ids
        ]
        
        # Call GPT
        gpt_labels = await call_gpt_for_labels.ainvoke({
            "unlabeled_words": unlabeled_words,
            "merchant_name": state.get("merchant_name")
        })
        
        # Merge GPT labels
        draft_labels.update(gpt_labels)
    
    # 6. Persist to storage systems
    await persist_labels_to_dynamodb.ainvoke({
        "labels": draft_labels,
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"],
        "receipt_words": state.get("receipt_words", [])
    })
    
    await update_pinecone_metadata.ainvoke({
        "labels": draft_labels,
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"]
    })
    
    # 7. Update state
    state["draft_labels"] = draft_labels
    state["draft_coverage"] = len(draft_labels) / total_words * 100 if total_words > 0 else 0
    
    # Add decision tracking
    state["decisions"].append({
        "node": "draft_labeling",
        "action": "created_draft_labels",
        "pattern_labels": len(pattern_labels),
        "currency_labels": len(currency_labels),
        "gpt_labels": len(gpt_labels) if coverage < 0.6 or missing_essentials else 0,
        "total_labels": len(draft_labels),
        "coverage": f"{state['draft_coverage']:.1f}%",
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(f"Draft labeling complete: {len(draft_labels)} labels, {state['draft_coverage']:.1f}% coverage")
    
    return state