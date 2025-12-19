"""
Quick integration test for constellation anomaly detection with Ollama LLM.

This test:
1. Limits pattern learning to 20 receipts (fast)
2. Tests constellation anomaly detection
3. Uses Ollama for LLM validation of flagged issues

Run: python receipt_agent/tests/integration/test_constellation_ollama.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def run_test():
    """Run constellation anomaly detection test with Ollama."""
    # Import after path setup
    from receipt_dynamo import DynamoClient
    from receipt_agent.agents.label_evaluator import graph as evaluator_graph
    from receipt_agent.agents.label_evaluator import (
        create_label_evaluator_graph,
        run_label_evaluator,
    )

    # Limit receipts for faster testing
    original_max = evaluator_graph.MAX_OTHER_RECEIPTS
    evaluator_graph.MAX_OTHER_RECEIPTS = 20
    logger.info("Limited MAX_OTHER_RECEIPTS to 20 for faster testing")

    # Clear pattern cache
    evaluator_graph._PATTERN_CACHE.clear()

    try:
        # Initialize DynamoDB client (use Pulumi stack output for table name)
        # Get table name from environment or use dev default
        import os
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
        dynamo = DynamoClient(table_name=table_name)
        logger.info(f"Using DynamoDB table: {table_name}")

        # Find a Sprouts receipt with actual data
        logger.info("Finding a Sprouts receipt with words/labels...")
        metadatas, _ = dynamo.get_receipt_metadatas_by_merchant(
            "Sprouts Farmers Market",
            limit=20,
        )

        if not metadatas:
            logger.error("No Sprouts receipts found")
            return

        # Find a receipt with words and labels
        image_id = None
        receipt_id = None
        words = []
        labels = []

        for meta in metadatas:
            test_words = dynamo.list_receipt_words_from_receipt(
                meta.image_id, meta.receipt_id
            )
            test_labels, _ = dynamo.list_receipt_word_labels_for_receipt(
                meta.image_id, meta.receipt_id
            )
            if len(test_words) > 50 and len(test_labels) > 20:
                image_id = meta.image_id
                receipt_id = meta.receipt_id
                words = test_words
                labels = test_labels
                break

        if image_id is None:
            logger.error("No Sprouts receipt found with sufficient words/labels")
            return

        logger.info(f"Testing receipt: {image_id}#{receipt_id}")
        logger.info(f"Receipt has {len(words)} words and {len(labels)} labels")

        # Create graph and run evaluation with Ollama Cloud
        from receipt_agent.config.settings import get_settings
        settings = get_settings()

        logger.info("Creating label evaluator graph with Ollama Cloud LLM...")
        logger.info(f"  Ollama base URL: {settings.ollama_base_url}")
        logger.info(f"  Ollama model: gpt-oss:20b-cloud")

        # Get API key from settings or Pulumi secrets
        ollama_api_key = settings.ollama_api_key.get_secret_value()
        if not ollama_api_key:
            try:
                from receipt_dynamo.data._pulumi import (
                    load_secrets as load_pulumi_secrets,
                )
                pulumi_secrets = load_pulumi_secrets("dev") or load_pulumi_secrets("prod")
                if pulumi_secrets:
                    ollama_api_key = (
                        pulumi_secrets.get("portfolio:OLLAMA_API_KEY")
                        or pulumi_secrets.get("OLLAMA_API_KEY")
                        or pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
                    )
                    if ollama_api_key:
                        logger.info("Loaded Ollama API key from Pulumi secrets")
            except Exception as e:
                logger.debug(f"Could not load Ollama API key from Pulumi: {e}")

        if not ollama_api_key:
            logger.error("RECEIPT_AGENT_OLLAMA_API_KEY not set - cannot use Ollama Cloud")
            return

        graph = create_label_evaluator_graph(
            dynamo,
            llm_provider="ollama",
            llm_model="gpt-oss:20b-cloud",
            ollama_base_url=settings.ollama_base_url,
            ollama_api_key=ollama_api_key,
        )

        # Run WITH LLM review (skip_llm_review=False)
        logger.info("Running label evaluation with Ollama LLM review...")
        result = await run_label_evaluator(
            graph,
            image_id,
            receipt_id,
            skip_llm_review=False,  # Use LLM for validation
        )

        # Print results
        print("\n" + "=" * 80)
        print("CONSTELLATION ANOMALY DETECTION TEST RESULTS")
        print("=" * 80)

        print(f"\nReceipt: {image_id}#{receipt_id}")
        print(f"Words: {len(words)}, Labels: {len(labels)}")

        # Check for constellation-related issues (issues are dicts, not objects)
        issues = result.get("issues", [])
        issues_count = result.get("issues_found", 0)
        constellation_issues = [
            i for i in issues if i.get("type") == "constellation_anomaly"
        ]
        other_issues = [i for i in issues if i.get("type") != "constellation_anomaly"]

        print(f"\nTotal issues found: {issues_count}")
        print(f"  - Constellation anomalies: {len(constellation_issues)}")
        print(f"  - Other anomalies: {len(other_issues)}")

        # Print constellation issues
        if constellation_issues:
            print("\n--- Constellation Anomalies ---")
            for issue in constellation_issues:
                print(f"\nWord: '{issue.get('word_text')}'")
                print(f"  Current label: {issue.get('current_label')}")
                print(f"  Status: {issue.get('suggested_status')}")
                reasoning = issue.get("reasoning", "")[:200]
                print(f"  Reasoning: {reasoning}...")
        else:
            print("\nNo constellation anomalies detected.")

        # Print other issues
        if other_issues:
            print("\n--- Other Anomalies ---")
            for issue in other_issues[:5]:  # Limit to 5
                print(f"\n  Word: '{issue.get('word_text')}' | Label: {issue.get('current_label')}")
                print(f"    Type: {issue.get('type')}")
                print(f"    Status: {issue.get('suggested_status')}")

        # Print LLM review results
        reviews = result.get("reviews", [])
        print(f"\n--- LLM Reviews ({len(reviews)} total) ---")
        for review in reviews[:5]:  # Limit to 5
            print(f"\n  Word: '{review.get('word_text')}'")
            print(f"    LLM Decision: {review.get('decision')}")
            reasoning = review.get("reasoning", "")[:150]
            print(f"    LLM Reasoning: {reasoning}...")

        # Check patterns computed (from result summary)
        merchant_receipts = result.get("merchant_receipts_analyzed")
        if merchant_receipts:
            print(f"\n--- Pattern Statistics ---")
            print(f"  Merchant receipts analyzed: {merchant_receipts}")

        print("\n" + "=" * 80)
        print("TEST COMPLETE")
        print("=" * 80)

    finally:
        # Restore original setting
        evaluator_graph.MAX_OTHER_RECEIPTS = original_max


if __name__ == "__main__":
    asyncio.run(run_test())
