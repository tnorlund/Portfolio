#!/usr/bin/env python3
"""
Benchmark compute_merchant_patterns() with Scalene profiler.

Micro-benchmark for the two-pass label pattern computation algorithm.
Loads real receipt data from DynamoDB and profiles the core geometry
computation, optionally skipping LLM batch classification.

This benchmark is designed to measure performance over time as the package
evolves, enabling regression detection and optimization validation.

Usage (from repo root):
    # Install perf dependencies first
    pip install -e 'receipt_agent[perf]'

    # Profile with 10 Sprouts receipts (default, skip LLM batching)
    python receipt_agent/tests/benchmarks/benchmark_pattern_computation.py \
        --merchant "Sprouts Farmers Market"

    # Include LLM batch classification (slower, more realistic)
    python receipt_agent/tests/benchmarks/benchmark_pattern_computation.py \
        --merchant "Sprouts Farmers Market" --include-batching

    # Custom receipt count
    python receipt_agent/tests/benchmarks/benchmark_pattern_computation.py \
        --merchant "Sprouts Farmers Market" --limit 20

    # Different merchant (use exact name from database, e.g., "Costco
    # Wholesale", "Target Grocery")
    python receipt_agent/tests/benchmarks/benchmark_pattern_computation.py \
        --merchant "Costco Wholesale" --limit 10

    # Verbose logging
    python receipt_agent/tests/benchmarks/benchmark_pattern_computation.py \
        --merchant "Sprouts Farmers Market" --verbose

Note: Merchant names must match exactly as stored in DynamoDB. Common merchants
in dev:
    - "Sprouts Farmers Market" (169 receipts)
    - "Costco Wholesale" (26 receipts)
    - "Vons" (24 receipts)
    - "The Home Depot" (18 receipts)
    - "Target Grocery" (9 receipts)
"""

# pylint: disable=import-outside-toplevel
# Imports delayed for monkey-patching and optional dependencies

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from receipt_agent.agents.label_evaluator.helpers import (
    compute_merchant_patterns,
)
from receipt_agent.agents.label_evaluator.state import (
    MerchantPatterns,
    OtherReceiptData,
)
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


def find_repo_root() -> Path:
    """Find repository root by locating the infra directory."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "infra").exists():
            return parent
    raise RuntimeError(
        "Could not find repo root (looking for 'infra' directory)"
    )


def load_environment(stack: str = "dev") -> str:
    """Load environment configuration from Pulumi."""
    try:
        repo_root = find_repo_root()
        infra_dir = repo_root / "infra"
        env = load_env(stack, working_dir=str(infra_dir))

        table_name = env.get("dynamodb_table_name")
        if not table_name:
            raise ValueError(f"Missing dynamodb_table_name in {stack} stack")

        return table_name
    except Exception as e:
        logger.error("Failed to load environment: %s", e)
        raise


def load_receipt_data(
    dynamo_client: DynamoClient,
    merchant_name: str,
    limit: int = 10,
) -> List[OtherReceiptData]:
    """
    Load receipt data from DynamoDB and convert to OtherReceiptData format.

    Args:
        dynamo_client: DynamoDB client
        merchant_name: Merchant name to query
        limit: Maximum number of receipts to load

    Returns:
        List of OtherReceiptData objects
    """
    logger.info("Loading receipts for merchant: %s", merchant_name)

    other_receipt_data: list[OtherReceiptData] = []
    last_evaluated_key = None

    try:
        # Query receipts by merchant (using ReceiptPlace)
        while len(other_receipt_data) < limit:
            places, last_evaluated_key = (
                dynamo_client.get_receipt_places_by_merchant(
                    merchant_name=merchant_name,
                    limit=min(100, limit - len(other_receipt_data)),
                    last_evaluated_key=last_evaluated_key,
                )
            )

            if not places:
                break

            for place in places:
                if len(other_receipt_data) >= limit:
                    break

                try:
                    # Fetch words for this receipt
                    words = dynamo_client.list_receipt_words_from_receipt(
                        place.image_id, place.receipt_id
                    )

                    # Fetch labels for this receipt
                    labels, _ = (
                        dynamo_client.list_receipt_word_labels_for_receipt(
                            place.image_id, place.receipt_id
                        )
                    )

                    # Create OtherReceiptData
                    other_receipt_data.append(
                        OtherReceiptData(
                            place=place,
                            words=words,
                            labels=labels,
                        )
                    )

                    logger.debug(
                        "Loaded receipt %s#%s (%s words, %s labels)",
                        place.image_id,
                        place.receipt_id,
                        len(words),
                        len(labels),
                    )

                except Exception as e:
                    logger.warning(
                        "Failed to load receipt %s#%s: %s",
                        place.image_id,
                        place.receipt_id,
                        e,
                    )
                    continue

            if not last_evaluated_key:
                break

        if not other_receipt_data:
            raise ValueError(
                f"No receipts found for merchant: {merchant_name}"
            )

        logger.info(
            "✓ Loaded %s receipts (%s words total)",
            len(other_receipt_data),
            sum(len(r.words) for r in other_receipt_data),
        )

        return other_receipt_data

    except Exception as e:
        logger.error("Failed to load receipt data: %s", e)
        raise


def run_pattern_computation(
    other_receipt_data: List[OtherReceiptData],
    merchant_name: str,
    skip_batching: bool = True,
    max_pair_patterns: int = 4,
    max_relationship_dimension: int = 2,
) -> Tuple[Optional[MerchantPatterns], float]:
    """
    Run compute_merchant_patterns() and return result with timing.

    Args:
        other_receipt_data: List of receipts to process
        merchant_name: Merchant name for patterns
        skip_batching: If True, skip LLM batch classification
        max_pair_patterns: Max number of pairs/tuples to compute
        max_relationship_dimension: Relationship dimension (2=pairs, 3+=n-
        tuples)

    Returns:
        (MerchantPatterns result, elapsed time in seconds)
    """
    # Optionally skip LLM batch classification
    if skip_batching:
        import receipt_agent.agents.label_evaluator.helpers as helpers

        def mock_batch_classification(*_args, **_kwargs):  # type: ignore
            """Skip LLM calls by returning empty batches."""
            return {
                "HAPPY": [],
                "AMBIGUOUS": [],
                "ANTI_PATTERN": [],
            }

        original_fn = helpers.batch_receipts_by_quality
        helpers.batch_receipts_by_quality = mock_batch_classification
        logger.info("LLM batch classification skipped (mock enabled)")

    try:
        start_time = time.perf_counter()
        result = compute_merchant_patterns(
            other_receipt_data,
            merchant_name,
            max_pair_patterns=max_pair_patterns,
            max_relationship_dimension=max_relationship_dimension,
        )
        elapsed = time.perf_counter() - start_time

        logger.info("✓ Pattern computation completed in %.2fs", elapsed)
        return result, elapsed

    finally:
        if skip_batching:
            helpers.batch_receipts_by_quality = original_fn


def build_metrics(
    data: List[OtherReceiptData],
    result: Optional[MerchantPatterns],
    elapsed_time: float,
    merchant_name: str,
    skip_batching: bool,
    max_pair_patterns: int = 4,
    max_relationship_dimension: int = 2,
) -> Dict:
    """
    Build comprehensive metrics dictionary.

    Args:
        data: Input receipt data
        result: MerchantPatterns result
        elapsed_time: Execution time in seconds
        merchant_name: Merchant name
        skip_batching: Whether batching was skipped
        max_pair_patterns: Max pairs/tuples computed
        max_relationship_dimension: Relationship dimension

    Returns:
        Metrics dictionary
    """
    total_words = sum(len(r.words) for r in data)
    total_labels = sum(len(r.labels) for r in data)

    metrics = {
        "profiling_metadata": {
            "timestamp": datetime.now().isoformat(),
            "merchant_name": merchant_name,
            "receipt_count": len(data),
            "function": "compute_merchant_patterns",
            "skip_batching": skip_batching,
            "max_pair_patterns": max_pair_patterns,
            "max_relationship_dimension": max_relationship_dimension,
        },
        "execution_metrics": {
            "total_time_seconds": round(elapsed_time, 3),
        },
        "data_metrics": {
            "total_words": total_words,
            "total_labels": total_labels,
            "avg_words_per_receipt": (
                round(total_words / len(data), 1) if data else 0
            ),
            "avg_labels_per_receipt": (
                round(total_labels / len(data), 1) if data else 0
            ),
        },
        "pattern_metrics": {
            "label_pairs_selected": (
                len(result.label_pair_geometry) if result else 0
            ),
            "label_pairs_total": (
                len(result.all_observed_pairs) if result else 0
            ),
            "unique_labels": len(result.label_positions) if result else 0,
            "receipts_processed": result.receipt_count if result else 0,
        },
    }

    return metrics


def run_with_scalene(
    merchant_name: str,
    data: List[OtherReceiptData],
    skip_batching: bool,
    output_dir: Path,
) -> Tuple[Path, float]:
    """
    Run pattern computation with Scalene CLI profiler.

    Args:
        merchant_name: Merchant name
        data: Receipt data to process
        skip_batching: Skip LLM batching
        output_dir: Output directory for reports

    Returns:
        (HTML report path, elapsed time)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = output_dir / f"pattern_computation_{timestamp}.html"
    repo_root = find_repo_root()

    # Create temporary wrapper script
    wrapper_code = f"""
import sys
import time
import json

sys.path.insert(0, "{repo_root}")

# Load data
import pickle
with open("{output_dir}/_data_{timestamp}.pkl", "rb") as f:
    other_receipt_data = pickle.load(f)

# Import function
from receipt_agent.agents.label_evaluator.helpers import (
    compute_merchant_patterns,
)

# Skip batching if requested
skip_batching = {skip_batching}
if skip_batching:
    import receipt_agent.agents.label_evaluator.helpers as helpers
    def mock_batch(*args, **kwargs):
        return {{"HAPPY": [], "AMBIGUOUS": [], "ANTI_PATTERN": []}}
    helpers.batch_receipts_by_quality = mock_batch

# Run computation
start = time.perf_counter()
result = compute_merchant_patterns(other_receipt_data, {repr(merchant_name)})
elapsed = time.perf_counter() - start

# Save only the elapsed time (result is not picklable due to lambda functions)
with open("{output_dir}/_result_{timestamp}.json", "w") as f:
    json.dump({{"elapsed": elapsed}}, f)
"""

    wrapper_script = output_dir / f"_wrapper_{timestamp}.py"
    wrapper_script.write_text(wrapper_code)

    # Save data to pickle file
    data_file = output_dir / f"_data_{timestamp}.pkl"
    with open(data_file, "wb") as f:
        import pickle

        pickle.dump(data, f)

    # Run with Scalene
    cmd = [
        "python",
        "-m",
        "scalene",
        "--html",
        "--outfile",
        str(html_path),
        "--reduced-profile",
        str(wrapper_script),
    ]

    logger.info("Running Scalene profiling...")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error("Scalene failed: %s", result.stderr)
        raise RuntimeError(f"Scalene profiling failed: {result.stderr}")

    # Load result timing
    result_file = output_dir / f"_result_{timestamp}.json"
    with open(result_file) as f:
        result_data = json.load(f)
        elapsed_time = result_data["elapsed"]

    # Cleanup temp files
    wrapper_script.unlink()
    data_file.unlink()
    result_file.unlink()

    logger.info("✓ Scalene report: %s", html_path)

    return html_path, elapsed_time


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Profile compute_merchant_patterns() with Scalene"
    )
    parser.add_argument(
        "--merchant",
        required=True,
        help="Merchant name to query (e.g., Sprouts)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max receipts to load (default: 10)",
    )
    parser.add_argument(
        "--stack",
        default="dev",
        choices=["dev", "prod"],
        help="Pulumi stack (default: dev)",
    )
    parser.add_argument(
        "--skip-batching",
        action="store_true",
        default=True,
        help="Skip LLM batch classification (default: True)",
    )
    parser.add_argument(
        "--include-batching",
        action="store_false",
        dest="skip_batching",
        help="Include LLM batch classification",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/profiling",
        help="Output directory for reports (default: logs/profiling)",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=4,
        help=(
            "Maximum number of label pairs to compute geometry for (default: "
            "4)"
        ),
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=2,
        choices=[2, 3, 4, 5],
        help=(
            "Relationship dimension: 2=pairs, 3=triples, 4=quadruples "
            "(default: 2)"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output with debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Load environment
        logger.info("Loading %s environment...", args.stack)
        table_name = load_environment(args.stack)
        logger.info("✓ Loaded environment")

        # Create DynamoDB client
        dynamo_client = DynamoClient(table_name)

        # Load receipt data
        logger.info("Loading receipt data...")
        other_receipt_data = load_receipt_data(
            dynamo_client, args.merchant, args.limit
        )

        # Run profiling with Scalene
        logger.info("Starting Scalene profiling...")
        html_path, _elapsed_time = run_with_scalene(
            args.merchant,
            other_receipt_data,
            args.skip_batching,
            output_dir,
        )

        # Run once more without Scalene for metrics (avoids Scalene overhead)
        logger.info("Collecting metrics...")
        result, actual_elapsed = run_pattern_computation(
            other_receipt_data,
            args.merchant,
            args.skip_batching,
            max_pair_patterns=args.max_pairs,
            max_relationship_dimension=args.dimension,
        )

        # Build and save metrics
        metrics = build_metrics(
            other_receipt_data,
            result,
            actual_elapsed,
            args.merchant,
            args.skip_batching,
            max_pair_patterns=args.max_pairs,
            max_relationship_dimension=args.dimension,
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        metrics_path = output_dir / f"pattern_computation_{timestamp}.json"
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2)

        # Print summary
        print("\n" + "=" * 70)
        print("PROFILING COMPLETE")
        print("=" * 70)
        print(f"\nMerchant: {args.merchant}")
        print(f"Receipts profiled: {len(other_receipt_data)}")
        print(f"Pattern computation time: {actual_elapsed:.2f}s")
        print(f"Skip batching: {args.skip_batching}")
        print("\nOutputs:")
        print(f"  HTML report: {html_path}")
        print(f"  Metrics JSON: {metrics_path}")
        print("\nNext steps:")
        print(f"  1. Open HTML report in browser: open {html_path}")
        print(f"  2. Review metrics: cat {metrics_path}")
        print("=" * 70 + "\n")

        logger.info("✓ Profiling complete")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error("Error: %s", e)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
