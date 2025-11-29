#!/usr/bin/env python
"""
Test ReceiptMetadataFinder on a single receipt.
"""

import asyncio
import os
import sys

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

# Add receipt_chroma to path if not installed
receipt_chroma_path = os.path.join(repo_root, "receipt_chroma")
if os.path.exists(receipt_chroma_path) and receipt_chroma_path not in sys.path:
    sys.path.insert(0, receipt_chroma_path)


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    infra_dir = os.path.join(repo_root, "infra")
    env = load_env("dev", working_dir=infra_dir)
    secrets = load_secrets("dev", working_dir=infra_dir)

    # Set environment variables
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-east-1"
    )

    # API Keys
    google_places_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY")
    if google_places_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_places_key

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key

    ollama_base_url = secrets.get("portfolio:OLLAMA_BASE_URL", "https://ollama.com")
    os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = ollama_base_url

    ollama_model = secrets.get("portfolio:OLLAMA_MODEL", "gpt-oss:120b-cloud")
    os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = ollama_model

    # OpenAI API key for embeddings
    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        os.environ["OPENAI_API_KEY"] = openai_key

    # ChromaDB
    lines_dir = os.path.join(repo_root, ".chroma_snapshots", "lines")
    words_dir = os.path.join(repo_root, ".chroma_snapshots", "words")

    if os.path.exists(lines_dir) and os.path.exists(words_dir):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_dir
        print(f"✅ ChromaDB: {lines_dir}, {words_dir}")
    else:
        print(f"⚠️  ChromaDB snapshots not found at {lines_dir} or {words_dir}")
        print("   Agent mode may not work without ChromaDB")

    return env, secrets


async def test_receipt():
    """Test the receipt metadata finder on a specific receipt."""
    from receipt_dynamo import DynamoClient
    from receipt_places import PlacesClient, PlacesConfig
    from receipt_agent.tools.receipt_metadata_finder import ReceiptMetadataFinder
    from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
    from receipt_agent.config.settings import get_settings
    from receipt_agent.graph.receipt_metadata_finder_workflow import (
        create_receipt_metadata_finder_graph,
        run_receipt_metadata_finder,
    )

    # Setup clients
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )

    places_config = PlacesConfig(
        api_key=os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"),
        cache_enabled=True,
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )
    places = PlacesClient(config=places_config)

    settings = get_settings()
    chroma_client = create_chroma_client(mode="read", settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    image_id = "cee0fbe1-d84a-4f69-9af1-7166226e8b88"
    receipt_id = 2

    print("=" * 70)
    print("TESTING RECEIPT METADATA FINDER")
    print("=" * 70)
    print(f"Receipt: {image_id}#{receipt_id}")
    print()

    # Get current metadata
    print("📋 CURRENT METADATA:")
    try:
        current = dynamo.get_receipt_metadata(image_id, receipt_id)
        if current:
            print(f"  merchant_name: {current.merchant_name}")
            print(f"  place_id: {current.place_id}")
            print(f"  address: {current.address}")
            print(f"  phone_number: {current.phone_number}")
            print(f"  validation_status: {current.validation_status}")
        else:
            print("  No metadata found")
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("📄 Getting receipt content...")
    try:
        # Use get_receipt_details to get everything in one call
        # Returns: ReceiptDetails object with receipt, lines, words, letters, labels
        details = dynamo.get_receipt_details(
            image_id=image_id,
            receipt_id=receipt_id,
        )

        if details.receipt:
            if details.lines:
                print(f"Lines ({len(details.lines)}):")
                # Sort by line_id for display
                sorted_lines = sorted(details.lines, key=lambda x: x.line_id)
                for i, line in enumerate(sorted_lines[:10], 1):
                    print(f"  {i}. {line.text}")
                if len(details.lines) > 10:
                    print(f"  ... and {len(details.lines) - 10} more")
            else:
                print("Lines: (none)")
            print()

            if details.words:
                print(f"Words ({len(details.words)}):")
                # Show first 10 words
                for i, word in enumerate(details.words[:10], 1):
                    word_text = word.text if hasattr(word, 'text') else str(word)
                    print(f"  {i}. {word_text}")
                if len(details.words) > 10:
                    print(f"  ... and {len(details.words) - 10} more")
            else:
                print("Words: (none)")
        else:
            print("  Receipt not found")
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("🤖 Running Receipt Metadata Finder agent...")
    print()

    # Create graph
    graph, state_holder = create_receipt_metadata_finder_graph(
        dynamo_client=dynamo,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places,
        settings=settings,
    )

    # Run agent
    result = await run_receipt_metadata_finder(
        graph=graph,
        state_holder=state_holder,
        image_id=image_id,
        receipt_id=receipt_id,
    )

    print()
    print("=" * 70)
    print("AGENT RESULTS")
    print("=" * 70)
    print()

    if result.get("found"):
        print("✅ METADATA FOUND:")
        print(f"  place_id: {result.get('place_id') or '(not found)'}")
        print(f"  merchant_name: {result.get('merchant_name') or '(not found)'}")
        print(f"  address: {result.get('address') or '(not found)'}")
        print(f"  phone_number: {result.get('phone_number') or '(not found)'}")
        print()
        print(f"Confidence: {result.get('confidence', 0):.0%}")
        print(f"Fields found: {result.get('fields_found', [])}")
        print()
        print("Sources:")
        sources = result.get("sources", {})
        for field, source in sources.items():
            print(f"  {field}: {source}")
        print()
        print("Field confidence:")
        field_conf = result.get("field_confidence", {})
        for field, conf in field_conf.items():
            print(f"  {field}: {conf:.0%}")
        print()
        print("Reasoning:")
        reasoning = result.get("reasoning", "")
        if reasoning:
            # Print reasoning in chunks if long
            if len(reasoning) > 500:
                print(f"  {reasoning[:500]}...")
                print(f"  ... (truncated, full length: {len(reasoning)} chars)")
            else:
                # Print with line breaks
                for line in reasoning.split("\n"):
                    print(f"  {line}")
    else:
        print("❌ NO METADATA FOUND")
        print(f"Error: {result.get('reasoning', 'Unknown error')}")

    print()
    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print()

    try:
        current = dynamo.get_receipt_metadata(image_id, receipt_id)
        if current:
            print("BEFORE (Current):")
            print(f"  merchant_name: {current.merchant_name}")
            print(f"  place_id: {current.place_id}")
            print(f"  address: {current.address}")
            print(f"  phone_number: {current.phone_number}")
            print()

            if result.get("found"):
                print("AFTER (Agent Found):")
                print(f"  merchant_name: {result.get('merchant_name') or '(not found)'}")
                print(f"  place_id: {result.get('place_id') or '(not found)'}")
                print(f"  address: {result.get('address') or '(not found)'}")
                print(f"  phone_number: {result.get('phone_number') or '(not found)'}")
                print()

                print("CHANGES NEEDED:")
                changes = []
                if result.get("merchant_name") and current.merchant_name != result.get("merchant_name"):
                    changes.append(
                        f"merchant_name: \"{current.merchant_name}\" → \"{result.get('merchant_name')}\""
                    )
                if result.get("place_id") and current.place_id != result.get("place_id"):
                    changes.append(
                        f"place_id: \"{current.place_id}\" → \"{result.get('place_id')}\""
                    )
                if result.get("address") and current.address != result.get("address"):
                    changes.append(
                        f"address: \"{current.address}\" → \"{result.get('address')}\""
                    )
                if result.get("phone_number") and current.phone_number != result.get("phone_number"):
                    changes.append(
                        f"phone_number: \"{current.phone_number}\" → \"{result.get('phone_number')}\""
                    )

                if changes:
                    for change in changes:
                        print(f"  • {change}")
                else:
                    print("  (No changes needed)")
    except Exception as e:
        print(f"Error comparing: {e}")
        import traceback
        traceback.print_exc()


async def apply_metadata_update(dynamo, image_id, receipt_id, result):
    """Apply the metadata update to DynamoDB."""
    if not result.get("found"):
        print("❌ No metadata found - cannot apply update")
        return False

    try:
        # Get existing metadata
        try:
            metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
            metadata_exists = True
        except Exception as e:
            error_str = str(e)
            if "does not exist" in error_str or "not found" in error_str.lower():
                metadata_exists = False
            else:
                raise

        if not metadata_exists:
            # Create new metadata
            from datetime import datetime, timezone
            from receipt_dynamo.constants import ValidationMethod, MerchantValidationStatus
            from receipt_dynamo.entities import ReceiptMetadata

            metadata = ReceiptMetadata(
                image_id=image_id,
                receipt_id=receipt_id,
                place_id=result.get("place_id") or "",
                merchant_name=result.get("merchant_name") or "",
                merchant_category="",
                address=result.get("address") or "",
                phone_number=result.get("phone_number") or "",
                matched_fields=result.get("fields_found", []),
                validated_by=ValidationMethod.TEXT_SEARCH.value,
                timestamp=datetime.now(timezone.utc),
                reasoning=result.get("reasoning", "Created by receipt_metadata_finder"),
                validation_status=MerchantValidationStatus.MATCHED.value if result.get("place_id") else MerchantValidationStatus.UNSURE.value,
            )

            dynamo.add_receipt_metadata(metadata)
            print("✅ Created new metadata record")
            return True
        else:
            # Update existing metadata
            updated_fields = []

            if result.get("place_id") and metadata.place_id != result.get("place_id"):
                metadata.place_id = result.get("place_id")
                updated_fields.append("place_id")

            if result.get("merchant_name") and metadata.merchant_name != result.get("merchant_name"):
                metadata.merchant_name = result.get("merchant_name")
                updated_fields.append("merchant_name")

            if result.get("address") and metadata.address != result.get("address"):
                metadata.address = result.get("address")
                updated_fields.append("address")

            if result.get("phone_number") and metadata.phone_number != result.get("phone_number"):
                metadata.phone_number = result.get("phone_number")
                updated_fields.append("phone_number")

            # Update matched_fields to include all fields we found
            fields_found = result.get("fields_found", [])
            # Map field names to matched_fields format (name, phone, address, place_id)
            field_mapping = {
                "merchant_name": "name",
                "phone_number": "phone",
                "address": "address",
                "place_id": "place_id",
            }
            new_matched_fields = list(metadata.matched_fields) if metadata.matched_fields else []
            for field in fields_found:
                mapped_field = field_mapping.get(field, field)
                if mapped_field not in new_matched_fields:
                    new_matched_fields.append(mapped_field)

            if set(new_matched_fields) != set(metadata.matched_fields or []):
                metadata.matched_fields = new_matched_fields
                updated_fields.append("matched_fields")

            # Update validation_status based on whether we found a place_id
            # Note: ReceiptMetadata.__post_init__ will recalculate this, but we set it explicitly
            # to ensure it's correct based on our confidence and place_id
            from receipt_dynamo.constants import MerchantValidationStatus
            # Confidence from workflow is already a decimal (0.0 to 1.0)
            confidence = result.get("confidence", 0.0)
            has_place_id = bool(result.get("place_id"))

            # Determine appropriate validation status
            if has_place_id and confidence >= 0.8:  # 80% confidence threshold
                new_status = MerchantValidationStatus.MATCHED.value
            elif has_place_id and confidence >= 0.5:  # 50-80% confidence
                new_status = MerchantValidationStatus.UNSURE.value
            elif not has_place_id:
                new_status = MerchantValidationStatus.NO_MATCH.value
            else:
                new_status = MerchantValidationStatus.UNSURE.value

            old_status = metadata.validation_status
            if old_status != new_status:
                metadata.validation_status = new_status
                updated_fields.append("validation_status")
                print(f"  Updating validation_status: {old_status} → {new_status} (confidence={confidence:.0%}, has_place_id={has_place_id})")
            else:
                print(f"  validation_status already correct: {old_status} (confidence={confidence:.0%}, has_place_id={has_place_id})")

            if updated_fields:
                dynamo.update_receipt_metadata(metadata)
                print(f"✅ Updated metadata: {', '.join(updated_fields)}")
                return True
            else:
                print("ℹ️  No changes needed - metadata already matches")
                return False

    except Exception as e:
        print(f"❌ Error applying update: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_receipt_with_update():
    """Test the receipt metadata finder and apply the update."""
    from receipt_dynamo import DynamoClient
    from receipt_places import PlacesClient, PlacesConfig
    from receipt_agent.tools.receipt_metadata_finder import ReceiptMetadataFinder
    from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
    from receipt_agent.config.settings import get_settings
    from receipt_agent.graph.receipt_metadata_finder_workflow import (
        create_receipt_metadata_finder_graph,
        run_receipt_metadata_finder,
    )

    # Setup clients
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )

    places_config = PlacesConfig(
        api_key=os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"),
        cache_enabled=True,
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )
    places = PlacesClient(config=places_config)

    settings = get_settings()
    chroma_client = create_chroma_client(mode="read", settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    image_id = "cee0fbe1-d84a-4f69-9af1-7166226e8b88"
    receipt_id = 2

    print("=" * 70)
    print("TESTING RECEIPT METADATA FINDER - WITH UPDATE")
    print("=" * 70)
    print(f"Receipt: {image_id}#{receipt_id}")
    print()

    # Get current metadata
    print("📋 CURRENT METADATA:")
    try:
        current = dynamo.get_receipt_metadata(image_id, receipt_id)
        if current:
            print(f"  merchant_name: {current.merchant_name}")
            print(f"  place_id: {current.place_id}")
            print(f"  address: {current.address}")
            print(f"  phone_number: {current.phone_number}")
            print(f"  validation_status: {current.validation_status}")
        else:
            print("  No metadata found")
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("🤖 Running Receipt Metadata Finder agent...")
    print()

    # Create graph
    graph, state_holder = create_receipt_metadata_finder_graph(
        dynamo_client=dynamo,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places,
        settings=settings,
    )

    # Run agent
    result = await run_receipt_metadata_finder(
        graph=graph,
        state_holder=state_holder,
        image_id=image_id,
        receipt_id=receipt_id,
    )

    print()
    print("=" * 70)
    print("AGENT RESULTS")
    print("=" * 70)
    print()

    if result.get("found"):
        print("✅ METADATA FOUND:")
        print(f"  place_id: {result.get('place_id') or '(not found)'}")
        print(f"  merchant_name: {result.get('merchant_name') or '(not found)'}")
        print(f"  address: {result.get('address') or '(not found)'}")
        print(f"  phone_number: {result.get('phone_number') or '(not found)'}")
        print()
        print(f"Confidence: {result.get('confidence', 0):.0%}")
        print()

        # Apply the update
        print()
        print("=" * 70)
        print("APPLYING UPDATE TO DYNAMODB")
        print("=" * 70)
        print()

        success = await apply_metadata_update(dynamo, image_id, receipt_id, result)

        if success:
            print()
            # Small delay to ensure DynamoDB consistency
            import asyncio
            await asyncio.sleep(0.5)

            print("=" * 70)
            print("VERIFICATION - UPDATED METADATA")
            print("=" * 70)
            print()

            # Verify the update
            updated = dynamo.get_receipt_metadata(image_id, receipt_id)
            if updated:
                print("✅ Updated metadata:")
                print(f"  merchant_name: {updated.merchant_name}")
                print(f"  place_id: {updated.place_id}")
                print(f"  address: {updated.address}")
                print(f"  phone_number: {updated.phone_number}")
                print(f"  validation_status: {updated.validation_status}")

                # Check if validation_status was actually updated
                expected_status = "MATCHED" if result.get("place_id") and result.get("confidence", 0.0) >= 0.8 else "UNSURE"
                if updated.validation_status == expected_status:
                    print(f"  ✅ validation_status correctly set to {expected_status}")
                else:
                    print(f"  ⚠️  validation_status is {updated.validation_status}, expected {expected_status}")
    else:
        print("❌ NO METADATA FOUND")
        print(f"Error: {result.get('reasoning', 'Unknown error')}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        setup_environment()
        asyncio.run(test_receipt_with_update())
    else:
        setup_environment()
        asyncio.run(test_receipt())

