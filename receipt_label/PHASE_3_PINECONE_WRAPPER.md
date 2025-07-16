# Phase 3: Pinecone Wrapper Design

## The Problem

You're right to be concerned about data consistency. Currently:
- **DynamoDB**: Has `receipt_dynamo` wrapper ensuring entity structure
- **Pinecone**: Direct SDK usage with manual metadata management
- **Risk**: Metadata drift between systems

## Proposed Solution: PineconeReceiptStore Wrapper

### 1. Core Wrapper Design

```python
# receipt_label/pinecone_wrapper/store.py
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from datetime import datetime
import logging
from pinecone import Index
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)

@dataclass
class PineconeWordMetadata:
    """Standardized metadata for word vectors"""
    # Identity
    image_id: str
    receipt_id: str
    line_id: int
    word_id: int
    
    # Text data
    text: str
    normalized_text: str
    
    # Spatial data
    x: float
    y: float
    width: float
    height: float
    line_number: int
    
    # Labels (Phase 3)
    labels: List[Dict[str, Any]]  # [{"type": "PRODUCT_NAME", "confidence": 0.95}]
    label_source: str  # "pattern", "gpt", "manual"
    label_timestamp: Optional[str] = None
    
    # Merchant context
    merchant_name: Optional[str] = None
    merchant_confidence: Optional[float] = None
    
    # Validation status
    validation_status: Optional[str] = None  # "validated", "failed", "pending"
    validation_errors: Optional[List[str]] = None
    
    def to_pinecone_metadata(self) -> Dict[str, Any]:
        """Convert to Pinecone-compatible metadata"""
        return {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "line_id": self.line_id,
            "word_id": self.word_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "line_number": self.line_number,
            "labels": str(self.labels),  # Pinecone doesn't support nested dicts
            "label_source": self.label_source,
            "label_timestamp": self.label_timestamp or "",
            "merchant_name": self.merchant_name or "",
            "merchant_confidence": self.merchant_confidence or 0.0,
            "validation_status": self.validation_status or "pending",
            "validation_errors": ",".join(self.validation_errors or [])
        }
    
    @classmethod
    def from_pinecone_metadata(cls, metadata: Dict[str, Any]) -> 'PineconeWordMetadata':
        """Reconstruct from Pinecone metadata"""
        import ast
        return cls(
            image_id=metadata["image_id"],
            receipt_id=metadata["receipt_id"],
            line_id=metadata["line_id"],
            word_id=metadata["word_id"],
            text=metadata["text"],
            normalized_text=metadata["normalized_text"],
            x=metadata["x"],
            y=metadata["y"],
            width=metadata["width"],
            height=metadata["height"],
            line_number=metadata["line_number"],
            labels=ast.literal_eval(metadata.get("labels", "[]")),
            label_source=metadata.get("label_source", "unknown"),
            label_timestamp=metadata.get("label_timestamp") or None,
            merchant_name=metadata.get("merchant_name") or None,
            merchant_confidence=metadata.get("merchant_confidence") or None,
            validation_status=metadata.get("validation_status") or None,
            validation_errors=metadata.get("validation_errors", "").split(",") if metadata.get("validation_errors") else None
        )


class PineconeReceiptStore:
    """
    Wrapper for Pinecone operations ensuring consistency with DynamoDB.
    
    This class guarantees:
    1. Consistent metadata structure
    2. Atomic updates to both Pinecone and DynamoDB
    3. Validation before writes
    4. Proper error handling and rollback
    """
    
    def __init__(self, index: Index, dynamo_client):
        self.index = index
        self.dynamo_client = dynamo_client
        self.words_namespace = "words"
        self.lines_namespace = "lines"
        
    def store_labeled_words(
        self, 
        receipt_id: str,
        words: List[ReceiptWord],
        embeddings: List[List[float]],
        labels: Dict[int, Dict[str, Any]],  # word_id -> label info
        merchant_data: Optional[Dict] = None
    ) -> bool:
        """
        Store word embeddings with labels, ensuring consistency.
        
        Args:
            receipt_id: Receipt identifier
            words: List of ReceiptWord entities
            embeddings: Corresponding embeddings from OpenAI
            labels: Label information keyed by word_id
            merchant_data: Optional merchant metadata
            
        Returns:
            Success status
        """
        try:
            # Step 1: Validate all data
            if len(words) != len(embeddings):
                raise ValueError(f"Mismatch: {len(words)} words vs {len(embeddings)} embeddings")
            
            # Step 2: Prepare Pinecone vectors
            vectors = []
            dynamo_updates = []
            
            for word, embedding in zip(words, embeddings):
                # Build metadata
                metadata = PineconeWordMetadata(
                    image_id=word.image_id,
                    receipt_id=receipt_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    text=word.text,
                    normalized_text=word.text.lower().strip(),
                    x=word.x,
                    y=word.y,
                    width=word.width,
                    height=word.height,
                    line_number=word.line_number,
                    labels=labels.get(word.word_id, []),
                    label_source="phase3",
                    label_timestamp=datetime.utcnow().isoformat(),
                    merchant_name=merchant_data.get("name") if merchant_data else None,
                    merchant_confidence=merchant_data.get("confidence") if merchant_data else None
                )
                
                # Create vector ID
                vector_id = self._create_vector_id(word)
                
                vectors.append({
                    "id": vector_id,
                    "values": embedding,
                    "metadata": metadata.to_pinecone_metadata()
                })
                
                # Prepare DynamoDB update
                if word.word_id in labels:
                    dynamo_updates.append({
                        "word": word,
                        "labels": labels[word.word_id]
                    })
            
            # Step 3: Atomic updates (as atomic as possible)
            # Update Pinecone first
            self.index.upsert(
                vectors=vectors,
                namespace=self.words_namespace
            )
            
            # Update DynamoDB
            for update in dynamo_updates:
                label_entity = ReceiptWordLabel(
                    word_id=update["word"].word_id,
                    receipt_id=receipt_id,
                    label_type=update["labels"]["type"],
                    confidence=update["labels"]["confidence"],
                    source="phase3_langgraph",
                    created_at=datetime.utcnow().isoformat()
                )
                self.dynamo_client.put_label(label_entity)
            
            logger.info(f"Successfully stored {len(vectors)} labeled words for receipt {receipt_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store labeled words: {str(e)}")
            # In production, implement rollback logic here
            return False
    
    def update_validation_status(
        self,
        receipt_id: str,
        word_ids: List[int],
        validation_results: Dict[int, Dict[str, Any]]
    ) -> bool:
        """Update validation status in both Pinecone and DynamoDB"""
        try:
            # Update Pinecone metadata
            for word_id in word_ids:
                vector_id = f"IMAGE#{receipt_id}#RECEIPT#{receipt_id:05d}#LINE#{0:05d}#WORD#{word_id:05d}"
                
                if word_id in validation_results:
                    result = validation_results[word_id]
                    self.index.update(
                        id=vector_id,
                        set_metadata={
                            "validation_status": result["status"],
                            "validation_errors": ",".join(result.get("errors", []))
                        },
                        namespace=self.words_namespace
                    )
            
            # Update DynamoDB
            # ... dynamo update logic ...
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update validation status: {str(e)}")
            return False
    
    def query_by_merchant(
        self,
        merchant_name: str,
        limit: int = 100
    ) -> List[PineconeWordMetadata]:
        """Query vectors by merchant with proper type conversion"""
        
        results = self.index.query(
            vector=[0.0] * 1536,  # Dummy vector for metadata-only query
            filter={
                "merchant_name": {"$eq": merchant_name}
            },
            top_k=limit,
            include_metadata=True,
            namespace=self.words_namespace
        )
        
        return [
            PineconeWordMetadata.from_pinecone_metadata(match.metadata)
            for match in results.matches
        ]
    
    def _create_vector_id(self, word: ReceiptWord) -> str:
        """Create consistent vector ID"""
        return f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"


# Sync utility
class PineconeDynamoSync:
    """Ensure consistency between Pinecone and DynamoDB"""
    
    def __init__(self, pinecone_store: PineconeReceiptStore, dynamo_client):
        self.pinecone_store = pinecone_store
        self.dynamo_client = dynamo_client
        
    async def sync_receipt_labels(self, receipt_id: str):
        """Sync all labels for a receipt between systems"""
        # Get labels from DynamoDB (source of truth)
        dynamo_labels = await self.dynamo_client.get_receipt_labels(receipt_id)
        
        # Update Pinecone metadata to match
        updates = {}
        for label in dynamo_labels:
            updates[label.word_id] = {
                "type": label.label_type,
                "confidence": label.confidence,
                "source": label.source
            }
        
        # Apply updates
        return self.pinecone_store.update_word_labels(receipt_id, updates)
```

### 2. Integration with LangGraph Tools

Since you mentioned OpenAI's agents SDK and tool calling, here's how to integrate with LangGraph:

```python
# receipt_label/langgraph_integration/tools.py
from typing import Dict, Any, List
from langchain.tools import Tool
from langchain.pydantic_v1 import BaseModel, Field

class QueryMerchantPatternsInput(BaseModel):
    """Input for querying merchant patterns"""
    merchant_name: str = Field(description="Name of the merchant")
    pattern_type: str = Field(description="Type of pattern: product, transaction, layout")
    limit: int = Field(default=10, description="Maximum results to return")

class UpdateLabelValidationInput(BaseModel):
    """Input for updating label validation status"""
    receipt_id: str = Field(description="Receipt ID")
    word_id: int = Field(description="Word ID")
    validation_status: str = Field(description="Status: validated, failed, pending")
    errors: List[str] = Field(default=[], description="Validation error messages")

def create_pinecone_tools(pinecone_store: PineconeReceiptStore) -> List[Tool]:
    """Create LangGraph-compatible tools for Pinecone operations"""
    
    def query_merchant_patterns(
        merchant_name: str,
        pattern_type: str = "all",
        limit: int = 10
    ) -> Dict[str, Any]:
        """Query Pinecone for merchant-specific patterns"""
        results = pinecone_store.query_by_merchant(merchant_name, limit)
        
        # Filter by pattern type if specified
        if pattern_type != "all":
            results = [
                r for r in results 
                if any(label["type"] == pattern_type for label in r.labels)
            ]
        
        return {
            "merchant": merchant_name,
            "pattern_type": pattern_type,
            "patterns": [
                {
                    "text": r.text,
                    "labels": r.labels,
                    "confidence": r.merchant_confidence
                }
                for r in results
            ],
            "count": len(results)
        }
    
    def update_label_validation(
        receipt_id: str,
        word_id: int,
        validation_status: str,
        errors: List[str] = None
    ) -> Dict[str, Any]:
        """Update validation status in Pinecone"""
        success = pinecone_store.update_validation_status(
            receipt_id,
            [word_id],
            {word_id: {"status": validation_status, "errors": errors or []}}
        )
        
        return {
            "success": success,
            "receipt_id": receipt_id,
            "word_id": word_id,
            "status": validation_status
        }
    
    return [
        Tool(
            name="query_merchant_patterns",
            description="Query Pinecone for merchant-specific patterns and validated labels",
            func=query_merchant_patterns,
            args_schema=QueryMerchantPatternsInput
        ),
        Tool(
            name="update_label_validation",
            description="Update label validation status in Pinecone",
            func=update_label_validation,
            args_schema=UpdateLabelValidationInput
        )
    ]

# Integration with LangGraph workflow
def create_phase3_workflow_with_tools():
    """Create workflow with Pinecone tools"""
    
    # Initialize stores
    pinecone_store = PineconeReceiptStore(
        index=pinecone_client.get_index(),
        dynamo_client=dynamo_client
    )
    
    # Create tools
    tools = create_pinecone_tools(pinecone_store)
    
    # Create agent with tools
    from langchain.agents import AgentExecutor
    from langchain_openai import ChatOpenAI
    
    llm = ChatOpenAI(model="gpt-4", temperature=0)
    agent = create_openai_tools_agent(llm, tools)
    agent_executor = AgentExecutor(agent=agent, tools=tools)
    
    # Now use in LangGraph nodes
    async def merchant_lookup_node(state):
        """Use tool to query merchant patterns"""
        result = await agent_executor.ainvoke({
            "input": f"Query patterns for merchant {state['merchant_name']}"
        })
        state["merchant_patterns"] = result["output"]
        return state
    
    # ... rest of workflow
```

### 3. Consistency Guarantees

```python
# receipt_label/langgraph_integration/consistency.py

class ConsistentLabelStore:
    """
    Ensures labels are consistently stored across both systems.
    Implements two-phase commit pattern.
    """
    
    def __init__(self, pinecone_store: PineconeReceiptStore, dynamo_client):
        self.pinecone = pinecone_store
        self.dynamo = dynamo_client
        
    async def store_labels_atomic(
        self,
        receipt_id: str,
        labels: Dict[int, Dict[str, Any]]
    ) -> bool:
        """
        Atomically store labels in both systems.
        
        Transaction flow:
        1. Prepare phase: Validate all data
        2. Write to DynamoDB (source of truth)
        3. Write to Pinecone
        4. If Pinecone fails, mark for retry
        """
        
        # Phase 1: Prepare
        try:
            # Validate labels
            for word_id, label in labels.items():
                if "type" not in label or "confidence" not in label:
                    raise ValueError(f"Invalid label for word {word_id}")
            
            # Phase 2: Write to DynamoDB first
            dynamo_success = await self._write_to_dynamo(receipt_id, labels)
            if not dynamo_success:
                return False
            
            # Phase 3: Write to Pinecone
            try:
                pinecone_success = await self._write_to_pinecone(receipt_id, labels)
                if not pinecone_success:
                    # Mark for retry but don't fail - DynamoDB is source of truth
                    await self._mark_for_sync(receipt_id)
                    logger.warning(f"Pinecone write failed for {receipt_id}, marked for sync")
            except Exception as e:
                # Same - mark for retry
                await self._mark_for_sync(receipt_id)
                logger.error(f"Pinecone error for {receipt_id}: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Label storage failed: {e}")
            return False
    
    async def _mark_for_sync(self, receipt_id: str):
        """Mark receipt as needing Pinecone sync"""
        await self.dynamo.put_item({
            "pk": f"SYNC_NEEDED#{receipt_id}",
            "sk": "PINECONE",
            "created_at": datetime.utcnow().isoformat(),
            "ttl": int(time.time()) + 86400  # Retry for 24 hours
        })
```

## Summary

1. **Yes, you need a wrapper** - Direct Pinecone SDK usage will lead to inconsistency
2. **The wrapper ensures**:
   - Consistent metadata structure
   - Synchronized updates with DynamoDB
   - Proper error handling
   - Tool integration for LangGraph

3. **Key benefits**:
   - Type safety with dataclasses
   - Atomic updates where possible
   - DynamoDB as source of truth
   - Pinecone for fast queries
   - Integration with LangGraph tools

4. **Tool calling** works perfectly with LangGraph - you can expose Pinecone operations as tools that the agent can call when needed

This approach mirrors what `receipt_dynamo` does for DynamoDB, giving you the same guarantees for Pinecone operations.