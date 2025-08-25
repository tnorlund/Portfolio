#!/usr/bin/env python3
"""
Process Remaining GRAND_TOTAL Labels - Direct Approach
======================================================

Simple script to find and process the remaining NONE status GRAND_TOTAL labels.
"""

import asyncio
from dotenv import load_dotenv
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

# Load environment
load_dotenv()
pulumi_env = load_env()

async def process_remaining_labels():
    print("🔍 FINDING REMAINING NONE STATUS GRAND_TOTAL LABELS")
    print("=" * 55)
    
    # Initialize client
    dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
    
    # Get ALL GRAND_TOTAL labels and filter for NONE status
    all_labels, _ = dynamo_client.get_receipt_word_labels_by_label('GRAND_TOTAL', limit=200)
    
    none_labels = [label for label in all_labels if label.validation_status == ValidationStatus.NONE.value]
    
    print(f"Found {len(none_labels)} labels with NONE validation status")
    
    if len(none_labels) > 0:
        print("\nSample of unprocessed labels:")
        for i, label in enumerate(none_labels[:5]):
            print(f"{i+1}. {label.image_id} - Receipt {label.receipt_id}, Line {label.line_id}")
        
        print(f"\nProcessing {len(none_labels)} labels with parallel validation...")
        
        # Import our parallel validator
        from dev.revalidate_parallel_single_prompts import ParallelSinglePromptValidator
        from receipt_label.langchain_validation import ContextPreparationService, ContextPreparationConfig
        from receipt_label.vector_store import VectorClient
        import os
        from pathlib import Path
        
        # Setup components
        api_key = os.getenv("OLLAMA_API_KEY")
        if not api_key:
            raise ValueError("OLLAMA_API_KEY not found")
        
        # Initialize vector client
        LOCAL_CHROMA_WORD_PATH = Path(__file__).parent / "dev.word_chroma"
        vector_client = VectorClient.create_chromadb_client(
            persist_directory=str(LOCAL_CHROMA_WORD_PATH),
            mode="read",
            metadata_only=True,
        )
        
        # Initialize context service
        context_config = ContextPreparationConfig(
            context_lines=3,
            similarity_threshold=0.7,
            max_similar_words=20
        )
        
        context_service = ContextPreparationService(
            dynamo_client=dynamo_client,
            chroma_client=vector_client,
            config=context_config
        )
        
        # Initialize validator
        validator = ParallelSinglePromptValidator(
            api_key=api_key, 
            dynamo_client=dynamo_client,
            max_concurrent=5
        )
        
        # Prepare contexts
        print("Preparing validation contexts...")
        contexts = await context_service.prepare_validation_context(none_labels)
        print(f"Prepared {len(contexts)} contexts")
        
        # Process in parallel
        print("Starting parallel validation...")
        results = await validator.validate_labels_parallel(
            contexts, none_labels, context_service, dry_run=False
        )
        
        print(f"✅ Successfully processed {len(results)} labels!")
        
    else:
        print("🎉 No unprocessed GRAND_TOTAL labels found!")

if __name__ == "__main__":
    asyncio.run(process_remaining_labels())