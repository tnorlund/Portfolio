#!/usr/bin/env python3
"""
Run label evaluator on multiple Sprouts receipts and collect ROC data.

Generates JSON output with all geometric anomalies and LLM decisions
for threshold optimization analysis.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add repo root to path
repo_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, repo_root)

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.s3.snapshot import download_snapshot_atomic
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_agent.agents.label_evaluator import create_label_evaluator_graph

# Test receipts to evaluate (excluding the one we already tested)
TEST_RECEIPTS = [
    ("00d01b34-2a2f-460a-b317-e37fafbd53ba", 3),
    ("00ded398-af6f-4a49-86f7-c79ccb554e48", 2),
    ("01d3b8e0-f0f8-43d5-af23-07b7c3a73bd9", 3),
    ("03fa2d0f-33c6-43be-88b0-dae73ec26c93", 1),
    ("04a1dfe7-1858-403a-b74f-6edb23d0deb4", 1),
]

def load_environment(stack: str = "dev") -> tuple:
    """Load environment configuration from Pulumi."""
    try:
        infra_dir = os.path.join(repo_root, "infra")
        env = load_env(stack, working_dir=infra_dir)

        table_name = env.get("dynamodb_table_name")
        chroma_bucket = env.get("chromadb_bucket_name")

        if not table_name or not chroma_bucket:
            raise ValueError(
                f"Missing environment: table_name={table_name}, "
                f"chroma_bucket={chroma_bucket}"
            )

        return table_name, chroma_bucket
    except Exception as e:
        print(f"Failed to load environment: {e}")
        raise


def run_roc_analysis():
    """Run evaluator on test receipts and collect ROC data."""

    # Load environment
    try:
        table_name, chroma_bucket = load_environment("dev")
        print(f"✓ Loaded dev environment")
    except Exception as e:
        print(f"Failed to load environment: {e}")
        sys.exit(1)

    # Set environment variables
    os.environ["DYNAMODB_TABLE_NAME"] = table_name

    # Create clients
    try:
        dynamo_client = DynamoClient(table_name)
        print("✓ Created DynamoDB client")
    except Exception as e:
        print(f"Failed to create DynamoDB client: {e}")
        sys.exit(1)

    # Download ChromaDB snapshot
    temp_dir = tempfile.mkdtemp(prefix="roc_analysis_")
    print(f"Downloading ChromaDB snapshot to: {temp_dir}")

    try:
        result = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="words",
            local_path=temp_dir,
            verify_integrity=False,
        )

        if result.get("status") != "downloaded":
            print(f"Failed to download snapshot: {result}")
            sys.exit(1)

        print(f"✓ Downloaded version: {result.get('version_id')}")

        # Open ChromaDB client and run analysis
        with ChromaClient(persist_directory=temp_dir, mode="read") as chroma_client:
            print("✓ Opened ChromaDB client")

            # Create graph
            print("Creating label evaluator graph...")
            graph = create_label_evaluator_graph(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                llm_provider="ollama",
                ollama_base_url="https://ollama.com",
            )
            print("✓ Graph created\n")

            # Collect results
            all_results = []

            # Run on each test receipt
            for i, (image_id, receipt_id) in enumerate(TEST_RECEIPTS, 1):
                print(f"\n{'='*70}")
                print(f"Receipt {i}/{len(TEST_RECEIPTS)}: {image_id} (receipt_id={receipt_id})")
                print(f"{'='*70}")

                try:
                    result = graph.invoke({
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "skip_llm_review": False,  # Enable LLM review
                    })

                    # Extract anomaly data
                    issues = result.get("issues_found", [])
                    review_results = result.get("review_results", [])

                    print(f"Issues detected: {len(issues)}")
                    print(f"LLM reviews completed: {len(review_results)}")

                    # Store results for analysis
                    for review in review_results:
                        issue = review.issue
                        all_results.append({
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "word_text": issue.word.text,
                            "label": issue.current_label,
                            "issue_type": issue.issue_type,
                            "word_line": issue.word.line_id,
                            "word_id": issue.word.word_id,
                            "llm_decision": review.decision,
                            "is_valid": review.decision == "VALID",
                            "is_invalid": review.decision == "INVALID",
                            "reasoning": review.reasoning[:100] + "..." if len(review.reasoning) > 100 else review.reasoning,
                        })

                except Exception as e:
                    print(f"Error on receipt {image_id}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            # Save results to JSON
            output_dir = Path(repo_root) / "logs" / "roc_analysis"
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = output_dir / f"roc_data_{timestamp}.json"

            with open(output_file, "w") as f:
                json.dump(all_results, f, indent=2)

            print(f"\n✓ Saved ROC data to: {output_file}")
            print(f"Total anomalies collected: {len(all_results)}")

            # Summary
            valid_count = sum(1 for r in all_results if r["is_valid"])
            invalid_count = sum(1 for r in all_results if r["is_invalid"])
            print(f"\nSummary:")
            print(f"  VALID: {valid_count}")
            print(f"  INVALID: {invalid_count}")
            print(f"  Invalid rate: {invalid_count / len(all_results) * 100:.1f}%" if all_results else "  No anomalies")

    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        # Clean up
        print(f"\nCleaning up {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_roc_analysis()
