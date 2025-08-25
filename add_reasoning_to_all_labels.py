#!/usr/bin/env python3
"""
Add Reasoning Descriptions to All Labels
========================================

Enhanced validation script that adds detailed reasoning to all labels that lack it,
regardless of their current validation status (VALID, INVALID, NEEDS_REVIEW).

Features:
- Progress checkpointing for resume capability
- Batch processing with configurable sizes
- Error handling with retry logic
- Skips labels that already have reasoning
- Comprehensive progress tracking

Usage:
    python add_reasoning_to_all_labels.py --label MERCHANT_NAME --batch-size 100
    python add_reasoning_to_all_labels.py --label PHONE_NUMBER --batch-size 50 --dry-run
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


class ProgressTracker:
    """Tracks validation progress with checkpointing capability."""
    
    def __init__(self, checkpoint_file: Optional[str] = None):
        self.checkpoint_file = checkpoint_file
        self.processed_count = 0
        self.successful_count = 0
        self.failed_count = 0
        self.start_time = time.time()
        self.processed_ids = set()
        
        # Load existing progress if checkpoint exists
        if checkpoint_file and Path(checkpoint_file).exists():
            self.load_checkpoint()
    
    def load_checkpoint(self):
        """Load progress from checkpoint file."""
        try:
            with open(self.checkpoint_file, 'r') as f:
                data = json.load(f)
            
            self.processed_count = data.get('processed_count', 0)
            self.successful_count = data.get('successful_count', 0)
            self.failed_count = data.get('failed_count', 0)
            self.processed_ids = set(data.get('processed_ids', []))
            
            logger.info(f"Loaded checkpoint: {self.processed_count} processed, {len(self.processed_ids)} IDs")
        except Exception as e:
            logger.warning(f"Could not load checkpoint: {e}")
    
    def save_checkpoint(self):
        """Save current progress to checkpoint file."""
        if not self.checkpoint_file:
            return
        
        data = {
            'processed_count': self.processed_count,
            'successful_count': self.successful_count,
            'failed_count': self.failed_count,
            'processed_ids': list(self.processed_ids),
            'timestamp': time.time()
        }
        
        try:
            with open(self.checkpoint_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save checkpoint: {e}")
    
    def is_processed(self, label_id: str) -> bool:
        """Check if a label has already been processed."""
        return label_id in self.processed_ids
    
    def mark_processed(self, label_id: str, success: bool):
        """Mark a label as processed."""
        self.processed_ids.add(label_id)
        self.processed_count += 1
        if success:
            self.successful_count += 1
        else:
            self.failed_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current processing statistics."""
        elapsed = time.time() - self.start_time
        rate = self.processed_count / elapsed if elapsed > 0 else 0
        
        return {
            'processed': self.processed_count,
            'successful': self.successful_count,
            'failed': self.failed_count,
            'success_rate': (self.successful_count / self.processed_count * 100) if self.processed_count > 0 else 0,
            'elapsed_time': elapsed,
            'processing_rate': rate
        }


class ReasoningValidator:
    """Enhanced validator for adding reasoning to labels without it."""
    
    def __init__(self, api_key: str, dynamo_client, max_concurrent: int = 5):
        self.validator = StructuredReceiptValidator(api_key=api_key)
        self.dynamo_client = dynamo_client
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
    
    async def add_reasoning_to_labels(
        self,
        label_type: str,
        batch_size: int = 100,
        dry_run: bool = False,
        progress_tracker: Optional[ProgressTracker] = None
    ) -> Dict[str, Any]:
        """
        Add reasoning to all labels of a specific type that don't have it.
        
        Args:
            label_type: The type of label to process (e.g., 'MERCHANT_NAME')
            batch_size: Number of labels to process in each batch
            dry_run: If True, don't update the database
            progress_tracker: Optional progress tracker for checkpointing
        
        Returns:
            Dictionary with processing results and statistics
        """
        logger.info(f"Starting reasoning addition for {label_type} labels")
        
        # Get all labels of this type
        all_labels, _ = self.dynamo_client.get_receipt_word_labels_by_label(
            label_type, limit=None
        )
        
        if not all_labels:
            logger.warning(f"No {label_type} labels found")
            return {'processed': 0, 'message': 'No labels found'}
        
        # Filter for labels without reasoning
        labels_without_reasoning = []
        for label in all_labels:
            label_id = f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}"
            
            # Skip if already processed in this session
            if progress_tracker and progress_tracker.is_processed(label_id):
                continue
            
            # Check if label already has reasoning
            has_reasoning = (
                hasattr(label, 'reasoning') and 
                label.reasoning and 
                label.reasoning.strip()
            )
            
            if not has_reasoning:
                labels_without_reasoning.append(label)
        
        logger.info(f"Found {len(labels_without_reasoning)} {label_type} labels without reasoning")
        logger.info(f"Total {label_type} labels: {len(all_labels)}")
        
        if not labels_without_reasoning:
            logger.info("All labels already have reasoning!")
            return {'processed': 0, 'message': 'All labels already have reasoning'}
        
        # Initialize context service
        vector_client = VectorClient.create_chromadb_client(
            persist_directory=str(LOCAL_CHROMA_WORD_PATH),
            mode="read",
            metadata_only=True,
        )
        
        context_config = ContextPreparationConfig(
            context_lines=3,
            similarity_threshold=0.7,
            max_similar_words=20
        )
        
        context_service = ContextPreparationService(
            dynamo_client=self.dynamo_client,
            chroma_client=vector_client,
            config=context_config
        )
        
        # Process in batches
        total_processed = 0
        total_successful = 0
        batch_num = 0
        
        for i in range(0, len(labels_without_reasoning), batch_size):
            batch_num += 1
            batch_labels = labels_without_reasoning[i:i + batch_size]
            
            logger.info(f"Processing batch {batch_num}: {len(batch_labels)} labels")
            print(f"\n🔄 BATCH {batch_num} - Processing {len(batch_labels)} {label_type} labels")
            print(f"📊 Progress: {i}/{len(labels_without_reasoning)} labels")
            
            try:
                # Prepare contexts for this batch
                print("🔍 Preparing validation contexts...")
                contexts = await context_service.prepare_validation_context(batch_labels)
                print(f"✅ Prepared {len(contexts)} contexts")
                
                # Process batch in parallel
                batch_results = await self._process_batch_parallel(
                    contexts, batch_labels, context_service, dry_run, progress_tracker
                )
                
                batch_successful = sum(1 for r in batch_results if r is not None)
                total_processed += len(batch_labels)
                total_successful += batch_successful
                
                # Save progress checkpoint
                if progress_tracker:
                    progress_tracker.save_checkpoint()
                
                print(f"✅ Batch {batch_num} complete: {batch_successful}/{len(batch_labels)} successful")
                
                # Brief pause between batches to avoid overloading API
                if i + batch_size < len(labels_without_reasoning):
                    await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Batch {batch_num} failed: {e}")
                print(f"❌ Batch {batch_num} failed: {e}")
                continue
        
        # Final statistics
        stats = {
            'label_type': label_type,
            'total_labels': len(all_labels),
            'labels_needing_reasoning': len(labels_without_reasoning),
            'processed': total_processed,
            'successful': total_successful,
            'failed': total_processed - total_successful,
            'success_rate': (total_successful / total_processed * 100) if total_processed > 0 else 0
        }
        
        if progress_tracker:
            stats.update(progress_tracker.get_stats())
        
        logger.info(f"Completed {label_type}: {total_successful}/{total_processed} successful")
        return stats
    
    async def _process_batch_parallel(
        self,
        contexts: List[Any],
        labels: List[ReceiptWordLabel],
        context_service: ContextPreparationService,
        dry_run: bool,
        progress_tracker: Optional[ProgressTracker]
    ) -> List[Optional[Dict[str, Any]]]:
        """Process a batch of labels in parallel."""
        tasks = []
        
        for context, label in zip(contexts, labels):
            task = self._validate_single_label_with_semaphore(
                context, label, context_service, dry_run, progress_tracker
            )
            tasks.append(task)
        
        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Label {i+1} failed: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _validate_single_label_with_semaphore(
        self,
        context: Any,
        label: ReceiptWordLabel,
        context_service: ContextPreparationService,
        dry_run: bool,
        progress_tracker: Optional[ProgressTracker]
    ) -> Optional[Dict[str, Any]]:
        """Validate a single label with rate limiting."""
        async with self.semaphore:
            label_id = f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}"
            
            try:
                start_time = time.time()
                
                # Format context and validate
                formatted_context = context_service.format_context_for_llm(context)
                
                result = await self.validator.validate_async(
                    word_text=context.word_context.target_word.text,
                    label=context.word_context.label_to_validate,
                    context=context,
                    formatted_context=formatted_context
                )
                
                validation_time = time.time() - start_time
                
                # Update database if not dry run
                if not dry_run:
                    updated_label = update_label_with_validation_result(
                        label,
                        result,
                        "reasoning_addition_validator"
                    )
                    self.dynamo_client.update_receipt_word_label(updated_label)
                
                # Track progress
                if progress_tracker:
                    progress_tracker.mark_processed(label_id, True)
                
                # Progress indicator
                word_text = getattr(context.word_context.target_word, 'text', 'N/A')[:20]
                status = result.validation_status
                mode = 'DRY' if dry_run else 'SAVED'
                print(f"✅ {word_text:20} | {status:12} | {validation_time:.1f}s | {mode}")
                
                return {
                    'label_id': label_id,
                    'validation_result': result.model_dump(),
                    'processing_time': validation_time
                }
                
            except Exception as e:
                logger.error(f"Failed to validate label {label_id}: {e}")
                
                if progress_tracker:
                    progress_tracker.mark_processed(label_id, False)
                
                return None


async def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Add reasoning to all labels without it")
    parser.add_argument("--label", required=True, help="Label type to process")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for processing")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent requests")
    parser.add_argument("--dry-run", action="store_true", help="Don't update database")
    parser.add_argument("--checkpoint-file", help="File for progress checkpointing")
    args = parser.parse_args()
    
    try:
        print("🚀 ADDING REASONING TO ALL LABELS")
        print("=" * 50)
        print(f"📋 Label type: {args.label}")
        print(f"📊 Batch size: {args.batch_size}")
        print(f"🔄 Concurrency: {args.concurrency}")
        print(f"⚡ Mode: {'DRY RUN' if args.dry_run else 'LIVE UPDATE'}")
        print()
        
        # Initialize clients
        pulumi_env = load_env()
        dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
        
        # Get API key
        api_key = os.getenv("OLLAMA_API_KEY")
        if not api_key:
            raise ValueError("OLLAMA_API_KEY not found in environment")
        
        # Initialize progress tracker
        checkpoint_file = args.checkpoint_file or f"progress_{args.label}.json"
        progress_tracker = ProgressTracker(checkpoint_file)
        
        # Initialize validator
        validator = ReasoningValidator(
            api_key=api_key,
            dynamo_client=dynamo_client,
            max_concurrent=args.concurrency
        )
        
        # Process labels
        results = await validator.add_reasoning_to_labels(
            label_type=args.label,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            progress_tracker=progress_tracker
        )
        
        # Print final results
        print("\n" + "=" * 60)
        print("📊 FINAL RESULTS")
        print("=" * 60)
        print(f"Label type: {results['label_type']}")
        print(f"Total labels: {results.get('total_labels', 0)}")
        print(f"Needed reasoning: {results.get('labels_needing_reasoning', 0)}")
        print(f"Processed: {results.get('processed', 0)}")
        print(f"Successful: {results.get('successful', 0)}")
        print(f"Failed: {results.get('failed', 0)}")
        print(f"Success rate: {results.get('success_rate', 0):.1f}%")
        
        if 'processing_rate' in results:
            print(f"Processing rate: {results['processing_rate']:.1f} labels/second")
        
        print(f"\n🎉 Processing complete for {args.label}!")
        
    except Exception as e:
        logger.error(f"Main process failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())