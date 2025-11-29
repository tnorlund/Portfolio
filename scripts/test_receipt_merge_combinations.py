#!/usr/bin/env python3
"""
Test script to try different receipt merge combinations.

This script directly tests merging receipts 1+2 and 2+3 for a given image
and shows the results without using the agent.
"""

import argparse
import json
import logging
import os
import sys

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


def get_image_lines(dynamo_client, image_id):
    """Get all OCR lines from the image."""
    image_lines = dynamo_client.list_lines_from_image(image_id)
    lines = []
    for line in image_lines:
        centroid = line.calculate_centroid()
        lines.append({
            "line_id": line.line_id,
            "text": line.text,
            "centroid_x": centroid[0],
            "centroid_y": centroid[1],
            "top_left": {
                "x": line.top_left.get("x", 0),
                "y": line.top_left.get("y", 0),
            },
            "top_right": {
                "x": line.top_right.get("x", 0),
                "y": line.top_right.get("y", 0),
            },
            "bottom_left": {
                "x": line.bottom_left.get("x", 0),
                "y": line.bottom_left.get("y", 0),
            },
            "bottom_right": {
                "x": line.bottom_right.get("x", 0),
                "y": line.bottom_right.get("y", 0),
            },
        })
    # Sort by Y position (top to bottom), then X (left to right)
    lines.sort(key=lambda l: (l["centroid_y"], l["centroid_x"]))
    return lines


def get_current_receipts(dynamo_client, image_id):
    """Get current receipt groupings."""
    receipts = []

    # Try get_receipts_from_image first
    try:
        receipt_objects = dynamo_client.get_receipts_from_image(image_id)
        print(f"   get_receipts_from_image found {len(receipt_objects)} receipts")

        for receipt in receipt_objects:
            # Get lines for this receipt
            receipt_lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt.receipt_id)
            line_ids = sorted([rl.line_id for rl in receipt_lines])

            # Try to get metadata
            metadata = None
            try:
                receipt_metadata = dynamo_client.get_receipt_metadata(
                    image_id, receipt.receipt_id
                )
                if receipt_metadata:
                    metadata = {
                        "merchant_name": receipt_metadata.merchant_name,
                        "address": receipt_metadata.address,
                        "phone": receipt_metadata.phone_number,
                        "place_id": receipt_metadata.place_id,
                    }
            except Exception:
                pass  # Metadata might not exist

            receipts.append({
                "receipt_id": receipt.receipt_id,
                "line_ids": line_ids,
                "line_count": len(line_ids),
                "metadata": metadata,
            })
    except Exception as e:
        print(f"   Error with get_receipts_from_image: {e}")
        # Fallback to get_image_details
        try:
            image_details = dynamo_client.get_image_details(image_id)
            if image_details and hasattr(image_details, 'receipts'):
                print(f"   Image details has {len(image_details.receipts)} receipts")

                for receipt in image_details.receipts:
                    # Get lines for this receipt
                    receipt_lines = [
                        rl for rl in image_details.receipt_lines
                        if rl.receipt_id == receipt.receipt_id
                    ]
                    line_ids = sorted([rl.line_id for rl in receipt_lines])

                    # Try to get metadata
                    metadata = None
                    try:
                        receipt_metadata = dynamo_client.get_receipt_metadata(
                            image_id, receipt.receipt_id
                        )
                        if receipt_metadata:
                            metadata = {
                                "merchant_name": receipt_metadata.merchant_name,
                                "address": receipt_metadata.address,
                                "phone": receipt_metadata.phone_number,
                                "place_id": receipt_metadata.place_id,
                            }
                    except Exception:
                        pass  # Metadata might not exist

                    receipts.append({
                        "receipt_id": receipt.receipt_id,
                        "line_ids": line_ids,
                        "line_count": len(line_ids),
                        "metadata": metadata,
                    })
        except Exception as e2:
            print(f"   Error with get_image_details: {e2}")
            return []

    # Sort by receipt_id
    receipts.sort(key=lambda r: r["receipt_id"])
    return receipts


def try_merge(dynamo_client, image_id, receipt_1, receipt_2, image_lines, image_width=None, image_height=None):
    """Try merging two receipts and evaluate."""
    # Merge line IDs
    merged_line_ids = sorted(list(set(receipt_1["line_ids"] + receipt_2["line_ids"])))

    # Get lines for merged receipt
    # If we have receipt entities, we should transform receipt coordinates to image coordinates
    # Otherwise, use image lines directly
    if image_width and image_height:
        # Get receipt entities to transform coordinates
        try:
            from receipt_agent.utils.receipt_coordinates import (
                get_receipt_lines_in_image_coords,
                sort_lines_by_reading_order,
            )

            # Get receipt entities
            receipt_obj_1 = None
            receipt_obj_2 = None
            image_details = dynamo_client.get_image_details(image_id)
            for receipt in image_details.receipts:
                if receipt.receipt_id == receipt_1["receipt_id"]:
                    receipt_obj_1 = receipt
                if receipt.receipt_id == receipt_2["receipt_id"]:
                    receipt_obj_2 = receipt

            if receipt_obj_1 and receipt_obj_2:
                # Get receipt lines
                receipt_lines_1 = [
                    rl for rl in image_details.receipt_lines
                    if rl.receipt_id == receipt_1["receipt_id"]
                ]
                receipt_lines_2 = [
                    rl for rl in image_details.receipt_lines
                    if rl.receipt_id == receipt_2["receipt_id"]
                ]

                # Transform to image coordinates
                transformed_1 = get_receipt_lines_in_image_coords(
                    receipt_lines_1, receipt_obj_1, image_width, image_height
                )
                transformed_2 = get_receipt_lines_in_image_coords(
                    receipt_lines_2, receipt_obj_2, image_width, image_height
                )

                # Combine and sort by reading order
                all_transformed = transformed_1 + transformed_2
                merged_lines = sort_lines_by_reading_order(all_transformed)
            else:
                # Fallback to image lines
                line_map = {line["line_id"]: line for line in image_lines}
                merged_lines = [line_map[line_id] for line_id in merged_line_ids if line_id in line_map]
        except Exception as e:
            print(f"   Warning: Could not transform coordinates: {e}")
            # Fallback to image lines
            line_map = {line["line_id"]: line for line in image_lines}
            merged_lines = [line_map[line_id] for line_id in merged_line_ids if line_id in line_map]
    else:
        # Use image lines directly
        line_map = {line["line_id"]: line for line in image_lines}
        merged_lines = [line_map[line_id] for line_id in merged_line_ids if line_id in line_map]

    if not merged_lines:
        return {"error": "No valid lines found in merged receipt"}

    # Build merged text
    merged_text = " ".join(line["text"] for line in merged_lines)
    text_lower = merged_text.lower()

    # Check for receipt elements
    has_merchant = any(
        word in text_lower for word in ["market", "store", "restaurant", "cafe", "shop", "inc", "llc", "corp", "farmers", "provisions"]
    ) or len([w for w in merged_lines[0]["text"].split() if len(w) > 3]) > 0

    has_address = any(
        word in text_lower for word in ["street", "st", "avenue", "ave", "road", "rd", "blvd", "boulevard", "drive", "dr", "way", "lane", "ln", "westlake", "thousand", "oaks"]
    ) or any(char.isdigit() for char in merged_text)

    has_phone = any(
        char in merged_text for char in ["(", ")", "-"]
    ) or any(len(part) == 10 and part.isdigit() for part in merged_text.replace("(", "").replace(")", "").replace("-", "").split())

    has_total = any(
        word in text_lower for word in ["total", "amount", "sum", "$", "subtotal"]
    ) or any(char == "$" for char in merged_text)

    # Calculate coherence score
    coherence = 0.0
    if has_merchant:
        coherence += 0.3
    if has_address:
        coherence += 0.3
    if has_phone:
        coherence += 0.2
    if has_total:
        coherence += 0.2

    # Check for duplicate merchant names (bad sign)
    merchant_names_1 = receipt_1.get("metadata", {}).get("merchant_name", "")
    merchant_names_2 = receipt_2.get("metadata", {}).get("merchant_name", "")
    has_duplicate_merchants = (
        merchant_names_1 and merchant_names_2 and
        merchant_names_1.lower() != merchant_names_2.lower()
    )

    # Check for duplicate addresses (bad sign)
    address_1 = receipt_1.get("metadata", {}).get("address", "")
    address_2 = receipt_2.get("metadata", {}).get("address", "")
    has_duplicate_addresses = (
        address_1 and address_2 and
        address_1.lower() != address_2.lower()
    )

    # Check for duplicate phones (bad sign)
    phone_1 = receipt_1.get("metadata", {}).get("phone", "")
    phone_2 = receipt_2.get("metadata", {}).get("phone", "")
    has_duplicate_phones = (
        phone_1 and phone_2 and
        phone_1.replace("-", "").replace("(", "").replace(")", "").replace(" ", "") !=
        phone_2.replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
    )

    # Analyze spatial layout
    if merged_lines:
        y_positions = [line["centroid_y"] for line in merged_lines]
        y_range = max(y_positions) - min(y_positions) if y_positions else 0
        y_positions.sort()

        # Check for large gaps that might indicate separate receipts
        large_gaps = []
        for i in range(len(y_positions) - 1):
            gap = y_positions[i + 1] - y_positions[i]
            if gap > 0.1:  # Large gap might indicate separate receipts
                large_gaps.append({
                    "gap_size": round(gap, 3),
                    "between_y": f"{y_positions[i]:.3f} and {y_positions[i+1]:.3f}",
                })
    else:
        y_range = 0
        large_gaps = []

    # Determine if merge makes sense
    issues = []
    if has_duplicate_merchants:
        issues.append(f"Different merchants: '{merchant_names_1}' vs '{merchant_names_2}'")
        coherence *= 0.5  # Penalize heavily
    if has_duplicate_addresses:
        issues.append(f"Different addresses: '{address_1}' vs '{address_2}'")
        coherence *= 0.7
    if has_duplicate_phones:
        issues.append(f"Different phone numbers: '{phone_1}' vs '{phone_2}'")
        coherence *= 0.7

    if len(merged_lines) < 3:
        issues.append(f"Very few lines ({len(merged_lines)})")
    if not has_merchant:
        issues.append("No clear merchant name")
    if not has_address:
        issues.append("No clear address")
    if not has_phone:
        issues.append("No clear phone number")
    if not has_total:
        issues.append("No clear total")

    # Check spatial layout
    if large_gaps:
        issues.append(f"Large spatial gaps detected ({len(large_gaps)} gaps) - might be separate receipts")
        coherence *= 0.8  # Penalize for spatial gaps
    if y_range > 0.5:
        issues.append(f"Very large vertical spread ({y_range:.3f}) - lines might not belong together")
        coherence *= 0.9

    makes_sense = (
        coherence > 0.6 and
        not has_duplicate_merchants and
        not has_duplicate_addresses and
        not has_duplicate_phones and
        len(merged_lines) >= 3
    )

    return {
        "receipt_id_1": receipt_1["receipt_id"],
        "receipt_id_2": receipt_2["receipt_id"],
        "merged_line_ids": merged_line_ids,
        "merged_line_count": len(merged_lines),
        "merged_text": merged_text[:500] + "..." if len(merged_text) > 500 else merged_text,
        "coherence_score": round(coherence, 3),
        "has_merchant_name": has_merchant,
        "has_address": has_address,
        "has_phone": has_phone,
        "has_total": has_total,
        "has_duplicate_merchants": has_duplicate_merchants,
        "has_duplicate_addresses": has_duplicate_addresses,
        "has_duplicate_phones": has_duplicate_phones,
        "spatial_analysis": {
            "y_range": round(y_range, 3),
            "large_gaps": large_gaps,
            "gap_count": len(large_gaps),
        },
        "makes_sense": makes_sense,
        "issues": issues,
        "recommendation": "✅ Merge makes sense" if makes_sense else "❌ Merge does NOT make sense - these appear to be different receipts",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Test receipt merge combinations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--image-id", required=True, help="Image ID to analyze")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    args = parser.parse_args()

    print("=" * 70)
    print("RECEIPT MERGE COMBINATION TESTER")
    print("=" * 70)
    print()

    # Setup
    env, secrets = setup_environment()

    # Create DynamoDB client
    from receipt_dynamo import DynamoClient
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )
    print("✅ DynamoDB client created")
    print()

    # Get current receipts
    print(f"📋 Loading current receipts for image {args.image_id}...")
    receipts = get_current_receipts(dynamo, args.image_id)
    print(f"   Found {len(receipts)} receipts:")
    for receipt in receipts:
        print(f"   - Receipt {receipt['receipt_id']}: {receipt['line_count']} lines")
        if receipt.get("metadata"):
            meta = receipt["metadata"]
            print(f"     Merchant: {meta.get('merchant_name', 'N/A')}")
            print(f"     Address: {meta.get('address', 'N/A')[:50]}...")
            print(f"     Phone: {meta.get('phone', 'N/A')}")
    print()

    if len(receipts) < 2:
        print("❌ Need at least 2 receipts to test merges")
        return

    # Get image dimensions
    print("📐 Getting image dimensions...")
    image_width = None
    image_height = None
    try:
        image_details = dynamo.get_image_details(args.image_id)
        if image_details.images:
            image = image_details.images[0]
            image_width = image.width
            image_height = image.height
            print(f"   Image dimensions: {image_width}x{image_height}")
        else:
            print("   ⚠️  No image entity found, cannot transform coordinates")
    except Exception as e:
        print(f"   ⚠️  Could not get image dimensions: {e}")

    # Get image lines
    print("📄 Loading image lines...")
    image_lines = get_image_lines(dynamo, args.image_id)
    print(f"   Found {len(image_lines)} total lines")
    print()

    # Try merge 1+2
    print("=" * 70)
    print("TESTING MERGE: Receipt 1 + Receipt 2")
    print("=" * 70)
    merge_1_2 = try_merge(dynamo, args.image_id, receipts[0], receipts[1], image_lines, image_width, image_height)
    print(f"Coherence Score: {merge_1_2.get('coherence_score', 0):.3f}")
    print(f"Makes Sense: {merge_1_2.get('makes_sense', False)}")
    print(f"Recommendation: {merge_1_2.get('recommendation', 'N/A')}")
    print()
    print("Details:")
    print(f"  - Merged {merge_1_2.get('merged_line_count', 0)} lines")
    print(f"  - Has merchant: {merge_1_2.get('has_merchant_name', False)}")
    print(f"  - Has address: {merge_1_2.get('has_address', False)}")
    print(f"  - Has phone: {merge_1_2.get('has_phone', False)}")
    print(f"  - Has total: {merge_1_2.get('has_total', False)}")
    print(f"  - Duplicate merchants: {merge_1_2.get('has_duplicate_merchants', False)}")
    print(f"  - Duplicate addresses: {merge_1_2.get('has_duplicate_addresses', False)}")
    print(f"  - Duplicate phones: {merge_1_2.get('has_duplicate_phones', False)}")
    spatial = merge_1_2.get('spatial_analysis', {})
    print(f"  - Y range: {spatial.get('y_range', 0):.3f}")
    print(f"  - Large gaps: {spatial.get('gap_count', 0)}")
    if merge_1_2.get('issues'):
        print("  Issues:")
        for issue in merge_1_2['issues']:
            print(f"    - {issue}")
    print()
    print("Merged text preview:")
    print(merge_1_2.get('merged_text', '')[:300])
    print()

    # Try merge 2+3 if we have 3 receipts
    if len(receipts) >= 3:
        print("=" * 70)
        print("TESTING MERGE: Receipt 2 + Receipt 3")
        print("=" * 70)
        merge_2_3 = try_merge(dynamo, args.image_id, receipts[1], receipts[2], image_lines, image_width, image_height)
        print(f"Coherence Score: {merge_2_3.get('coherence_score', 0):.3f}")
        print(f"Makes Sense: {merge_2_3.get('makes_sense', False)}")
        print(f"Recommendation: {merge_2_3.get('recommendation', 'N/A')}")
        print()
        print("Details:")
        print(f"  - Merged {merge_2_3.get('merged_line_count', 0)} lines")
        print(f"  - Has merchant: {merge_2_3.get('has_merchant_name', False)}")
        print(f"  - Has address: {merge_2_3.get('has_address', False)}")
        print(f"  - Has phone: {merge_2_3.get('has_phone', False)}")
        print(f"  - Has total: {merge_2_3.get('has_total', False)}")
        print(f"  - Duplicate merchants: {merge_2_3.get('has_duplicate_merchants', False)}")
        print(f"  - Duplicate addresses: {merge_2_3.get('has_duplicate_addresses', False)}")
        print(f"  - Duplicate phones: {merge_2_3.get('has_duplicate_phones', False)}")
        spatial = merge_2_3.get('spatial_analysis', {})
        print(f"  - Y range: {spatial.get('y_range', 0):.3f}")
        print(f"  - Large gaps: {spatial.get('gap_count', 0)}")
        if merge_2_3.get('issues'):
            print("  Issues:")
            for issue in merge_2_3['issues']:
                print(f"    - {issue}")
        print()
        print("Merged text preview:")
        print(merge_2_3.get('merged_text', '')[:300])
        print()

        # Compare results
        print("=" * 70)
        print("COMPARISON")
        print("=" * 70)
        print()
        print("Merge 1+2:")
        print(f"  Coherence: {merge_1_2.get('coherence_score', 0):.3f}")
        print(f"  Makes Sense: {merge_1_2.get('makes_sense', False)}")
        print()
        print("Merge 2+3:")
        print(f"  Coherence: {merge_2_3.get('coherence_score', 0):.3f}")
        print(f"  Makes Sense: {merge_2_3.get('makes_sense', False)}")
        print()

        if merge_1_2.get('makes_sense') and not merge_2_3.get('makes_sense'):
            print("✅ RECOMMENDATION: Merge receipts 1+2")
        elif merge_2_3.get('makes_sense') and not merge_1_2.get('makes_sense'):
            print("✅ RECOMMENDATION: Merge receipts 2+3")
            print("   (This matches the expected result: receipts 2 and 3 should be combined)")
        elif merge_1_2.get('makes_sense') and merge_2_3.get('makes_sense'):
            # Both make sense - compare coherence
            if merge_1_2.get('coherence_score', 0) > merge_2_3.get('coherence_score', 0):
                print("✅ RECOMMENDATION: Merge receipts 1+2 (higher coherence)")
            else:
                print("✅ RECOMMENDATION: Merge receipts 2+3 (higher coherence)")
                print("   (This matches the expected result: receipts 2 and 3 should be combined)")
        else:
            print("❌ RECOMMENDATION: Neither merge makes sense - keep as 3 separate receipts")
            print("   ⚠️  Expected: receipts 2 and 3 should be merged, but analysis suggests otherwise")
    else:
        print("⚠️  Only 2 receipts found - cannot test merge 2+3")

    # Save results
    if args.output:
        results = {
            "image_id": args.image_id,
            "current_receipts": receipts,
            "merge_1_2": merge_1_2,
        }
        if len(receipts) >= 3:
            results["merge_2_3"] = merge_2_3

        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print()
        print(f"💾 Saved results to {args.output}")


if __name__ == "__main__":
    main()

