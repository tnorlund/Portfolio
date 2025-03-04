"""
Refine OCR Module

This module provides functionality to refine OCR results by:
1. Processing new OCR results for receipts
2. Mapping tags from old OCR results to new OCR results
3. Preserving human-validated tags

The approach uses a delete-and-recreate strategy where all old OCR entities 
(lines, words, letters) are deleted and replaced with new ones, while 
maintaining tag information.
"""

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._ocr import apple_vision_ocr
from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptLetter
import boto3
import tempfile
from pathlib import Path


def is_point_in_quadrilateral(point_x, point_y, corners):
    """
    Check if a point is inside a quadrilateral defined by four corners.
    Uses the ray casting algorithm.

    Args:
        point_x (float): X coordinate of the point to check
        point_y (float): Y coordinate of the point to check
        corners (dict): Dictionary with top_left, top_right, bottom_right, bottom_left coordinates

    Returns:
        bool: True if the point is inside the quadrilateral, False otherwise
    """
    # Extract the corners
    vertices = [
        (corners["top_left"][0], corners["top_left"][1]),
        (corners["top_right"][0], corners["top_right"][1]),
        (corners["bottom_right"][0], corners["bottom_right"][1]),
        (corners["bottom_left"][0], corners["bottom_left"][1]),
    ]

    # Ray casting algorithm
    inside = False
    j = len(vertices) - 1

    for i in range(len(vertices)):
        xi, yi = vertices[i]
        xj, yj = vertices[j]

        # Check if the point is on an edge
        # Calculate the distance from point to line segment
        if (
            yi == yj
            and yi == point_y
            and min(xi, xj) <= point_x <= max(xi, xj)
        ):
            return True  # Point is on a horizontal edge

        if (
            xi == xj
            and xi == point_x
            and min(yi, yj) <= point_y <= max(yi, yj)
        ):
            return True  # Point is on a vertical edge

        # Check if ray from point crosses this edge
        intersect = ((yi > point_y) != (yj > point_y)) and (
            point_x < (xj - xi) * (point_y - yi) / (yj - yi) + xi
        )

        if intersect:
            inside = not inside

        j = i

    return inside


def refine_receipt_ocr(
    image_id, receipt_id, env="dev", debug=False, commit_changes=False
):
    """
    Refine OCR results for a specific receipt by running a new OCR process and
    transferring tags from old OCR results.

    Args:
        image_id (str): The image ID
        receipt_id (int): The receipt ID
        env (dict or str, optional): Environment configuration from load_env() or environment name. Default is "dev".
        debug (bool, optional): Whether to print debug information
        commit_changes (bool, optional): Whether to commit the changes to the database

    Returns:
        dict: Results of the refinement process
    """
    # Initialize environment and clients
    if isinstance(env, str):
        env = load_env(env)
    elif env is None:
        env = load_env("dev")

    # Create clients from environment
    client = DynamoClient(env["dynamodb_table_name"])
    s3_client = boto3.client("s3")

    if debug:
        print(f"\n--- Processing receipt {image_id}_{receipt_id} ---")
        print(f"Using DynamoDB table: {env['dynamodb_table_name']}")

    # Get the old receipt details
    try:
        (
            old_receipt,
            old_receipt_lines,
            old_receipt_words,
            old_receipt_letters,
            old_receipt_word_tags,
            old_gpt_validations,
            old_gpt_initial_taggings,
        ) = client.getReceiptDetails(image_id, receipt_id)
    except Exception as e:
        if debug:
            print(f"Error getting receipt details: {e}")
        return {"success": False, "error": f"Failed to get receipt details: {str(e)}"}

    if debug:
        print("\nOld receipt details:")
        print(f"Lines: {len(old_receipt_lines)}")
        print(f"Words: {len(old_receipt_words)}")
        print(f"Letters: {len(old_receipt_letters)}")
        print(f"Word tags: {len(old_receipt_word_tags)}")

    # Use a temporary directory to download the raw receipt image
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        image_path = temp_dir / f"{image_id}_{receipt_id}.png"
        
        try:
            # Download the raw receipt image
            if debug:
                print(f"\nDownloading image from s3://{old_receipt.raw_s3_bucket}/{old_receipt.raw_s3_key}")
            s3_client.download_file(
                Bucket=old_receipt.raw_s3_bucket,
                Key=old_receipt.raw_s3_key,
                Filename=str(image_path)
            )
        except Exception as e:
            if debug:
                print(f"Error downloading image: {e}")
            return {"success": False, "error": f"Failed to download image: {str(e)}"}

        # Verify the image exists and is readable
        if not image_path.exists():
            error_msg = f"Downloaded image not found at {image_path}"
            if debug:
                print(error_msg)
            return {"success": False, "error": error_msg}

        # Run the OCR on the receipt image
        if debug:
            print("\nRunning OCR on image...")
        
        try:
            ocr_result = apple_vision_ocr([str(image_path)])
            if not ocr_result:
                error_msg = f"OCR process failed to produce results for {image_id}_{receipt_id}.png"
                if debug:
                    print(error_msg)
                return {"success": False, "error": error_msg}
        except Exception as e:
            if debug:
                print(f"Error during OCR process: {e}")
                import traceback
                traceback.print_exc()
            return {
                "success": False,
                "error": f"OCR process failed with error: {str(e)}"
            }

        # Get the results for this specific image
        first_image_id = next(iter(ocr_result.keys()))
        new_receipt_lines, new_receipt_words, new_receipt_letters = ocr_result[first_image_id]

        if debug:
            print("\nOCR Results:")
            print(f"Lines detected: {len(new_receipt_lines)}")
            print(f"Words detected: {len(new_receipt_words)}")
            print(f"Letters detected: {len(new_receipt_letters)}")

        # Validate OCR results
        if not new_receipt_lines or not new_receipt_words or not new_receipt_letters:
            error_msg = "OCR process produced empty results"
            if debug:
                print(f"\nError: {error_msg}")
                print(f"Lines: {len(new_receipt_lines)}")
                print(f"Words: {len(new_receipt_words)}")
                print(f"Letters: {len(new_receipt_letters)}")
            return {
                "success": False,
                "error": error_msg,
                "ocr_result": {
                    "lines": new_receipt_lines,
                    "words": new_receipt_words,
                    "letters": new_receipt_letters
                }
            }

        try:
            # Process the OCR results using the delete-and-recreate approach
            result = process_ocr_results(
                image_id,
                receipt_id,
                old_receipt_lines,
                old_receipt_words,
                old_receipt_letters,
                old_receipt_word_tags,
                new_receipt_lines,
                new_receipt_words,
                new_receipt_letters,
                debug=debug,
            )

            # Commit changes to the database if requested
            if commit_changes:
                commit_result = commit_ocr_changes(client, result, debug=debug)
                result["commit_result"] = commit_result

            return result

        except Exception as e:
            if debug:
                print(f"Error during processing: {e}")
                import traceback
                traceback.print_exc()
            return {"success": False, "error": str(e)}


def commit_ocr_changes(client, results, debug=False):
    """
    Commit OCR changes to the database by deleting old entities and creating new ones.
    Uses batch operations to minimize DynamoDB transactions.

    Args:
        client: DynamoDB client instance
        results: Results dictionary from process_ocr_results
        debug: Whether to print debug information

    Returns:
        Dictionary with counts of deleted and created entities and success status
    """
    if debug:
        print(
            "\n--- Committing changes to database using batch operations ---"
        )

    commit_results = {
        "deleted": {"tags": 0, "letters": 0, "words": 0, "lines": 0},
        "created": {"lines": 0, "words": 0, "letters": 0, "tags": 0},
        "success": True,
    }

    try:
        # Get entities to delete and create from the results dictionary
        entities_to_delete = results["entities_to_delete"]
        entities_to_create = results["entities_to_create"]

        # Validate that we have all necessary entities to create
        if not all(entities_to_create[entity_type] for entity_type in ["lines", "words", "letters"]):
            raise ValueError("Missing required entities to create. Cannot proceed with partial data.")

        # STEP 1: Delete old entities in reverse hierarchy order (tags -> letters -> words -> lines)
        try:
            # Delete tags (must be first due to foreign key constraints)
            if entities_to_delete["tags"]:
                if debug:
                    print(
                        f"Deleting {len(entities_to_delete['tags'])} tags in batch..."
                    )
                client.deleteReceiptWordTags(entities_to_delete["tags"])
                commit_results["deleted"]["tags"] = len(entities_to_delete["tags"])

            # Delete letters
            if entities_to_delete["letters"]:
                if debug:
                    print(
                        f"Deleting {len(entities_to_delete['letters'])} letters in batch..."
                    )
                client.deleteReceiptLetters(entities_to_delete["letters"])
                commit_results["deleted"]["letters"] = len(
                    entities_to_delete["letters"]
                )

            # Delete words
            if entities_to_delete["words"]:
                if debug:
                    print(
                        f"Deleting {len(entities_to_delete['words'])} words in batch..."
                    )
                client.deleteReceiptWords(entities_to_delete["words"])
                commit_results["deleted"]["words"] = len(
                    entities_to_delete["words"]
                )

            # Delete lines
            if entities_to_delete["lines"]:
                if debug:
                    print(
                        f"Deleting {len(entities_to_delete['lines'])} lines in batch..."
                    )
                client.deleteReceiptLines(entities_to_delete["lines"])
                commit_results["deleted"]["lines"] = len(
                    entities_to_delete["lines"]
                )

        except Exception as delete_error:
            if debug:
                print(f"Error during entity deletion: {delete_error}")
                import traceback
                traceback.print_exc()
            raise delete_error

        # STEP 2: Create new entities in hierarchical order (lines -> words -> letters -> tags)
        try:
            # First, verify all entities have required fields
            for line in entities_to_create["lines"]:
                if not all(hasattr(line, attr) for attr in ["image_id", "line_id", "receipt_id"]):
                    raise ValueError(f"Line missing required attributes: {vars(line)}")

            for word in entities_to_create["words"]:
                if not all(hasattr(word, attr) for attr in ["image_id", "line_id", "word_id", "receipt_id"]):
                    raise ValueError(f"Word missing required attributes: {vars(word)}")

            for letter in entities_to_create["letters"]:
                if not all(hasattr(letter, attr) for attr in ["image_id", "line_id", "word_id", "letter_id", "receipt_id"]):
                    raise ValueError(f"Letter missing required attributes: {vars(letter)}")

            # Create lines
            if entities_to_create["lines"]:
                if debug:
                    print(
                        f"Creating {len(entities_to_create['lines'])} lines in batch..."
                    )
                client.addReceiptLines(entities_to_create["lines"])
                commit_results["created"]["lines"] = len(
                    entities_to_create["lines"]
                )

            # Create words
            if entities_to_create["words"]:
                if debug:
                    print(
                        f"Creating {len(entities_to_create['words'])} words in batch..."
                    )
                client.addReceiptWords(entities_to_create["words"])
                commit_results["created"]["words"] = len(
                    entities_to_create["words"]
                )

            # Create letters
            if entities_to_create["letters"]:
                if debug:
                    print(
                        f"Creating {len(entities_to_create['letters'])} letters in batch..."
                    )
                client.addReceiptLetters(entities_to_create["letters"])
                commit_results["created"]["letters"] = len(
                    entities_to_create["letters"]
                )

            # Create tags only after all other entities are created
            if entities_to_create["tags"]:
                if debug:
                    print(
                        f"Creating {len(entities_to_create['tags'])} tags in batch..."
                    )
                client.addReceiptWordTags(entities_to_create["tags"])
                commit_results["created"]["tags"] = len(entities_to_create["tags"])

        except Exception as creation_error:
            if debug:
                print(f"Error during entity creation: {creation_error}")
                import traceback
                traceback.print_exc()
            
            # If creation fails, we need to rollback any created entities
            try:
                if commit_results["created"]["tags"] > 0:
                    client.deleteReceiptWordTags(entities_to_create["tags"])
                if commit_results["created"]["letters"] > 0:
                    client.deleteReceiptLetters(entities_to_create["letters"])
                if commit_results["created"]["words"] > 0:
                    client.deleteReceiptWords(entities_to_create["words"])
                if commit_results["created"]["lines"] > 0:
                    client.deleteReceiptLines(entities_to_create["lines"])
                
                if debug:
                    print("Successfully rolled back partial changes")
            except Exception as rollback_error:
                if debug:
                    print(f"Error during rollback: {rollback_error}")
                    traceback.print_exc()
                print("WARNING: Database may be in an inconsistent state")
            
            # Re-raise the original creation error
            raise creation_error

        if debug:
            print("\n--- Database Commit Summary ---")
            print("Deleted entities:")
            for entity_type, count in commit_results["deleted"].items():
                print(f"  {entity_type.capitalize()}: {count}")

            print("Created entities:")
            for entity_type, count in commit_results["created"].items():
                print(f"  {entity_type.capitalize()}: {count}")

            print(
                "\nTotal DynamoDB transactions used: ~8 (vs ~373 individual operations previously)"
            )
            print(
                "Successfully committed all changes to database using batch operations."
            )

        return commit_results

    except Exception as e:
        if debug:
            print(f"Error committing OCR changes to database: {e}")
            import traceback
            traceback.print_exc()
        commit_results["success"] = False
        commit_results["error"] = str(e)
        return commit_results


def process_ocr_results(
    image_id,
    receipt_id,
    old_receipt_lines,
    old_receipt_words,
    old_receipt_letters,
    old_receipt_word_tags,
    new_receipt_lines,
    new_receipt_words,
    new_receipt_letters,
    debug=False,
):
    """
    Process OCR results using a delete-and-recreate approach.
    """
    # Convert the OCR results to Receipt-level objects
    new_receipt_lines = [
        ReceiptLine(
            **{
                **dict(line),
                "image_id": image_id,
                "receipt_id": receipt_id,
            }
        )
        for line in new_receipt_lines
    ]
    new_receipt_words = [
        ReceiptWord(
            **{
                **dict(word),
                "image_id": image_id,
                "receipt_id": receipt_id,
            }
        )
        for word in new_receipt_words
    ]
    new_receipt_letters = [
        ReceiptLetter(
            **{
                **dict(letter),
                "image_id": image_id,
                "receipt_id": receipt_id,
            }
        )
        for letter in new_receipt_letters
    ]

    # Create lists for entities to delete and create
    entities_to_delete = {
        "lines": old_receipt_lines,
        "words": old_receipt_words,
        "tags": [tag for tag in old_receipt_word_tags if hasattr(tag, "tag")],
        "letters": old_receipt_letters,
    }

    # STEP 1: Map tags using spatial matching
    # Create a dictionary of old tags
    old_tag_dict = {}
    for tag in old_receipt_word_tags:
        if hasattr(tag, "tag"):
            old_tag_dict[(tag.image_id, tag.line_id, tag.word_id)] = tag

    if debug:
        print(f"\n--- Tag Debugging ---")
        print(f"Number of old tags in dictionary: {len(old_tag_dict)}")
        print(f"Number of old words: {len(old_receipt_words)}")
        print(f"Number of new words: {len(new_receipt_words)}")

    # Separate tags into human-validated and non-validated
    validated_tag_keys = []
    non_validated_tag_keys = []

    for tag_key, tag in old_tag_dict.items():
        if hasattr(tag, "human_validated") and tag.human_validated:
            validated_tag_keys.append(tag_key)
        else:
            non_validated_tag_keys.append(tag_key)

    if debug:
        print(f"Human-validated tags: {len(validated_tag_keys)}")
        print(f"Non-validated tags: {len(non_validated_tag_keys)}")

    # For tag transfer, we still need to map between old and new words
    new_tags = []
    used_old_tag_keys = set()
    tag_match_reasons = {}
    matched_new_word_keys = set()  # Track which new words already have tags

    # Define proximity thresholds for tag matching
    EXACT_MATCH_THRESHOLD = 0.01  # Threshold for exact matches
    PROXIMITY_MATCH_THRESHOLD = 0.03  # Threshold for proximity matches

    # Process function to avoid code duplication
    def process_tags_for_transfer(tag_keys_to_process):
        """Process a list of tag keys for transfer to new words"""
        tags_transferred = 0

        for old_tag_key in tag_keys_to_process:
            if old_tag_key in used_old_tag_keys:
                continue  # Skip already used tags

            old_tag = old_tag_dict[old_tag_key]
            old_word = next(
                (
                    w
                    for w in old_receipt_words
                    if w.image_id == old_tag.image_id
                    and w.line_id == old_tag.line_id
                    and w.word_id == old_tag.word_id
                ),
                None,
            )

            if not old_word:
                continue  # Skip if we can't find the old word

            # Find the best matching new word for this tag
            best_match = None
            best_match_distance = float("inf")
            best_match_reason = None

            old_cx, old_cy = old_word.calculate_centroid()

            for new_word in new_receipt_words:
                # Skip if this word already has a tag
                new_word_key = (
                    new_word.image_id,
                    new_word.line_id,
                    new_word.word_id,
                )
                if new_word_key in matched_new_word_keys:
                    continue

                new_cx, new_cy = new_word.calculate_centroid()

                # Calculate distance
                distance = (
                    (new_cx - old_cx) ** 2 + (new_cy - old_cy) ** 2
                ) ** 0.5

                # Apply text and spatial adjustments
                adjusted_distance = distance

                # Text similarity boost
                if new_word.text.lower() == old_word.text.lower():
                    adjusted_distance *= (
                        0.2  # 80% reduction for exact text match
                    )
                    if distance < EXACT_MATCH_THRESHOLD:
                        best_match = new_word
                        best_match_distance = distance
                        best_match_reason = (
                            f"Exact text & location match: '{old_word.text}'"
                        )
                        break
                elif (
                    new_word.text.lower() in old_word.text.lower()
                    or old_word.text.lower() in new_word.text.lower()
                ):
                    adjusted_distance *= (
                        0.7  # 30% reduction for partial text match
                    )

                # Line context bonus
                if abs(new_cy - old_cy) < 0.02:  # Same vertical position
                    adjusted_distance *= 0.8  # 20% reduction

                # Check if point is within quadrilateral (stronger indicator)
                corners = {
                    "top_left": (
                        old_word.top_left["x"],
                        old_word.top_left["y"],
                    ),
                    "top_right": (
                        old_word.top_right["x"],
                        old_word.top_right["y"],
                    ),
                    "bottom_right": (
                        old_word.bottom_right["x"],
                        old_word.bottom_right["y"],
                    ),
                    "bottom_left": (
                        old_word.bottom_left["x"],
                        old_word.bottom_left["y"],
                    ),
                }

                in_quad = is_point_in_quadrilateral(new_cx, new_cy, corners)
                if in_quad:
                    adjusted_distance *= 0.5  # 50% reduction

                # Update best match if better
                if adjusted_distance < best_match_distance:
                    best_match = new_word
                    best_match_distance = adjusted_distance
                    if in_quad:
                        best_match_reason = (
                            f"In quad + distance: {distance:.4f}"
                        )
                    elif new_word.text.lower() == old_word.text.lower():
                        best_match_reason = f"Text match: '{old_word.text}' + distance: {distance:.4f}"
                    else:
                        best_match_reason = (
                            f"Centroid distance: {distance:.4f}"
                        )

            # Create tag if good match found
            if best_match and best_match_distance < PROXIMITY_MATCH_THRESHOLD:
                # Create new tag
                new_tag = type(old_tag)(
                    image_id=best_match.image_id,
                    receipt_id=best_match.receipt_id,
                    line_id=best_match.line_id,
                    word_id=best_match.word_id,
                    tag=old_tag.tag,
                    timestamp_added=getattr(old_tag, "timestamp_added", None),
                    human_validated=getattr(old_tag, "human_validated", False),
                )
                new_tags.append(new_tag)

                # Mark tag as used (one-to-one mapping)
                used_old_tag_keys.add(old_tag_key)

                # Record match for debugging
                new_word_key = (
                    best_match.image_id,
                    best_match.line_id,
                    best_match.word_id,
                )
                matched_new_word_keys.add(new_word_key)

                tag_match_reasons[new_word_key] = {
                    "old_word": f"'{old_tag.tag}' for word '{old_word.text}'",
                    "new_word": f"'{best_match.text}'",
                    "reason": best_match_reason,
                    "human_validated": getattr(
                        old_tag, "human_validated", False
                    ),
                }

                tags_transferred += 1

        return tags_transferred

    # First, process human-validated tags
    validated_tags_transferred = process_tags_for_transfer(validated_tag_keys)
    if debug:
        print(f"Transferred {validated_tags_transferred} human-validated tags")

    # Then, process non-validated tags
    non_validated_tags_transferred = process_tags_for_transfer(
        non_validated_tag_keys
    )
    if debug:
        print(
            f"Transferred {non_validated_tags_transferred} non-validated tags"
        )

    # Debug: Count tags by type
    if debug:
        tag_counts = {}
        validated_counts = {}
        for tag in new_tags:
            if tag.tag not in tag_counts:
                tag_counts[tag.tag] = 0
                validated_counts[tag.tag] = 0
            tag_counts[tag.tag] += 1
            if hasattr(tag, "human_validated") and tag.human_validated:
                validated_counts[tag.tag] += 1

        print("\nTag counts by type:")
        for tag_type, count in sorted(
            tag_counts.items(), key=lambda x: x[1], reverse=True
        ):
            validated = validated_counts[tag_type]
            print(f"{tag_type}: {count} (validated: {validated})")

    # Prepare result data
    result = {
        "success": True,
        "old_data": {
            "lines": len(entities_to_delete["lines"]),
            "words": len(entities_to_delete["words"]),
            "letters": len(entities_to_delete["letters"]),
            "tags": len(entities_to_delete["tags"]),
        },
        "new_data": {
            "lines": len(new_receipt_lines),
            "words": len(new_receipt_words),
            "letters": len(new_receipt_letters),
            "tags": len(new_tags),
        },
        "tag_transfer": {
            "total_tags": len(old_tag_dict),
            "transferred_tags": len(new_tags),
            "validated_tags_transferred": validated_tags_transferred,
            "non_validated_tags_transferred": non_validated_tags_transferred,
            "transfer_rate": (
                len(new_tags) / len(entities_to_delete["tags"])
                if entities_to_delete["tags"]
                else 0
            ),
        },
        "entities_to_delete": entities_to_delete,
        "entities_to_create": {
            "lines": new_receipt_lines,
            "words": new_receipt_words,
            "letters": new_receipt_letters,
            "tags": new_tags,
        },
    }

    # Print tag matching statistics if debug mode is on
    if debug:
        print(f"\n--- Tag Transfer Results ---")
        print(f"Original tags: {len(old_tag_dict)}")
        print(f"Tags transferred: {len(new_tags)}")
        print(
            f"Human-validated tags transferred: {validated_tags_transferred}/{len(validated_tag_keys)}"
        )
        print(
            f"Non-validated tags transferred: {non_validated_tags_transferred}/{len(non_validated_tag_keys)}"
        )
        print(f"Unique old tags used: {len(used_old_tag_keys)}")
        print(
            f"Tags created per unique old tag: {len(new_tags) / len(used_old_tag_keys) if used_old_tag_keys else 0:.2f}"
        )

        # Identify tags that weren't transferred
        untransferred_tags = []
        for tag_key, tag in old_tag_dict.items():
            if tag_key not in used_old_tag_keys:
                old_word = next(
                    (
                        w
                        for w in old_receipt_words
                        if w.image_id == tag.image_id
                        and w.line_id == tag.line_id
                        and w.word_id == tag.word_id
                    ),
                    None,
                )
                untransferred_tags.append(
                    (
                        tag.tag,
                        old_word.text if old_word else "Unknown",
                        getattr(tag, "human_validated", False),
                    )
                )

        if untransferred_tags:
            print("\nTags that were not transferred:")
            for tag_info in untransferred_tags:
                validation_status = (
                    "VALIDATED" if tag_info[2] else "non-validated"
                )
                print(
                    f"  - {tag_info[0]} for word '{tag_info[1]}' ({validation_status})"
                )

        # Print the first 10 tag matches for inspection
        print("\nSample of tag matches (first 10):")
        for i, ((img_id, line_id, word_id), match_info) in enumerate(
            list(tag_match_reasons.items())[:10]
        ):
            print(
                f"{i+1}. {match_info['old_word']} -> {match_info['new_word']} ({match_info['reason']})"
            )

        # Print summary of entities to delete and create
        print("\n--- Final Results (Delete and Recreate Approach) ---")
        print(f"Lines to delete: {len(entities_to_delete['lines'])}")
        print(f"Words to delete: {len(entities_to_delete['words'])}")
        print(f"Letters to delete: {len(entities_to_delete['letters'])}")
        print(f"Tags to delete: {len(entities_to_delete['tags'])}")
        print(f"New lines to create: {len(new_receipt_lines)}")
        print(f"New words to create: {len(new_receipt_words)}")
        print(f"New letters to create: {len(new_receipt_letters)}")
        print(f"New tags to create: {len(new_tags)}")

        # Calculate tag transfer rate
        if entities_to_delete["tags"]:
            tag_transfer_rate = len(new_tags) / len(entities_to_delete["tags"])
            print(f"Tag transfer rate: {tag_transfer_rate:.2%}")

    return result


def process_all_receipts(
    env=None, debug=False, commit_changes=False
):
    """
    Process multiple receipts to refine their OCR results.

    Args:
        env (dict, optional): Environment configuration from load_env(). If None, will use "prod".
        debug (bool, optional): Whether to print debug information
        commit_changes (bool, optional): Whether to commit the changes to the database

    Returns:
        dict: Summary of results
    """
    # Initialize environment if not provided
    if env is None:
        env = load_env("dev")
    else:
        env = load_env(env)
    if env is None:
        raise ValueError("Environment not found")

    # Create client from environment
    client = DynamoClient(env["dynamodb_table_name"])

    # Get receipts
    receipts_details, last_evaluated_key = client.listReceiptDetails()

    results = {"processed": 0, "succeeded": 0, "failed": 0, "details": {}}

    for key, details in receipts_details.items():
        # Parse the key into image_id and receipt_id
        image_id, receipt_id_str = key.split("_")
        receipt_id = int(receipt_id_str)

        if debug:
            print(f"\n=== Processing receipt {image_id}_{receipt_id} ===")

        # Process the receipt using the env parameter
        result = refine_receipt_ocr(
            image_id=image_id,
            receipt_id=receipt_id,
            env=env,
            debug=debug,
            commit_changes=commit_changes,
        )

        # Update results summary
        results["processed"] += 1
        if result["success"]:
            results["succeeded"] += 1
        else:
            results["failed"] += 1

        results["details"][key] = result

    return results
