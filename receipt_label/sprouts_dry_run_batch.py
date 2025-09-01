#!/usr/bin/env python3
"""
Dry run analysis on all Sprouts receipts and save results locally.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.costco_analyzer import setup_langsmith_tracing


# All Sprouts receipts to analyze
sprouts_receipts: List[Tuple[str, int]] = [
    ("87bbf863-e4e2-4079-ac28-82e7abee02fb", 1),
    ("b6e7af49-3802-46f2-822e-bbd49cd55ada", 1),
    ("99a224b4-8af2-4bf4-a2bc-1c6c82640f12", 1),
    ("49d8fde2-7ffd-4913-a9c0-6731e74fa147", 1),
    ("ef8c6b37-c8f5-40f1-bd01-36e32a4a0dc7", 1),
    ("25244d14-5069-4220-a9ff-4a8c56d50b34", 1),
    ("60a66445-0505-4f5b-ad12-0d4ec22643a1", 1),
    ("6c180601-5d7e-4429-88ed-ea908d19e9ae", 1),
    ("86cca8b7-db22-4844-b6f7-d9130e3bae57", 1),
    ("1b441d33-6434-449f-b4c7-c98e70861277", 1),
    ("6d7db41f-6e9b-4f43-a577-3208194f64e5", 2),
    ("95718576-7873-4170-9b71-7736a44a2bd1", 1),
    ("646f8f0f-2907-4ec7-8196-b5223ceb222e", 1),
    ("79ad7b62-ea9d-4636-a411-8fe3f837ca96", 2),
    ("32e93177-6528-4d63-b637-45d53d1cf3f3", 2),
    ("b6e7af49-3802-46f2-822e-bbd49cd55ada", 2),
    ("07550815-fce8-445a-affe-3e3125625fae", 1),
    ("75ec2ac9-e043-4270-b7ec-67e4f225dae9", 2),
    ("6a23eb4b-f1a9-40c0-9cfa-c2a45d3940a9", 2),
    ("ec17c1aa-612f-4466-91b2-075b836c9b3b", 1),
    ("7c932424-cb54-4fdb-ac2d-465e9b57b7c8", 1),
    ("03fa2d0f-33c6-43be-88b0-dae73ec26c93", 1),
    ("687da832-1d9b-48c2-a295-1cc122c23ed8", 2),
    ("303ef1c5-6096-4385-9763-f4cfcc43a6e7", 1),
    ("93db2c10-ad41-4483-a48a-e6fe2b884e79", 1),
    ("adb04b5e-4714-4e33-a3e8-7a874ad1549a", 1),
    ("54b34a70-fa63-4baa-b4a8-cb5473bc5720", 1),
    ("6477f91c-c9bc-4b5d-be34-e172202e3f54", 1),
    ("646f8f0f-2907-4ec7-8196-b5223ceb222e", 2),
    ("6f2f1d08-af1e-4716-a26c-dea4c065ab62", 1),
    ("44dc1b44-a656-4dc1-bd41-4abf9d832827", 1),
    ("9af03fd2-e3a5-4798-99f5-3db3f5db00fd", 1),
    ("e936747a-4231-4ea6-be52-875975929a8a", 2),
    ("1d3b61e9-96b5-4cb4-a1a3-d2a35a30e2cd", 1),
    ("069e270a-a3ce-40dc-81bc-1f05d7f23ac2", 1),
    ("4619f1bf-08b8-483b-a597-4b4f9ece48e6", 1),
    ("d48a0ef4-e518-4a53-8414-0abc5f677a95", 1),
    ("ed3f3909-62d8-430b-9a1a-e82628d9d5ee", 2),
    ("5744e67f-1867-4c8a-a967-de567b4a0d71", 1),
    ("0d82e9a0-fac2-4bda-811f-bcb32ebfcb33", 1),
    ("105a4af7-9381-418c-b1ef-be1b4f6a2991", 1),
    ("cb868dc8-0c7a-46ab-bd6e-8994222c4a0d", 1),
    ("fa4fd183-8208-48b3-93d2-8f2fe10e8ddc", 1),
    ("04a1dfe7-1858-403a-b74f-6edb23d0deb4", 1),
    ("aabbf168-7a61-483b-97c7-e711de91ce5f", 1),
    ("0fd6e62e-a15e-4f89-962f-ae6dc2c4a0c3", 1),
    ("5985d2dd-462e-4ccb-928c-4f4d596d6be2", 1),
    ("c914012e-3fc3-4ba2-b314-fa7d423acac8", 2),
    ("d28fcc1e-642c-4d1b-89ec-8fd9f456ea78", 1),
    ("c3080014-6536-45ff-8280-b5ccc9f24751", 1),
    ("99a224b4-8af2-4bf4-a2bc-1c6c82640f12", 2),
    ("93247564-a489-4970-9992-1a436af35ef5", 1),
    ("80d76b93-9e71-42ee-a650-d176895d965a", 2),
    ("11da3353-cc82-45d8-829f-26f2f124d3aa", 1),
    ("04ebdb8a-b560-4c00-aa09-f6afa3bda458", 1),
    ("ceb066a5-b621-46b6-9f8f-754d998670a7", 2),
    ("c2c5aadf-58e8-4276-95ae-0f46039946da", 1),
    ("c4e2ee59-055f-4add-8dcf-ecb203da54a8", 1),
    ("cdf8dbe6-8cde-47e4-a988-6bad54e67105", 1),
    ("e936747a-4231-4ea6-be52-875975929a8a", 1),
    ("83e273c8-1703-43f4-ad5a-7a8c8ea2ed97", 1),
    ("47343fdf-ec16-4e2a-81d1-b2ecd5af723a", 2),
    ("63aa7edd-cf85-42fd-8c62-363981a115fe", 1),
    ("3b594aaf-915e-49cc-97e6-3a924710cabf", 1),
    ("2c9b770c-9407-4cdc-b0eb-3a5b27f0af15", 2),
    ("cb7e299d-1e2a-4f2d-b519-176fc9fe0e5d", 1),
    ("cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912", 1),
    ("fa4fd183-8208-48b3-93d2-8f2fe10e8ddc", 2),
    ("223c03e2-9f7e-481d-bc33-2b0631fbaaf9", 1),
    ("6e58ca91-4d12-47e1-ac65-a51402090206", 1),
    ("dbc78ee2-8d3c-4a31-b28a-0293583a2a25", 1),
    ("b4b269e9-5213-4e9a-b3df-661bc57dd81a", 1),
    ("dfb96b51-9c1e-43ea-be70-ec4b9d76080c", 1),
    ("8729d932-8637-4ab6-b38b-bae0e92c51e5", 1),
    ("4d0bdfd5-4d5f-4c01-8f20-7f21090ab361", 2),
    ("6f323479-9357-464b-b2e6-2883fde9f019", 1),
    ("53b8442d-754f-44c7-aa2d-2276c089af88", 1),
    ("d5f79185-3747-4a3f-81cd-a9a8ce8ca6b4", 1),
    ("a49fd6b9-5576-4a42-becd-1428a030ea07", 2),
    ("c985752b-1ef2-41cf-9707-76d84b7e3320", 1),
    ("a0301717-d765-4f34-a15d-48c362ebf9fd", 1),
    ("32e93177-6528-4d63-b637-45d53d1cf3f3", 1),
    ("0e222ed9-5a1f-49b3-be48-483d13442881", 2),
    ("95718576-7873-4170-9b71-7736a44a2bd1", 2),
    ("7118d82b-5558-4db1-8bec-caebf990efbf", 1),
    ("2dd75581-e04f-4c55-8c8e-d18c8518af1e", 1),
    ("b4b269e9-5213-4e9a-b3df-661bc57dd81a", 2),
    ("79ad7b62-ea9d-4636-a411-8fe3f837ca96", 1),
    ("ceb066a5-b621-46b6-9f8f-754d998670a7", 1),
    ("80d76b93-9e71-42ee-a650-d176895d965a", 1),
    ("2ab3cb1c-de47-4e25-b8fc-dd6f7cbde62c", 1),
    ("a018fe66-e186-4b6d-8e15-a40d7ac55d04", 2),
    ("5c13b5c3-2244-4ea6-8872-925839ab22f7", 2),
    ("ae0d9a91-ee91-4b88-aa68-881799eb9ab2", 1),
    ("37900099-30b4-4788-b128-73512a936045", 1),
    ("18f583d7-a278-4311-bb36-7abd0c97b2a1", 1),
    ("63999f30-44e2-4019-bb90-4284420f2df3", 1),
    ("735d8627-836b-44b7-9698-554172454bdb", 1),
    ("aebf9f1b-739a-432c-9fc2-d8408a166543", 1),
    ("232ae902-fee0-426e-b1ea-488a806028ae", 1),
    ("6e41d36d-f78d-4836-8aa7-da33d2fe3785", 1),
    ("9e630e7a-64f1-4b03-aac2-5698e28efbb6", 1),
    ("cee0fbe1-d84a-4f69-9af1-7166226e8b88", 1),
    ("08239059-378e-4c09-ac15-5d7de5dc23ac", 1),
    ("c77fcf9a-1b15-4147-ba71-09d56010520c", 1),
    ("645a8dcb-ee5e-4207-9b73-567ebd1ccc45", 1),
]


def serialize_currency_label(label) -> Dict[str, Any]:
    """Convert CurrencyLabel to serializable dict."""
    return {
        "word_text": label.word_text,
        "label_type": label.label_type.value if hasattr(label.label_type, 'value') else str(label.label_type),
        "line_number": label.line_number,
        "line_ids": label.line_ids,
        "confidence": label.confidence,
        "reasoning": label.reasoning,
        "value": label.value,
        "position_y": label.position_y,
        "context": label.context
    }


def serialize_receipt_analysis(analysis) -> Dict[str, Any]:
    """Convert ReceiptAnalysis to serializable dict."""
    return {
        "receipt_id": analysis.receipt_id,
        "known_total": analysis.known_total,
        "confidence_score": analysis.confidence_score,
        "processing_time": getattr(analysis, 'processing_time', None),
        "total_lines": analysis.total_lines,
        "discovered_labels": [serialize_currency_label(label) for label in analysis.discovered_labels],
        "validation_results": analysis.validation_results,
        "formatted_text": analysis.formatted_text
    }


async def analyze_single_receipt(
    client: DynamoClient, 
    image_id: str, 
    receipt_id: int,
    analyzer_type: str = "n_parallel"
) -> Dict[str, Any]:
    """Analyze a single receipt and return serializable results."""
    
    print(f"   📄 {image_id}/{receipt_id} - Starting {analyzer_type} analysis...")
    
    try:
        if analyzer_type == "n_parallel":
            from n_parallel_analyzer import analyze_receipt_n_parallel
            result = await analyze_receipt_n_parallel(
                client, image_id, receipt_id,
                update_labels=True,  # Enable dry run label updates
                dry_run=True         # Don't actually update database
            )
        elif analyzer_type == "single_parallel":
            from parallel_two_phase_analyzer import analyze_receipt_parallel_two_phase
            result = await analyze_receipt_parallel_two_phase(
                client, image_id, receipt_id,
                update_labels=True,  # Enable dry run label updates  
                dry_run=True         # Don't actually update database
            )
        elif analyzer_type == "sequential":
            from two_phase_analyzer import analyze_costco_receipt_two_phase
            result = await analyze_costco_receipt_two_phase(
                client, image_id, receipt_id,
                update_labels=True,  # Enable dry run label updates
                dry_run=True         # Don't actually update database
            )
        else:
            raise ValueError(f"Unknown analyzer type: {analyzer_type}")
        
        # Serialize the result
        serialized_result = serialize_receipt_analysis(result)
        
        # Add metadata
        serialized_result.update({
            "image_id": image_id,
            "receipt_id": receipt_id,
            "analyzer_type": analyzer_type,
            "analysis_timestamp": datetime.now().isoformat(),
            "status": "success"
        })
        
        print(f"   ✅ {image_id}/{receipt_id} - {len(result.discovered_labels)} labels, {result.processing_time:.1f}s")
        return serialized_result
        
    except Exception as e:
        print(f"   ❌ {image_id}/{receipt_id} - Analysis failed: {e}")
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "analyzer_type": analyzer_type,
            "analysis_timestamp": datetime.now().isoformat(),
            "status": "failed",
            "error": str(e)
        }


async def run_sprouts_dry_run_batch(
    receipts: List[Tuple[str, int]],
    analyzer_type: str = "n_parallel",
    output_dir: str = "./sprouts_dry_run_results",
    max_concurrent: int = 3
):
    """Run dry run analysis on all Sprouts receipts."""
    
    print(f"🧪 SPROUTS DRY RUN BATCH ANALYSIS")
    print("=" * 80)
    print(f"Analyzer: {analyzer_type}")
    print(f"Receipts: {len(receipts)}")
    print(f"Max concurrent: {max_concurrent}")
    print(f"Output directory: {output_dir}")
    print()
    
    # Setup
    os.environ["LANGCHAIN_PROJECT"] = f"sprouts-dry-run-{analyzer_type}"
    setup_langsmith_tracing()
    
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Process receipts with concurrency limit
    semaphore = asyncio.Semaphore(max_concurrent)
    start_time = time.time()
    
    async def analyze_with_semaphore(image_id: str, receipt_id: int) -> Dict[str, Any]:
        async with semaphore:
            return await analyze_single_receipt(client, image_id, receipt_id, analyzer_type)
    
    print(f"🚀 Starting batch analysis...")
    
    # Create all tasks
    tasks = [
        analyze_with_semaphore(image_id, receipt_id) 
        for image_id, receipt_id in receipts
    ]
    
    # Execute with progress tracking
    results = []
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        result = await coro
        results.append(result)
        
        if (i + 1) % 10 == 0 or (i + 1) == len(tasks):
            elapsed = time.time() - start_time
            print(f"   Progress: {i + 1}/{len(tasks)} completed ({elapsed:.1f}s elapsed)")
    
    total_time = time.time() - start_time
    
    # Generate summary
    successful = [r for r in results if r.get("status") == "success"]
    failed = [r for r in results if r.get("status") == "failed"]
    
    # Save individual results
    for result in results:
        filename = f"{result['image_id']}_{result['receipt_id']}_{analyzer_type}.json"
        output_file = output_path / filename
        
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
    
    # Save batch summary
    summary = {
        "batch_info": {
            "analyzer_type": analyzer_type,
            "total_receipts": len(receipts),
            "successful": len(successful),
            "failed": len(failed),
            "total_time": total_time,
            "avg_time_per_receipt": total_time / len(receipts) if receipts else 0,
            "timestamp": datetime.now().isoformat()
        },
        "successful_analyses": successful,
        "failed_analyses": failed
    }
    
    summary_file = output_path / f"batch_summary_{analyzer_type}.json"
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n📊 BATCH ANALYSIS COMPLETE")
    print("=" * 80)
    print(f"✅ Successful: {len(successful)}/{len(receipts)} receipts")
    print(f"❌ Failed: {len(failed)}/{len(receipts)} receipts")
    print(f"⏱️ Total time: {total_time:.1f}s")
    print(f"📈 Avg time per receipt: {total_time/len(receipts):.1f}s")
    print(f"📁 Results saved to: {output_path}")
    print(f"📋 Summary file: {summary_file}")
    
    if successful:
        total_labels = sum(len(r.get("discovered_labels", [])) for r in successful)
        avg_labels = total_labels / len(successful)
        avg_confidence = sum(r.get("confidence_score", 0) for r in successful) / len(successful)
        
        print(f"\n📊 LABEL DISCOVERY STATS:")
        print(f"   Total labels discovered: {total_labels}")
        print(f"   Average labels per receipt: {avg_labels:.1f}")
        print(f"   Average confidence: {avg_confidence:.3f}")
    
    if failed:
        print(f"\n❌ FAILED RECEIPTS:")
        for result in failed[:5]:  # Show first 5 failures
            print(f"   {result['image_id']}/{result['receipt_id']}: {result.get('error', 'Unknown error')}")
        if len(failed) > 5:
            print(f"   ... and {len(failed) - 5} more failures")


async def main():
    """Main entry point."""
    
    # Check for required environment variables
    if not os.getenv("OLLAMA_API_KEY"):
        print("❌ OLLAMA_API_KEY required")
        print("Set: export OLLAMA_API_KEY='your-api-key'")
        return
    
    if not os.getenv("LANGCHAIN_API_KEY"):
        print("❌ LANGCHAIN_API_KEY required")  
        print("Set: export LANGCHAIN_API_KEY='your-api-key'")
        return
    
    print("🔑 API keys found, proceeding with batch analysis...")
    print()
    
    # Run N-parallel dry run on all Sprouts receipts
    await run_sprouts_dry_run_batch(
        receipts=sprouts_receipts,
        analyzer_type="n_parallel",
        output_dir="./sprouts_dry_run_results", 
        max_concurrent=3  # Conservative to avoid rate limits
    )


if __name__ == "__main__":
    asyncio.run(main())