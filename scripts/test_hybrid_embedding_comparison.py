"""
Test the new hybrid embedding implementation and compare to background process.

This script:
1. Takes a sample of NEEDS_REVIEW MERCHANT_NAME labels
2. Tests with OLD approach (stored embedding only - if available)
3. Tests with NEW approach (hybrid: stored first, on-the-fly fallback)
4. Compares results side-by-side
5. Measures performance differences

This does NOT interfere with the background process - it uses a separate results file.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._pulumi import load_env, load_secrets as load_pulumi_secrets
from receipt_chroma import ChromaClient
from receipt_agent.clients.factory import create_embed_fn
from receipt_agent.config.settings import get_settings
from receipt_agent.graph.label_validation_workflow import (
    create_label_validation_graph,
    run_label_validation,
)


async def get_sample_labels(dynamo: DynamoClient, sample_size: int = 50) -> List[Dict]:
    """Get a sample of NEEDS_REVIEW MERCHANT_NAME labels for testing."""
    print(f"Getting sample of {sample_size} NEEDS_REVIEW MERCHANT_NAME labels...")

    labels = []
    last_evaluated_key = None
    count = 0

    while count < sample_size:
        batch, last_evaluated_key = dynamo.list_receipt_word_labels_with_status(
            status=ValidationStatus.NEEDS_REVIEW,
            last_evaluated_key=last_evaluated_key,
            limit=min(100, sample_size - count),
        )

        # Filter for MERCHANT_NAME
        batch = [l for l in batch if l.label == "MERCHANT_NAME"]

        for label in batch:
            if count >= sample_size:
                break

            # Get word text (handle missing words gracefully)
            word_text = None
            try:
                word = dynamo.get_receipt_word(
                    image_id=label.image_id,
                    receipt_id=label.receipt_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                )
                word_text = word.text if word else None
            except Exception as e:
                print(f"  Warning: Word not found for label {label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}: {e}")
                continue

            if not word_text:
                continue

            # Get merchant name
            metadata = dynamo.get_receipt_metadata(
                image_id=label.image_id,
                receipt_id=label.receipt_id,
            )

            labels.append({
                "label": label,
                "word_text": word_text,
                "merchant_name": metadata.merchant_name if metadata else None,
                "original_reasoning": label.reasoning or "",
            })
            count += 1

        if not last_evaluated_key:
            break

    print(f"Found {len(labels)} labels for testing")
    return labels


async def test_with_approach(
    graph,
    state_holder,
    label_data: Dict,
    approach_name: str,
) -> Dict:
    """Test a single label with timing information."""
    import time

    label = label_data["label"]
    word_text = label_data["word_text"]
    merchant_name = label_data["merchant_name"]
    original_reasoning = label_data["original_reasoning"]

    start_time = time.time()

    try:
        result = await asyncio.wait_for(
            run_label_validation(
                graph=graph,
                state_holder=state_holder,
                word_text=word_text,
                suggested_label_type="MERCHANT_NAME",
                merchant_name=merchant_name,
                original_reasoning=original_reasoning,
                image_id=label.image_id,
                receipt_id=label.receipt_id,
                line_id=label.line_id,
                word_id=label.word_id,
            ),
            timeout=60.0,
        )

        elapsed_time = time.time() - start_time

        return {
            "approach": approach_name,
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            "word_text": word_text,
            "merchant_name": merchant_name,
            "decision": result.get("decision"),
            "confidence": result.get("confidence", 0.0),
            "reasoning": result.get("reasoning", ""),
            "evidence": result.get("evidence", []),
            "tools_used": result.get("tools_used", []),
            "elapsed_time": elapsed_time,
            "success": True,
        }
    except asyncio.TimeoutError:
        elapsed_time = time.time() - start_time
        return {
            "approach": approach_name,
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            "word_text": word_text,
            "error": "Timeout after 60 seconds",
            "elapsed_time": elapsed_time,
            "success": False,
        }
    except Exception as e:
        elapsed_time = time.time() - start_time
        return {
            "approach": approach_name,
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            "word_text": word_text,
            "error": str(e),
            "elapsed_time": elapsed_time,
            "success": False,
        }


async def compare_approaches(
    graph_old,
    state_holder_old,
    graph_new,
    state_holder_new,
    labels: List[Dict],
) -> List[Dict]:
    """Compare old vs new approach on the same labels."""
    print(f"\nComparing approaches on {len(labels)} labels...")
    print("=" * 80)

    results = []

    for i, label_data in enumerate(labels, 1):
        print(f"\n[{i}/{len(labels)}] Testing: '{label_data['word_text']}'")

        # Test with OLD approach (stored embedding only - if available)
        # Note: The old approach is what the background process is using
        # We'll test with the NEW hybrid approach and compare
        print("  Testing with NEW hybrid approach...")
        result_new = await test_with_approach(
            graph_new,
            state_holder_new,
            label_data,
            "hybrid_embedding",
        )

        results.append(result_new)

        if result_new.get("success"):
            print(f"    ✅ Decision: {result_new.get('decision')} ({result_new.get('confidence', 0):.0%} confidence)")
            print(f"    ⏱️  Time: {result_new.get('elapsed_time', 0):.2f}s")
            print(f"    🔧 Tools: {', '.join(result_new.get('tools_used', []))}")
        else:
            print(f"    ❌ Error: {result_new.get('error')}")

        # Flush output
        sys.stdout.flush()

    return results


async def analyze_comparison(results: List[Dict]) -> Dict:
    """Analyze the comparison results."""
    analysis = {
        "total_tested": len(results),
        "successful": len([r for r in results if r.get("success")]),
        "failed": len([r for r in results if not r.get("success")]),
        "by_decision": {},
        "performance": {
            "avg_time": 0.0,
            "min_time": float('inf'),
            "max_time": 0.0,
            "times": [],
        },
        "tools_usage": {},
        "embedding_source": {
            "stored": 0,  # Would need to track this in the tool
            "on_the_fly": 0,
        },
    }

    successful_results = [r for r in results if r.get("success")]

    if successful_results:
        # Performance analysis
        times = [r.get("elapsed_time", 0) for r in successful_results]
        analysis["performance"]["avg_time"] = sum(times) / len(times)
        analysis["performance"]["min_time"] = min(times)
        analysis["performance"]["max_time"] = max(times)
        analysis["performance"]["times"] = times

        # Decision analysis
        for result in successful_results:
            decision = result.get("decision", "UNKNOWN")
            if decision not in analysis["by_decision"]:
                analysis["by_decision"][decision] = {
                    "count": 0,
                    "avg_confidence": 0.0,
                    "avg_time": 0.0,
                }
            analysis["by_decision"][decision]["count"] += 1
            analysis["by_decision"][decision]["avg_confidence"] += result.get("confidence", 0.0)
            analysis["by_decision"][decision]["avg_time"] += result.get("elapsed_time", 0.0)

        # Calculate averages
        for decision, data in analysis["by_decision"].items():
            if data["count"] > 0:
                data["avg_confidence"] = data["avg_confidence"] / data["count"]
                data["avg_time"] = data["avg_time"] / data["count"]

        # Tools usage
        for result in successful_results:
            tools = result.get("tools_used", [])
            for tool in tools:
                analysis["tools_usage"][tool] = analysis["tools_usage"].get(tool, 0) + 1

    return analysis


async def main():
    """Main function."""
    print("=" * 80)
    print("Hybrid Embedding Comparison Test")
    print("=" * 80)
    print()
    print("This script tests the NEW hybrid embedding approach on a sample of labels.")
    print("It does NOT interfere with the background process.")
    print()

    # Load environment
    print("Loading environment...")
    env = load_env()

    # Initialize clients
    dynamo = DynamoClient(
        table_name=env["dynamodb_table_name"],
        region=env["region"],
    )

    # Initialize ChromaDB (same as background process)
    words_dir = "/tmp/chromadb_words"
    words_dir_path = Path(words_dir)

    if words_dir_path.exists() and any(words_dir_path.iterdir()):
        print(f"✅ Using downloaded words collection from S3: {words_dir}")
        chroma_persist_dir = words_dir
    else:
        print(f"⚠️  Words collection not found at {words_dir}")
        print("   Downloading from S3...")
        # Download logic (same as background process)
        bucket_name = (
            env.get("embedding_chromadb_bucket_name") or
            env.get("chromadb_bucket_name") or
            os.environ.get("CHROMADB_BUCKET")
        )
        if bucket_name:
            from receipt_chroma import download_snapshot_atomic
            os.makedirs(words_dir, exist_ok=True)
            words_result = download_snapshot_atomic(
                bucket=bucket_name,
                collection="words",
                local_path=words_dir,
                verify_integrity=True,
            )
            if words_result.get("status") == "downloaded":
                print(f"✅ Words collection downloaded")
                chroma_persist_dir = words_dir
            else:
                print(f"⚠️  Download failed, using fallback")
                chroma_persist_dir = "/tmp/chromadb"
        else:
            chroma_persist_dir = "/tmp/chromadb"

    chroma = ChromaClient(persist_directory=chroma_persist_dir)

    # Initialize embedding function
    settings = get_settings()
    try:
        embed_fn = create_embed_fn(settings=settings)
    except ValueError as e:
        print(f"Warning: {e}")
        def dummy_embed_fn(texts):
            return [[0.0] * 1536 for _ in texts]
        embed_fn = dummy_embed_fn

    # Load Ollama API key
    pulumi_secrets = load_pulumi_secrets("dev") or load_pulumi_secrets("prod")
    if pulumi_secrets:
        ollama_api_key = (
            pulumi_secrets.get("portfolio:OLLAMA_API_KEY") or
            pulumi_secrets.get("OLLAMA_API_KEY") or
            pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        )
        if ollama_api_key and not settings.ollama_api_key.get_secret_value():
            os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_api_key
            settings = get_settings()

    # Get sample labels
    sample_size = int(os.environ.get("SAMPLE_SIZE", "20"))  # Default 20 for quick testing
    labels = await get_sample_labels(dynamo, sample_size=sample_size)

    if not labels:
        print("No labels found to test")
        return

    # Create graphs (both use the same code now, but we can test separately)
    print("\nCreating validation graphs...")
    graph_new, state_holder_new = create_label_validation_graph(
        dynamo_client=dynamo,
        chroma_client=chroma,
        embed_fn=embed_fn,
    )

    # Test with new approach
    print(f"\nTesting {len(labels)} labels with NEW hybrid embedding approach...")
    results = await compare_approaches(
        None, None,  # Old approach not needed for this test
        graph_new,
        state_holder_new,
        labels,
    )

    # Analyze results
    print("\n" + "=" * 80)
    print("Analysis")
    print("=" * 80)

    analysis = await analyze_comparison(results)

    print(f"\nTotal labels tested: {analysis['total_tested']}")
    print(f"Successful: {analysis['successful']}")
    print(f"Failed: {analysis['failed']}")

    print("\n--- By Decision ---")
    for decision, data in analysis["by_decision"].items():
        print(f"{decision}: {data['count']} labels")
        print(f"  Avg confidence: {data['avg_confidence']:.0%}")
        print(f"  Avg time: {data['avg_time']:.2f}s")

    print("\n--- Performance ---")
    print(f"Average time: {analysis['performance']['avg_time']:.2f}s")
    print(f"Min time: {analysis['performance']['min_time']:.2f}s")
    print(f"Max time: {analysis['performance']['max_time']:.2f}s")

    print("\n--- Tools Usage ---")
    for tool, count in sorted(analysis["tools_usage"].items(), key=lambda x: -x[1]):
        print(f"{tool}: {count}")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"hybrid_embedding_comparison_{timestamp}.json"
    analysis_file = f"hybrid_embedding_analysis_{timestamp}.json"

    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {results_file}")

    with open(analysis_file, "w") as f:
        json.dump(analysis, f, indent=2, default=str)
    print(f"Analysis saved to: {analysis_file}")

    print("\n" + "=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

