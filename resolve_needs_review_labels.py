#!/usr/bin/env python3
"""
Resolve NEEDS_REVIEW Labels - Enhanced Review and Resolution
===========================================================

This script focuses specifically on labels that were validated as NEEDS_REVIEW,
providing enhanced prompting and human-readable output for review decisions.

Two main categories of NEEDS_REVIEW:
1. Parsing errors (JSON format issues) - Need re-validation
2. Ambiguous cases - Need enhanced context or human review

Usage:
    python resolve_needs_review_labels.py --label LINE_TOTAL --limit 10
    python resolve_needs_review_labels.py --label MERCHANT_NAME --auto-resolve --dry-run
"""

import argparse
import asyncio
import json
import logging
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
import os
from dotenv import load_dotenv

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
from receipt_label.vector_store import VectorClient
from receipt_label.langchain_validation import (
    StructuredReceiptValidator,
    ContextPreparationService,
    ContextPreparationConfig,
    update_label_with_validation_result
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
LOCAL_CHROMA_WORD_PATH = Path(__file__).parent / "dev.word_chroma"
load_dotenv()


class NeedsReviewResolver:
    """Specialized resolver for NEEDS_REVIEW labels."""
    
    def __init__(self, api_key: str, dynamo_client):
        self.validator = StructuredReceiptValidator(api_key=api_key)
        self.dynamo_client = dynamo_client
        
        # Enhanced configuration for difficult cases
        self.context_config = ContextPreparationConfig(
            context_lines=5,  # More context for difficult cases
            similarity_threshold=0.6,  # Lower threshold for more examples
            max_similar_words=30  # More examples for better guidance
        )
    
    async def resolve_needs_review_labels(
        self,
        label_type: str,
        limit: Optional[int] = None,
        auto_resolve: bool = False,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Resolve NEEDS_REVIEW labels with enhanced validation.
        
        Args:
            label_type: The type of label to process
            limit: Maximum number of labels to process
            auto_resolve: Automatically resolve parsing errors
            dry_run: Don't update the database
        """
        print(f"🔍 RESOLVING NEEDS_REVIEW LABELS")
        print("=" * 50)
        print(f"📋 Label type: {label_type}")
        print(f"📊 Limit: {limit or 'All'}")
        print(f"🤖 Auto-resolve: {auto_resolve}")
        print(f"⚡ Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'}")
        print()
        
        # Get NEEDS_REVIEW labels
        try:
            needs_review_labels, _ = self.dynamo_client.get_receipt_word_labels_by_validation_status_and_label(
                status=ValidationStatus.NEEDS_REVIEW,
                label=label_type,
                limit=limit
            )
        except Exception as e:
            logger.error(f"Failed to query {label_type} NEEDS_REVIEW labels: {e}")
            return {'error': str(e)}
        
        if not needs_review_labels:
            print(f"✅ No NEEDS_REVIEW {label_type} labels found!")
            return {'processed': 0, 'message': 'No labels to resolve'}
        
        print(f"📊 Found {len(needs_review_labels)} NEEDS_REVIEW {label_type} labels")
        
        # Categorize by issue type
        parsing_errors = []
        ambiguous_cases = []
        
        for label in needs_review_labels:
            reasoning = getattr(label, 'reasoning', '') or ''
            if 'parsing error' in reasoning.lower() or 'invalid json' in reasoning.lower():
                parsing_errors.append(label)
            else:
                ambiguous_cases.append(label)
        
        print(f"  📝 Parsing errors: {len(parsing_errors)}")
        print(f"  🤔 Ambiguous cases: {len(ambiguous_cases)}")
        print()
        
        # Initialize context service
        vector_client = VectorClient.create_chromadb_client(
            persist_directory=str(LOCAL_CHROMA_WORD_PATH),
            mode="read",
            metadata_only=True,
        )
        
        context_service = ContextPreparationService(
            dynamo_client=self.dynamo_client,
            chroma_client=vector_client,
            config=self.context_config
        )
        
        resolved_count = 0
        results = []
        
        # Process parsing errors first (easier to resolve)
        if parsing_errors and auto_resolve:
            print("🔧 RESOLVING PARSING ERRORS")
            print("-" * 30)
            
            for i, label in enumerate(parsing_errors, 1):
                try:
                    result = await self._resolve_single_label(
                        label, context_service, f"Parsing Error {i}", dry_run
                    )
                    if result:
                        resolved_count += 1
                        results.append(result)
                except Exception as e:
                    logger.error(f"Failed to resolve parsing error {i}: {e}")
        
        # Process ambiguous cases with enhanced context
        if ambiguous_cases:
            print(f"\\n🤔 REVIEWING AMBIGUOUS CASES")
            print("-" * 30)
            
            for i, label in enumerate(ambiguous_cases, 1):
                try:
                    result = await self._resolve_single_label(
                        label, context_service, f"Ambiguous Case {i}", dry_run
                    )
                    if result:
                        resolved_count += 1
                        results.append(result)
                except Exception as e:
                    logger.error(f"Failed to resolve ambiguous case {i}: {e}")
        
        # Summary
        print(f"\\n" + "=" * 50)
        print(f"📊 RESOLUTION SUMMARY")
        print("=" * 50)
        print(f"Total NEEDS_REVIEW labels: {len(needs_review_labels)}")
        print(f"Successfully resolved: {resolved_count}")
        print(f"Resolution rate: {(resolved_count / len(needs_review_labels) * 100):.1f}%")
        
        return {
            'label_type': label_type,
            'total_needs_review': len(needs_review_labels),
            'parsing_errors': len(parsing_errors),
            'ambiguous_cases': len(ambiguous_cases),
            'resolved': resolved_count,
            'results': results
        }
    
    async def _resolve_single_label(
        self,
        label: ReceiptWordLabel,
        context_service: ContextPreparationService,
        case_name: str,
        dry_run: bool
    ) -> Optional[Dict[str, Any]]:
        """Resolve a single NEEDS_REVIEW label with enhanced validation."""
        
        # Get enhanced context
        contexts = await context_service.prepare_validation_context([label])
        if not contexts:
            print(f"❌ {case_name}: Could not prepare context")
            return None
        
        context = contexts[0]
        
        # Display the case for review
        word_text = context.word_context.target_word.text
        current_reasoning = getattr(label, 'reasoning', '')[:100]
        
        print(f"\\n📋 {case_name}: \"{word_text}\" ({label.label})")
        print(f"   Current reasoning: {current_reasoning}...")
        
        start_time = time.time()
        
        try:
            # Enhanced validation with more context
            formatted_context = context_service.format_context_for_llm(context)
            
            result = await self.validator.validate_async(
                word_text=word_text,
                label=label.label,
                context=context,
                formatted_context=formatted_context
            )
            
            validation_time = time.time() - start_time
            
            # Display result
            status = result.validation_status
            new_reasoning = result.reasoning[:80]
            confidence = getattr(result, 'confidence', 'N/A')
            
            print(f"   ✨ New result: {status} (confidence: {confidence})")
            print(f"   📝 New reasoning: {new_reasoning}...")
            print(f"   ⏱️  Time: {validation_time:.1f}s")
            
            # Update database if not dry run and status changed
            if not dry_run and status != 'NEEDS_REVIEW':
                updated_label = update_label_with_validation_result(
                    label,
                    result,
                    "needs_review_resolver"
                )
                self.dynamo_client.update_receipt_word_label(updated_label)
                print(f"   💾 Updated in database")
            elif status == 'NEEDS_REVIEW':
                print(f"   🔄 Still NEEDS_REVIEW - requires human attention")
            else:
                print(f"   📋 DRY RUN - would update to {status}")
            
            return {
                'case_name': case_name,
                'word_text': word_text,
                'label_type': label.label,
                'old_status': 'NEEDS_REVIEW',
                'new_status': status,
                'confidence': confidence,
                'processing_time': validation_time,
                'resolved': status != 'NEEDS_REVIEW'
            }
            
        except Exception as e:
            print(f"   ❌ Resolution failed: {str(e)[:50]}...")
            return None


async def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Resolve NEEDS_REVIEW labels")
    parser.add_argument("--label", required=True, help="Label type to resolve")
    parser.add_argument("--limit", type=int, help="Maximum labels to process")
    parser.add_argument("--auto-resolve", action="store_true", help="Auto-resolve parsing errors")
    parser.add_argument("--dry-run", action="store_true", help="Don't update database")
    args = parser.parse_args()
    
    try:
        # Initialize clients
        pulumi_env = load_env()
        dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
        
        # Get API key
        api_key = os.getenv("OLLAMA_API_KEY")
        if not api_key:
            raise ValueError("OLLAMA_API_KEY not found in environment")
        
        # Initialize resolver
        resolver = NeedsReviewResolver(
            api_key=api_key,
            dynamo_client=dynamo_client
        )
        
        # Resolve NEEDS_REVIEW labels
        results = await resolver.resolve_needs_review_labels(
            label_type=args.label,
            limit=args.limit,
            auto_resolve=args.auto_resolve,
            dry_run=args.dry_run
        )
        
        print(f"\\n🎉 Resolution process complete!")
        
    except Exception as e:
        logger.error(f"Main process failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())