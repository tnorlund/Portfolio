"""Similar term retriever node using Pinecone for label correction suggestions.

This node queries Pinecone to find similar valid labels for invalid ones,
following the validation pipeline pattern from infra/validation_pipeline/lambda.py.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from langchain_core.tools import tool

from ...utils.client_manager import get_client_manager
from ..state import ReceiptProcessingState

logger = logging.getLogger(__name__)


@tool
async def query_pinecone_for_similar_terms(
    invalid_labels: List[Dict],
    merchant_name: Optional[str] = None,
    top_k: int = 5
) -> Dict[int, List[Dict]]:
    """Query Pinecone to find similar valid labels for invalid ones.
    
    Args:
        invalid_labels: List of invalid label dictionaries with word_id, text, label
        merchant_name: Optional merchant context for filtering
        top_k: Number of similar terms to retrieve per invalid label
        
    Returns:
        Dict mapping word_id to list of similar term suggestions
    """
    if not invalid_labels:
        return {}
    
    client_manager = get_client_manager()
    similar_terms = {}
    
    for invalid in invalid_labels:
        word_id = invalid["word_id"]
        text = invalid.get("text", "")
        invalid_label = invalid.get("label", "")
        
        # Skip if no text to query
        if not text or len(text.strip()) < 2:
            continue
        
        try:
            # Build query filters based on validation pipeline pattern
            filter_criteria = {
                "embedding_type": "word",
                "valid_labels": {"$exists": True, "$ne": []},  # Has valid labels
            }
            
            # Add merchant filter if available
            if merchant_name:
                filter_criteria["merchant_name"] = merchant_name
            
            # Query Pinecone using text similarity
            query_response = client_manager.pinecone.query(
                vector=None,  # Will use text if vector is None
                top_k=top_k * 2,  # Get more to filter
                include_metadata=True,
                namespace="words",
                filter=filter_criteria,
                # Use text-based query for semantic similarity
                include_values=False
            )
            
            # Process results and extract valid label suggestions
            suggestions = []
            seen_labels = set()
            
            for match in query_response.matches:
                metadata = match.metadata or {}
                
                # Get valid labels from metadata
                valid_labels = metadata.get("valid_labels", [])
                if isinstance(valid_labels, str):
                    valid_labels = [valid_labels]
                
                # Extract confidence and text similarity
                similarity_score = match.score if hasattr(match, 'score') else 0.5
                matched_text = metadata.get("text", "")
                
                for valid_label in valid_labels:
                    if valid_label not in seen_labels and valid_label != invalid_label:
                        suggestions.append({
                            "suggested_label": valid_label,
                            "confidence": min(similarity_score, 0.9),  # Cap at 0.9
                            "reasoning": f"Similar to '{matched_text}' (score: {similarity_score:.2f})",
                            "source_text": matched_text,
                            "similarity_score": similarity_score,
                        })
                        seen_labels.add(valid_label)
                        
                        # Limit suggestions per invalid label
                        if len(suggestions) >= top_k:
                            break
                
                if len(suggestions) >= top_k:
                    break
            
            # Sort by confidence descending
            suggestions.sort(key=lambda x: x["confidence"], reverse=True)
            
            if suggestions:
                similar_terms[word_id] = suggestions[:top_k]
            
        except Exception as e:
            logger.warning(f"Failed to query Pinecone for word_id {word_id}: {e}")
            continue
    
    return similar_terms


@tool
async def query_merchant_specific_patterns(
    invalid_labels: List[Dict],
    merchant_name: str,
    top_k: int = 3
) -> Dict[int, List[Dict]]:
    """Query for merchant-specific label patterns.
    
    Args:
        invalid_labels: List of invalid label dictionaries
        merchant_name: Merchant name for specific pattern lookup
        top_k: Number of patterns to retrieve
        
    Returns:
        Dict mapping word_id to merchant-specific suggestions
    """
    if not merchant_name or not invalid_labels:
        return {}
    
    client_manager = get_client_manager()
    merchant_suggestions = {}
    
    # Build merchant-specific filter
    filter_criteria = {
        "embedding_type": "word",
        "merchant_name": merchant_name,
        "valid_labels": {"$exists": True, "$ne": []},
        # Look for high-confidence labels from this merchant
        "label_confidence": {"$gte": 0.8}
    }
    
    try:
        # Query for merchant-specific patterns
        for invalid in invalid_labels:
            word_id = invalid["word_id"]
            text = invalid.get("text", "").upper()
            
            if not text:
                continue
            
            # Query for exact or similar text matches at this merchant
            query_response = client_manager.pinecone.query(
                vector=None,
                top_k=top_k * 2,
                include_metadata=True,
                namespace="words",
                filter={
                    **filter_criteria,
                    "text": {"$regex": f".*{text[:5]}.*"}  # Partial text match
                }
            )
            
            suggestions = []
            for match in query_response.matches:
                metadata = match.metadata or {}
                valid_labels = metadata.get("valid_labels", [])
                
                if isinstance(valid_labels, str):
                    valid_labels = [valid_labels]
                
                for label in valid_labels:
                    suggestions.append({
                        "suggested_label": label,
                        "confidence": 0.85,  # High confidence for merchant-specific
                        "reasoning": f"Common {merchant_name} pattern for '{text}'",
                        "source": "merchant_specific",
                    })
            
            if suggestions:
                # Remove duplicates and limit
                unique_suggestions = []
                seen = set()
                for sugg in suggestions:
                    label = sugg["suggested_label"]
                    if label not in seen:
                        unique_suggestions.append(sugg)
                        seen.add(label)
                        if len(unique_suggestions) >= top_k:
                            break
                
                merchant_suggestions[word_id] = unique_suggestions
    
    except Exception as e:
        logger.warning(f"Failed to query merchant patterns for {merchant_name}: {e}")
    
    return merchant_suggestions


@tool
async def query_contextual_suggestions(
    invalid_labels: List[Dict],
    receipt_words: List[Dict],
    top_k: int = 3
) -> Dict[int, List[Dict]]:
    """Query for contextual label suggestions based on nearby words.
    
    Args:
        invalid_labels: List of invalid label dictionaries
        receipt_words: All receipt words for context
        top_k: Number of contextual suggestions
        
    Returns:
        Dict mapping word_id to context-based suggestions
    """
    if not invalid_labels or not receipt_words:
        return {}
    
    client_manager = get_client_manager()
    contextual_suggestions = {}
    
    # Create word position mapping
    word_positions = {w["word_id"]: w for w in receipt_words}
    
    for invalid in invalid_labels:
        word_id = invalid["word_id"]
        
        if word_id not in word_positions:
            continue
        
        word = word_positions[word_id]
        y_position = word.get("y", 0)
        
        # Find nearby words (same line ±0.02 in y-coordinate)
        nearby_words = [
            w for w in receipt_words
            if abs(w.get("y", 0) - y_position) < 0.02 and w["word_id"] != word_id
        ]
        
        if not nearby_words:
            continue
        
        # Build context query with nearby words
        context_texts = [w["text"] for w in nearby_words[:5]]  # Limit context
        context_string = " ".join(context_texts)
        
        try:
            # Query for similar contextual patterns
            query_response = client_manager.pinecone.query(
                vector=None,
                top_k=top_k * 2,
                include_metadata=True,
                namespace="words",
                filter={
                    "embedding_type": "word",
                    "valid_labels": {"$exists": True, "$ne": []},
                    # Look for words with similar spatial context
                    "y": {"$gte": y_position - 0.05, "$lte": y_position + 0.05}
                }
            )
            
            suggestions = []
            for match in query_response.matches:
                metadata = match.metadata or {}
                valid_labels = metadata.get("valid_labels", [])
                
                if isinstance(valid_labels, str):
                    valid_labels = [valid_labels]
                
                for label in valid_labels:
                    suggestions.append({
                        "suggested_label": label,
                        "confidence": 0.7,  # Moderate confidence for contextual
                        "reasoning": f"Contextually similar to words near '{context_string[:50]}'",
                        "source": "contextual",
                    })
            
            # Remove duplicates and limit
            if suggestions:
                unique_suggestions = []
                seen = set()
                for sugg in suggestions:
                    label = sugg["suggested_label"]
                    if label not in seen:
                        unique_suggestions.append(sugg)
                        seen.add(label)
                        if len(unique_suggestions) >= top_k:
                            break
                
                contextual_suggestions[word_id] = unique_suggestions
        
        except Exception as e:
            logger.warning(f"Failed to query contextual suggestions for word_id {word_id}: {e}")
            continue
    
    return contextual_suggestions


async def similar_term_retriever_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Retrieve similar valid labels for invalid ones using Pinecone queries.
    
    This node follows the validation pipeline pattern, using multiple query strategies:
    1. Semantic similarity search for the invalid label text
    2. Merchant-specific pattern lookup
    3. Contextual suggestions based on nearby words
    
    Context Engineering:
    - SELECT: Only invalid labels are queried
    - WRITE: similar_terms dict to state
    - COMPRESS: Multiple query strategies combined into single response
    """
    logger.info("Starting similar term retrieval for invalid labels")
    
    invalid_labels = state.get("invalid_labels", [])
    
    if not invalid_labels:
        logger.info("No invalid labels to process")
        state["similar_terms"] = {}
        return state
    
    # Get context from state
    merchant_name = state.get("merchant_name")
    receipt_words = state.get("receipt_words", [])
    
    # Execute multiple query strategies in parallel
    tasks = []
    
    # 1. Semantic similarity search
    semantic_task = query_pinecone_for_similar_terms.ainvoke({
        "invalid_labels": invalid_labels,
        "merchant_name": merchant_name,
        "top_k": 3
    })
    tasks.append(("semantic", semantic_task))
    
    # 2. Merchant-specific patterns (if merchant known)
    if merchant_name:
        merchant_task = query_merchant_specific_patterns.ainvoke({
            "invalid_labels": invalid_labels,
            "merchant_name": merchant_name,
            "top_k": 2
        })
        tasks.append(("merchant", merchant_task))
    
    # 3. Contextual suggestions
    contextual_task = query_contextual_suggestions.ainvoke({
        "invalid_labels": invalid_labels,
        "receipt_words": receipt_words,
        "top_k": 2
    })
    tasks.append(("contextual", contextual_task))
    
    # Wait for all queries to complete
    import asyncio
    results = {}
    for strategy, task in tasks:
        try:
            result = await task
            results[strategy] = result
        except Exception as e:
            logger.warning(f"Failed {strategy} query: {e}")
            results[strategy] = {}
    
    # Merge results with priority: semantic > merchant > contextual
    merged_similar_terms = {}
    
    for word_id in [inv["word_id"] for inv in invalid_labels]:
        word_suggestions = []
        
        # Add semantic suggestions first (highest priority)
        if word_id in results.get("semantic", {}):
            word_suggestions.extend(results["semantic"][word_id])
        
        # Add merchant suggestions
        if word_id in results.get("merchant", {}):
            word_suggestions.extend(results["merchant"][word_id])
        
        # Add contextual suggestions
        if word_id in results.get("contextual", {}):
            word_suggestions.extend(results["contextual"][word_id])
        
        # Remove duplicates and sort by confidence
        unique_suggestions = []
        seen_labels = set()
        
        for sugg in word_suggestions:
            label = sugg["suggested_label"]
            if label not in seen_labels:
                unique_suggestions.append(sugg)
                seen_labels.add(label)
        
        # Sort by confidence and limit to top 5
        unique_suggestions.sort(key=lambda x: x["confidence"], reverse=True)
        merged_similar_terms[word_id] = unique_suggestions[:5]
    
    # Update state
    state["similar_terms"] = merged_similar_terms
    
    # Calculate retrieval metrics
    total_invalid = len(invalid_labels)
    words_with_suggestions = len(merged_similar_terms)
    total_suggestions = sum(len(suggestions) for suggestions in merged_similar_terms.values())
    
    # Add decision tracking
    state["decisions"].append({
        "node": "similar_term_retriever",
        "action": "retrieved_similar_terms",
        "invalid_labels_count": total_invalid,
        "words_with_suggestions": words_with_suggestions,
        "total_suggestions": total_suggestions,
        "strategies_used": list(results.keys()),
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(
        f"Similar term retrieval complete: {words_with_suggestions}/{total_invalid} "
        f"words have suggestions ({total_suggestions} total suggestions)"
    )
    
    return state