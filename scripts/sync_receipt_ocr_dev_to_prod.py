#!/usr/bin/env python3
"""
Sync re-OCR'd receipt data (ReceiptLine, ReceiptWord, ReceiptLetter) from dev to prod.

After regional re-OCR in dev, words may have been updated, added, or orphaned.
This script makes prod's ReceiptLine/ReceiptWord/ReceiptLetter records match dev
exactly by:

1. Querying all ReceiptLines/ReceiptWords for each receipt in both dev and prod
2. For each receipt:
   a. Deleting prod records that don't exist in dev (orphaned/removed)
   b. Adding prod records that exist in dev but not prod (new words from re-OCR)
   c. Updating prod records where text or bounding box differs
3. For letters: deleting all prod letters for affected words, then writing dev letters

Usage:
    # Dry run for specific images
    python scripts/sync_receipt_ocr_dev_to_prod.py \\
        --image-ids 0324604e-e1f7-4021-b887-7ef7e012c563

    # Dry run for all images that changed recently
    python scripts/sync_receipt_ocr_dev_to_prod.py --all-reocrd

    # Actually sync
    python scripts/sync_receipt_ocr_dev_to_prod.py \\
        --image-ids 0324604e-e1f7-4021-b887-7ef7e012c563 \\
        --no-dry-run
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import EmbeddingStatus, OCRJobType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_letter import ReceiptLetter
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def line_key(rl: ReceiptLine) -> Tuple[str, int, int]:
    return (rl.image_id, rl.receipt_id, rl.line_id)


def word_key(rw: ReceiptWord) -> Tuple[str, int, int, int]:
    return (rw.image_id, rw.receipt_id, rw.line_id, rw.word_id)


def letter_key(rl: ReceiptLetter) -> Tuple[str, int, int, int, int]:
    return (rl.image_id, rl.receipt_id, rl.line_id, rl.word_id, rl.letter_id)


def line_differs(dev: ReceiptLine, prod: ReceiptLine) -> bool:
    """Check if a ReceiptLine changed (text or bounding box)."""
    if dev.text != prod.text:
        return True
    for attr in ("top_left", "top_right", "bottom_left", "bottom_right"):
        if getattr(dev, attr, None) != getattr(prod, attr, None):
            return True
    return False


def word_differs(dev: ReceiptWord, prod: ReceiptWord) -> bool:
    """Check if a ReceiptWord changed (text, bounding box, confidence, etc.)."""
    if dev.text != prod.text:
        return True
    for attr in (
        "top_left", "top_right", "bottom_left", "bottom_right",
        "bounding_box", "confidence", "angle_degrees", "angle_radians",
        "is_noise",
    ):
        if getattr(dev, attr, None) != getattr(prod, attr, None):
            return True
    return False


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

@dataclass
class SyncStats:
    lines_added: int = 0
    lines_updated: int = 0
    lines_deleted: int = 0
    lines_unchanged: int = 0
    words_added: int = 0
    words_updated: int = 0
    words_deleted: int = 0
    words_unchanged: int = 0
    letters_deleted: int = 0
    letters_added: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"Lines:   +{self.lines_added}  ~{self.lines_updated}  "
            f"-{self.lines_deleted}  ={self.lines_unchanged}\n"
            f"Words:   +{self.words_added}  ~{self.words_updated}  "
            f"-{self.words_deleted}  ={self.words_unchanged}\n"
            f"Letters: +{self.letters_added}  -{self.letters_deleted}\n"
            f"Errors:  {self.errors}"
        )


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_receipt(
    dev_client: DynamoClient,
    prod_client: DynamoClient,
    image_id: str,
    receipt_id: int,
    dry_run: bool = True,
) -> SyncStats:
    """
    Sync ReceiptLine/ReceiptWord/ReceiptLetter records for one receipt
    from dev to prod.
    """
    stats = SyncStats()

    # ------------------------------------------------------------------
    # 1. Fetch all lines and words from both environments
    # ------------------------------------------------------------------
    dev_lines = dev_client.list_receipt_lines_from_receipt(image_id, receipt_id)
    prod_lines = prod_client.list_receipt_lines_from_receipt(image_id, receipt_id)
    dev_words = dev_client.list_receipt_words_from_receipt(image_id, receipt_id)
    prod_words = prod_client.list_receipt_words_from_receipt(image_id, receipt_id)

    dev_lines_by_key = {line_key(l): l for l in dev_lines}
    prod_lines_by_key = {line_key(l): l for l in prod_lines}
    dev_words_by_key = {word_key(w): w for w in dev_words}
    prod_words_by_key = {word_key(w): w for w in prod_words}

    dev_line_keys = set(dev_lines_by_key.keys())
    prod_line_keys = set(prod_lines_by_key.keys())
    dev_word_keys = set(dev_words_by_key.keys())
    prod_word_keys = set(prod_words_by_key.keys())

    # ------------------------------------------------------------------
    # 2. Compute diffs
    # ------------------------------------------------------------------

    # Lines
    lines_to_add_keys = dev_line_keys - prod_line_keys
    lines_to_delete_keys = prod_line_keys - dev_line_keys
    lines_common_keys = dev_line_keys & prod_line_keys

    lines_to_add = [dev_lines_by_key[k] for k in lines_to_add_keys]
    lines_to_delete = [prod_lines_by_key[k] for k in lines_to_delete_keys]
    lines_to_update = []
    for k in lines_common_keys:
        if line_differs(dev_lines_by_key[k], prod_lines_by_key[k]):
            lines_to_update.append(dev_lines_by_key[k])
        else:
            stats.lines_unchanged += 1

    # Words
    words_to_add_keys = dev_word_keys - prod_word_keys
    words_to_delete_keys = prod_word_keys - dev_word_keys
    words_common_keys = dev_word_keys & prod_word_keys

    words_to_add = [dev_words_by_key[k] for k in words_to_add_keys]
    words_to_delete = [prod_words_by_key[k] for k in words_to_delete_keys]
    words_to_update = []
    words_changed_keys: Set[Tuple] = set()  # track which words need letter sync
    for k in words_common_keys:
        if word_differs(dev_words_by_key[k], prod_words_by_key[k]):
            words_to_update.append(dev_words_by_key[k])
            words_changed_keys.add(k)
        else:
            stats.words_unchanged += 1

    # ------------------------------------------------------------------
    # 3. Log what we found
    # ------------------------------------------------------------------
    logger.info(
        f"  Receipt {image_id[:8]}.../{receipt_id}: "
        f"lines(+{len(lines_to_add)} ~{len(lines_to_update)} "
        f"-{len(lines_to_delete)}) "
        f"words(+{len(words_to_add)} ~{len(words_to_update)} "
        f"-{len(words_to_delete)})"
    )

    if not lines_to_add and not lines_to_update and not lines_to_delete \
       and not words_to_add and not words_to_update and not words_to_delete:
        logger.info("    No changes needed.")
        return stats

    # ------------------------------------------------------------------
    # 4. Apply changes
    # ------------------------------------------------------------------
    prefix = "[DRY RUN] " if dry_run else ""

    # 4a. Delete words that no longer exist (and their letters)
    if words_to_delete:
        logger.info(f"    {prefix}Deleting {len(words_to_delete)} orphaned words...")
        # Delete letters for these words first
        for w in words_to_delete:
            try:
                old_letters = prod_client.list_receipt_letters_from_word(
                    w.image_id, w.receipt_id, w.line_id, w.word_id
                )
                if old_letters and not dry_run:
                    prod_client.remove_receipt_letters(old_letters)
                stats.letters_deleted += len(old_letters)
            except Exception as e:
                logger.warning(f"    Error deleting letters for word {word_key(w)}: {e}")
                stats.errors += 1

        if not dry_run:
            try:
                prod_client.delete_receipt_words(words_to_delete)
            except Exception as e:
                logger.error(f"    Error deleting words: {e}")
                stats.errors += 1
        stats.words_deleted += len(words_to_delete)

    # 4b. Delete lines that no longer exist
    if lines_to_delete:
        logger.info(f"    {prefix}Deleting {len(lines_to_delete)} orphaned lines...")
        if not dry_run:
            try:
                prod_client.delete_receipt_lines(lines_to_delete)
            except Exception as e:
                logger.error(f"    Error deleting lines: {e}")
                stats.errors += 1
        stats.lines_deleted += len(lines_to_delete)

    # 4c. Update existing words that changed
    if words_to_update:
        logger.info(f"    {prefix}Updating {len(words_to_update)} changed words...")
        for w in words_to_update:
            # Reset embedding_status since text changed
            w_dict = dict(w)
            w_dict["embedding_status"] = EmbeddingStatus.NONE.value
            updated_word = ReceiptWord(**w_dict)
            if not dry_run:
                try:
                    prod_client.update_receipt_words([updated_word])
                except Exception as e:
                    logger.error(f"    Error updating word {word_key(w)}: {e}")
                    stats.errors += 1
        stats.words_updated += len(words_to_update)

        # Sync letters for updated words
        for w in words_to_update:
            k = word_key(w)
            try:
                dev_letters = dev_client.list_receipt_letters_from_word(
                    w.image_id, w.receipt_id, w.line_id, w.word_id
                )
                prod_letters = prod_client.list_receipt_letters_from_word(
                    w.image_id, w.receipt_id, w.line_id, w.word_id
                )
                if not dry_run:
                    # Delete old letters first (same key collision issue as overlay)
                    if prod_letters:
                        prod_client.remove_receipt_letters(prod_letters)
                    if dev_letters:
                        prod_client.put_receipt_letters(dev_letters)
                stats.letters_deleted += len(prod_letters)
                stats.letters_added += len(dev_letters)
            except Exception as e:
                logger.warning(f"    Error syncing letters for word {k}: {e}")
                stats.errors += 1

    # 4d. Update existing lines that changed
    if lines_to_update:
        logger.info(f"    {prefix}Updating {len(lines_to_update)} changed lines...")
        for l in lines_to_update:
            # Reset embedding_status since text changed
            l_dict = dict(l)
            l_dict["embedding_status"] = EmbeddingStatus.NONE.value
            updated_line = ReceiptLine(**l_dict)
            if not dry_run:
                try:
                    prod_client.update_receipt_lines([updated_line])
                except Exception as e:
                    logger.error(f"    Error updating line {line_key(l)}: {e}")
                    stats.errors += 1
        stats.lines_updated += len(lines_to_update)

    # 4e. Add new lines
    if lines_to_add:
        logger.info(f"    {prefix}Adding {len(lines_to_add)} new lines...")
        for l in lines_to_add:
            l_dict = dict(l)
            l_dict["embedding_status"] = EmbeddingStatus.NONE.value
            new_line = ReceiptLine(**l_dict)
            if not dry_run:
                try:
                    prod_client.add_receipt_lines([new_line])
                except Exception as e:
                    logger.error(f"    Error adding line {line_key(l)}: {e}")
                    stats.errors += 1
        stats.lines_added += len(lines_to_add)

    # 4f. Add new words (and their letters)
    if words_to_add:
        logger.info(f"    {prefix}Adding {len(words_to_add)} new words...")
        for w in words_to_add:
            w_dict = dict(w)
            w_dict["embedding_status"] = EmbeddingStatus.NONE.value
            new_word = ReceiptWord(**w_dict)
            if not dry_run:
                try:
                    prod_client.add_receipt_words([new_word])
                except Exception as e:
                    logger.error(f"    Error adding word {word_key(w)}: {e}")
                    stats.errors += 1

            # Add letters for new words
            try:
                dev_letters = dev_client.list_receipt_letters_from_word(
                    w.image_id, w.receipt_id, w.line_id, w.word_id
                )
                if dev_letters and not dry_run:
                    prod_client.put_receipt_letters(dev_letters)
                stats.letters_added += len(dev_letters)
            except Exception as e:
                logger.warning(f"    Error adding letters for word {word_key(w)}: {e}")
                stats.errors += 1

        stats.words_added += len(words_to_add)

    return stats


def get_receipts_for_image(
    client: DynamoClient, image_id: str
) -> List[int]:
    """Get all receipt IDs for an image."""
    details = client.get_image_details(image_id)
    return [r.receipt_id for r in details.receipts]


def main():
    parser = argparse.ArgumentParser(
        description="Sync re-OCR'd receipt data from dev to prod"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--image-ids",
        nargs="+",
        help="Image IDs to sync (full UUIDs)",
    )
    group.add_argument(
        "--all-reocr",
        action="store_true",
        help="Auto-discover all image IDs that have REGIONAL_REOCR jobs in dev",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually sync records",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE SYNC"
    logger.info(f"Mode: {mode}")
    if args.dry_run:
        logger.info("No changes will be made. Use --no-dry-run to actually sync.")

    try:
        # Get configurations from Pulumi
        logger.info("Loading configurations from Pulumi...")
        dev_config = load_env(env="dev")
        prod_config = load_env(env="prod")

        dev_table = dev_config["dynamodb_table_name"]
        prod_table = prod_config["dynamodb_table_name"]
        logger.info(f"Dev table:  {dev_table}")
        logger.info(f"Prod table: {prod_table}")

        dev_client = DynamoClient(dev_table)
        prod_client = DynamoClient(prod_table)

        # Resolve image IDs
        if args.all_reocr:
            logger.info("Discovering image IDs from REGIONAL_REOCR jobs in dev...")
            image_ids = set()
            last_key = None
            while True:
                jobs, last_key = dev_client.list_ocr_jobs(
                    limit=500, last_evaluated_key=last_key
                )
                for job in jobs:
                    if job.job_type == OCRJobType.REGIONAL_REOCR.value:
                        image_ids.add(job.image_id)
                if last_key is None:
                    break
            image_ids = sorted(image_ids)
            logger.info("  Found %d image(s) with REGIONAL_REOCR jobs", len(image_ids))
            if not image_ids:
                logger.info("Nothing to sync!")
                return
        else:
            image_ids = args.image_ids

        # Process each image
        total_stats = SyncStats()
        for image_id in image_ids:
            logger.info(f"\nProcessing image {image_id}...")

            # Get receipt IDs from dev
            receipt_ids = get_receipts_for_image(dev_client, image_id)
            if not receipt_ids:
                logger.warning(f"  No receipts found in dev for {image_id}")
                continue

            logger.info(f"  Found {len(receipt_ids)} receipt(s): {receipt_ids}")

            for receipt_id in receipt_ids:
                stats = sync_receipt(
                    dev_client, prod_client,
                    image_id, receipt_id,
                    dry_run=args.dry_run,
                )

                # Accumulate stats
                total_stats.lines_added += stats.lines_added
                total_stats.lines_updated += stats.lines_updated
                total_stats.lines_deleted += stats.lines_deleted
                total_stats.lines_unchanged += stats.lines_unchanged
                total_stats.words_added += stats.words_added
                total_stats.words_updated += stats.words_updated
                total_stats.words_deleted += stats.words_deleted
                total_stats.words_unchanged += stats.words_unchanged
                total_stats.letters_deleted += stats.letters_deleted
                total_stats.letters_added += stats.letters_added
                total_stats.errors += stats.errors

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 60)
        logger.info(f"\n{total_stats}")

        if args.dry_run:
            logger.info("\n[DRY RUN] No changes made. Use --no-dry-run to sync.")
        else:
            if total_stats.errors > 0:
                logger.error(f"\nSync completed with {total_stats.errors} errors")
                sys.exit(1)
            else:
                logger.info("\nSync completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
