"""
Test the label validation agent against all NEEDS_REVIEW MERCHANT_NAME labels.

This script:
1. Finds all NEEDS_REVIEW MERCHANT_NAME labels
2. Runs the validation agent on each
3. Collects results and analyzes patterns
4. Identifies what context/evidence leads to correct vs incorrect decisions
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

# Add project root to path
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._pulumi import load_env, load_secrets as load_pulumi_secrets
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_agent.clients.factory import create_embed_fn
from receipt_agent.config.settings import get_settings
from receipt_agent.graph.label_validation_workflow import (
    create_label_validation_graph,
    run_label_validation,
)


async def find_all_needs_review_merchant_names(dynamo: DynamoClient) -> List[Dict]:
    """Find all NEEDS_REVIEW MERCHANT_NAME labels."""
    print("Searching for all NEEDS_REVIEW MERCHANT_NAME labels...")

    labels = []
    last_evaluated_key = None

    while True:
        batch, last_evaluated_key = dynamo.list_receipt_word_labels_with_status(
            status=ValidationStatus.NEEDS_REVIEW,
            last_evaluated_key=last_evaluated_key,
            limit=100,
        )

        # Filter for MERCHANT_NAME
        batch = [l for l in batch if l.label == "MERCHANT_NAME"]

        for label in batch:
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
                # Word might not exist - skip this label
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

        if not last_evaluated_key:
            break

    print(f"Found {len(labels)} NEEDS_REVIEW MERCHANT_NAME labels")
    return labels


async def test_validation_agent_on_label(
    graph,
    state_holder,
    label_data: Dict,
) -> Dict:
    """Test the validation agent on a single label."""
    label = label_data["label"]
    word_text = label_data["word_text"]
    merchant_name = label_data["merchant_name"]
    original_reasoning = label_data["original_reasoning"]

    if not word_text:
        return {
            "error": "No word text found",
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
        }

    try:
        result = await run_label_validation(
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
        )

        return {
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            "word_text": word_text,
            "merchant_name": merchant_name,
            "original_reasoning": original_reasoning,
            "decision": result.get("decision"),
            "confidence": result.get("confidence", 0.0),
            "reasoning": result.get("reasoning", ""),
            "evidence": result.get("evidence", []),
            "tools_used": result.get("tools_used", []),
            "conversation": result.get("conversation"),  # Full conversation if debug enabled
        }
    except Exception as e:
        return {
            "error": str(e),
            "label_id": f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            "word_text": word_text,
        }


async def analyze_results(results: List[Dict]) -> Dict:
    """Analyze the results to identify patterns."""
    analysis = {
        "total": len(results),
        "by_decision": {},
        "by_confidence": {
            "high": 0,  # >= 0.8
            "medium": 0,  # 0.5-0.8
            "low": 0,  # < 0.5
        },
        "by_tools_used": {},
        "errors": [],
        "examples": {
            "valid_high_confidence": [],
            "invalid_high_confidence": [],
            "needs_review": [],
        },
    }

    for result in results:
        if "error" in result:
            analysis["errors"].append(result)
            continue

        decision = result.get("decision", "UNKNOWN")
        confidence = result.get("confidence", 0.0)
        tools_used = result.get("tools_used", [])

        # Count by decision
        if decision not in analysis["by_decision"]:
            analysis["by_decision"][decision] = {
                "count": 0,
                "avg_confidence": 0.0,
                "examples": [],
            }
        analysis["by_decision"][decision]["count"] += 1
        analysis["by_decision"][decision]["avg_confidence"] += confidence
        analysis["by_decision"][decision]["examples"].append({
            "word_text": result.get("word_text"),
            "merchant_name": result.get("merchant_name"),
            "confidence": confidence,
            "reasoning": result.get("reasoning", "")[:200],  # First 200 chars
        })

        # Count by confidence
        if confidence >= 0.8:
            analysis["by_confidence"]["high"] += 1
        elif confidence >= 0.5:
            analysis["by_confidence"]["medium"] += 1
        else:
            analysis["by_confidence"]["low"] += 1

        # Count by tools used
        tools_key = ", ".join(sorted(tools_used))
        if tools_key not in analysis["by_tools_used"]:
            analysis["by_tools_used"][tools_key] = 0
        analysis["by_tools_used"][tools_key] += 1

        # Collect examples
        if decision == "VALID" and confidence >= 0.8:
            analysis["examples"]["valid_high_confidence"].append(result)
        elif decision == "INVALID" and confidence >= 0.8:
            analysis["examples"]["invalid_high_confidence"].append(result)
        elif decision == "NEEDS_REVIEW":
            analysis["examples"]["needs_review"].append(result)

    # Calculate averages
    for decision, data in analysis["by_decision"].items():
        if data["count"] > 0:
            data["avg_confidence"] = data["avg_confidence"] / data["count"]

    return analysis


async def main():
    """Main function."""
    import sys
    # Force unbuffered output
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__

    print("=" * 80, flush=True)
    print("Testing Label Validation Agent on All NEEDS_REVIEW MERCHANT_NAME Labels", flush=True)
    print("=" * 80, flush=True)

    # Load environment
    print("\nLoading environment...", flush=True)
    env = load_env()
    print(f"Environment loaded: table={env.get('dynamodb_table_name')}, region={env.get('region')}", flush=True)

    # Initialize clients
    dynamo = DynamoClient(
        table_name=env["dynamodb_table_name"],
        region=env["region"],
    )

    # Initialize ChromaDB - use downloaded words collection from S3
    import os
    from pathlib import Path

    # Check if we have the downloaded words collection
    words_dir = "/tmp/chromadb_words"
    words_dir_path = Path(words_dir)

    if words_dir_path.exists() and any(words_dir_path.iterdir()):
        print(f"\n✅ Using downloaded words collection from S3")
        print(f"   Directory: {words_dir}")
        chroma_persist_dir = words_dir
    else:
        # Try to download from S3 if not found
        print(f"\n📥 Words collection not found at {words_dir}, downloading from S3...")
        bucket_name = (
            env.get("embedding_chromadb_bucket_name") or
            env.get("chromadb_bucket_name") or
            os.environ.get("CHROMADB_BUCKET")
        )

        if bucket_name:
            try:
                from receipt_chroma import download_snapshot_atomic
                os.makedirs(words_dir, exist_ok=True)

                print(f"   Bucket: {bucket_name}")
                print(f"   Target: {words_dir}")

                words_result = download_snapshot_atomic(
                    bucket=bucket_name,
                    collection="words",
                    local_path=words_dir,
                    verify_integrity=True,
                )

                if words_result.get("status") == "downloaded":
                    print(f"✅ Words collection downloaded: {words_result.get('version_id', 'unknown')}")
                    chroma_persist_dir = words_dir
                else:
                    print(f"⚠️  Words download failed: {words_result.get('error', 'unknown error')}")
                    print("   Continuing without ChromaDB (similarity search will be disabled)")
                    chroma_persist_dir = "/tmp/chromadb"  # Fallback
            except Exception as e:
                print(f"⚠️  Could not download ChromaDB from S3: {e}")
                print("   Continuing without ChromaDB (similarity search will be disabled)")
                chroma_persist_dir = "/tmp/chromadb"  # Fallback
        else:
            print("⚠️  No ChromaDB bucket configured, continuing without ChromaDB")
            chroma_persist_dir = "/tmp/chromadb"  # Fallback

    # Initialize ChromaDB client
    print(f"\nInitializing ChromaDB client at: {chroma_persist_dir}")
    chroma = ChromaClient(persist_directory=chroma_persist_dir)

    # Verify words collection exists
    try:
        words_col = chroma.get_collection("words", create_if_missing=False)
        print(f"✅ Words collection verified: {words_col.count()} documents")
    except Exception as e:
        print(f"⚠️  Warning: Could not access words collection: {e}")
        print("   Similarity search will be disabled, but agent will continue")

    # Initialize embedding function
    settings = get_settings()
    try:
        embed_fn = create_embed_fn(settings=settings)
    except ValueError as e:
        print(f"\nWarning: {e}")
        print("Using dummy embedding function (similarity search may not work)")
        def dummy_embed_fn(texts):
            return [[0.0] * 1536 for _ in texts]
        embed_fn = dummy_embed_fn

    # Load Ollama API key from Pulumi secrets if not in settings
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
            print(f"Loaded Ollama API key from Pulumi secrets")

    # Find all labels
    labels = await find_all_needs_review_merchant_names(dynamo)

    if not labels:
        print("No labels found to test")
        return

    # Create the graph
    print("\nCreating validation graph...")
    graph, state_holder = create_label_validation_graph(
        dynamo_client=dynamo,
        chroma_client=chroma,
        embed_fn=embed_fn,
    )

    # Test each label with timeout and better error handling
    print(f"\nTesting {len(labels)} labels...")
    results = []

    # Set up incremental JSON writing
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"label_validation_results_{timestamp}.json"
    analysis_file = f"label_validation_analysis_{timestamp}.json"
    print(f"Results will be saved incrementally to: {results_file}")
    print(f"Analysis will be saved to: {analysis_file}")

    # Initialize results file with empty array
    with open(results_file, "w") as f:
        json.dump([], f, indent=2, default=str)

    import signal
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def timeout_context(seconds: int):
        """Context manager for async timeout."""
        try:
            import asyncio
            task = asyncio.current_task()
            if task:
                # Set a timeout
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=seconds
                    )
                    yield result
                except asyncio.TimeoutError:
                    print(f"  ⚠️  Timeout after {seconds} seconds")
                    raise
            else:
                yield
        except Exception as e:
            raise

    for i, label_data in enumerate(labels, 1):
        word_text = label_data.get('word_text', 'UNKNOWN')
        merchant_name = label_data.get('merchant_name', 'Unknown')
        print(f"\n[{i}/{len(labels)}] Testing: '{word_text}' (merchant: {merchant_name})")
        print(f"  Starting validation...")

        try:
            # Add timeout of 60 seconds per label
            result = await asyncio.wait_for(
                test_validation_agent_on_label(
                    graph=graph,
                    state_holder=state_holder,
                    label_data=label_data,
                ),
                timeout=60.0
            )
            results.append(result)

            # Write result incrementally to JSON file
            try:
                # Read existing results
                with open(results_file, "r") as f:
                    existing_results = json.load(f)

                # Append new result
                existing_results.append(result)

                # Write back atomically (write to temp, then rename)
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=os.path.dirname(results_file)) as tmp:
                    json.dump(existing_results, tmp, indent=2, default=str)
                    tmp_path = tmp.name

                # Atomic rename
                os.replace(tmp_path, results_file)
            except Exception as e:
                print(f"  ⚠️  Warning: Could not write result to JSON: {e}")

            if "error" not in result:
                print(f"  ✅ Decision: {result.get('decision')} ({result.get('confidence', 0):.0%} confidence)")
                print(f"  Tools used: {', '.join(result.get('tools_used', []))}")
            else:
                print(f"  ❌ Error: {result.get('error')}")
        except asyncio.TimeoutError:
            print(f"  ⚠️  Timeout after 60 seconds - skipping")
            error_result = {
                "error": "Timeout after 60 seconds",
                "label_id": f"{label_data['label'].image_id}#{label_data['label'].receipt_id}#{label_data['label'].line_id}#{label_data['label'].word_id}",
                "word_text": word_text,
            }
            results.append(error_result)

            # Write error result incrementally
            try:
                with open(results_file, "r") as f:
                    existing_results = json.load(f)
                existing_results.append(error_result)
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=os.path.dirname(results_file)) as tmp:
                    json.dump(existing_results, tmp, indent=2, default=str)
                    tmp_path = tmp.name
                os.replace(tmp_path, results_file)
            except Exception:
                pass

        except Exception as e:
            print(f"  ❌ Exception: {str(e)}")
            error_result = {
                "error": str(e),
                "label_id": f"{label_data['label'].image_id}#{label_data['label'].receipt_id}#{label_data['label'].line_id}#{label_data['label'].word_id}",
                "word_text": word_text,
            }
            results.append(error_result)

            # Write error result incrementally
            try:
                with open(results_file, "r") as f:
                    existing_results = json.load(f)
                existing_results.append(error_result)
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=os.path.dirname(results_file)) as tmp:
                    json.dump(existing_results, tmp, indent=2, default=str)
                    tmp_path = tmp.name
                os.replace(tmp_path, results_file)
            except Exception:
                pass

        # Flush output
        import sys
        sys.stdout.flush()

    # Analyze results
    print("\n" + "=" * 80)
    print("Analysis")
    print("=" * 80)

    analysis = analyze_results(results)

    print(f"\nTotal labels tested: {analysis['total']}")
    print(f"Errors: {len(analysis['errors'])}")

    print("\n--- By Decision ---")
    for decision, data in analysis["by_decision"].items():
        print(f"{decision}: {data['count']} labels (avg confidence: {data['avg_confidence']:.0%})")

    print("\n--- By Confidence ---")
    print(f"High (>=80%): {analysis['by_confidence']['high']}")
    print(f"Medium (50-80%): {analysis['by_confidence']['medium']}")
    print(f"Low (<50%): {analysis['by_confidence']['low']}")

    print("\n--- By Tools Used ---")
    for tools, count in sorted(analysis["by_tools_used"].items(), key=lambda x: -x[1]):
        print(f"{tools}: {count}")

    # Results already saved incrementally, just create final analysis
    # Verify results file exists and has all results
    try:
        with open(results_file, "r") as f:
            saved_results = json.load(f)
        if len(saved_results) != len(results):
            print(f"⚠️  Warning: Saved results ({len(saved_results)}) != current results ({len(results)})")
            # Re-save to ensure consistency
            with open(results_file, "w") as f:
                json.dump(results, f, indent=2, default=str)
    except Exception as e:
        print(f"⚠️  Warning: Could not verify results file: {e}")
        # Save fresh copy
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2, default=str)

    print(f"\nDetailed results saved to: {results_file}")

    with open(analysis_file, "w") as f:
        json.dump(analysis, f, indent=2, default=str)
    print(f"Analysis saved to: {analysis_file}")

    # Print example cases
    print("\n--- Example Cases ---")
    print(f"\nVALID (high confidence) examples ({len(analysis['examples']['valid_high_confidence'])}):")
    for ex in analysis["examples"]["valid_high_confidence"][:5]:
        print(f"  - '{ex['word_text']}' (merchant: {ex['merchant_name']}, confidence: {ex['confidence']:.0%})")
        print(f"    Reasoning: {ex['reasoning'][:150]}...")

    print(f"\nINVALID (high confidence) examples ({len(analysis['examples']['invalid_high_confidence'])}):")
    for ex in analysis["examples"]["invalid_high_confidence"][:5]:
        print(f"  - '{ex['word_text']}' (merchant: {ex['merchant_name']}, confidence: {ex['confidence']:.0%})")
        print(f"    Reasoning: {ex['reasoning'][:150]}...")

    print(f"\nNEEDS_REVIEW examples ({len(analysis['examples']['needs_review'])}):")
    for ex in analysis["examples"]["needs_review"][:5]:
        print(f"  - '{ex['word_text']}' (merchant: {ex['merchant_name']}, confidence: {ex['confidence']:.0%})")
        print(f"    Reasoning: {ex['reasoning'][:150]}...")

    print("\n" + "=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

