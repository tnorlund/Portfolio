from receipt_label.poll_completion_batch import (
    list_pending_completion_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    update_valid_labels,
    update_invalid_labels,
    write_completion_batch_results,
)
from receipt_label.utils import get_clients
from receipt_label.poll_completion_batch.poll_batch import _chunk, PINECONE_NS
from copy import deepcopy
from receipt_dynamo.entities import ReceiptWordLabel, BatchSummary


def _serialize_rwl(rwl: ReceiptWordLabel) -> tuple:
    """Return a hashable snapshot of the RWL row we care about."""
    return (
        rwl.image_id,
        rwl.receipt_id,
        rwl.line_id,
        rwl.word_id,
        rwl.label,
        rwl.validation_status,
        rwl.label_proposed_by,
        getattr(rwl, "label_consolidated_from", None),
    )


dynamo_client, openai_client, pinecone_index = get_clients()

pending_completion_batches = list_pending_completion_batches()

for batch in pending_completion_batches:
    openai_batch_id = batch.openai_batch_id
    openai_batch_status = get_openai_batch_status(openai_batch_id)
    if openai_batch_status == "completed":
        parsed_results = download_openai_batch_result(openai_batch_id, batch)
        # write_completion_batch_results(parsed_results)
        # Separate the valid and invalid labels
        valid_labels = [result for result in parsed_results if result.is_valid]
        invalid_labels = [result for result in parsed_results if not result.is_valid]

        for label in valid_labels:
            print(label.vector_id)

        # # --- Backup current Pinecone metadata for affected vectors ------------
        # vector_ids = {r.vector_id for r in valid_labels}
        # backup_meta = {}

        # for id_batch in _chunk(vector_ids, 100):
        #     fetched = pinecone_index.fetch(
        #         ids=list(id_batch), namespace=PINECONE_NS
        #     ).vectors
        #     for vid, vec in fetched.items():
        #         # deepcopy to avoid mutation
        #         backup_meta[vid] = (vec.metadata or {}).copy()

        # # --- Update and then restore metadata ---------------------------------
        # try:
        #     update_valid_labels(valid_labels)

        #     # fetch again to confirm metadata changed as expected
        #     for id_batch in _chunk(vector_ids, 100):
        #         after_update = pinecone_index.fetch(
        #             ids=list(id_batch), namespace=PINECONE_NS
        #         ).vectors
        #         for vid in id_batch:
        #             assert set(after_update[vid].metadata["valid_labels"]).issuperset(
        #                 backup_meta.get(vid, {}).get("valid_labels", [])
        #             )
        # finally:
        #     for vid, meta in backup_meta.items():
        #         pinecone_index.update(id=vid, set_metadata=meta, namespace=PINECONE_NS)

        # # ---------------------------------------------------------------
        # # Handle INVALID labels with the same safety guarantees
        # # ---------------------------------------------------------------
        # if invalid_labels:
        #     # a. Backup Pinecone metadata for the related vectors
        #     invalid_vector_ids = {r.vector_id for r in invalid_labels}
        #     invalid_backup_meta = {}
        #     for id_batch in _chunk(invalid_vector_ids, 100):
        #         fetched = pinecone_index.fetch(
        #             ids=list(id_batch), namespace=PINECONE_NS
        #         ).vectors
        #         for vid, vec in fetched.items():
        #             invalid_backup_meta[vid] = (vec.metadata or {}).copy()

        #     # b. Backup the corresponding ReceiptWordLabels from DynamoDB
        #     invalid_indices = [
        #         (r.image_id, r.receipt_id, r.line_id, r.word_id, r.label)
        #         for r in invalid_labels
        #     ]
        #     original_rwls = dynamo_client.getReceiptWordLabelsByIndices(invalid_indices)
        #     rwl_backup = [deepcopy(lbl) for lbl in original_rwls]

        #     # make a complete BEFORE snapshot of every affected row
        #     before_snapshot = {_serialize_rwl(lbl) for lbl in rwl_backup}

        #     try:
        #         update_invalid_labels(invalid_labels)

        #         # Sanity‑check Pinecone changes
        #         for id_batch in _chunk(invalid_vector_ids, 100):
        #             after = pinecone_index.fetch(
        #                 ids=list(id_batch), namespace=PINECONE_NS
        #             ).vectors
        #             for vid in id_batch:
        #                 assert set(after[vid].metadata["invalid_labels"]).issuperset(
        #                     invalid_backup_meta.get(vid, {}).get("invalid_labels", [])
        #                 )
        #     finally:
        #         # ----------------------------------------------------------------
        #         # 1. Delete any *new* RWL rows created during the call
        #         # ----------------------------------------------------------------
        #         after_rwls = dynamo_client.getReceiptWordLabelsByIndices(
        #             invalid_indices
        #         )
        #         for row in after_rwls:
        #             if _serialize_rwl(row) not in before_snapshot:
        #                 dynamo_client.deleteReceiptWordLabel(row)

        #         # ----------------------------------------------------------------
        #         # 2. Restore the original rows’ attributes
        #         # ----------------------------------------------------------------
        #         if rwl_backup:
        #             dynamo_client.updateReceiptWordLabels(rwl_backup)

        #         # ----------------------------------------------------------------
        #         # 3. Restore Pinecone metadata
        #         # ----------------------------------------------------------------
        #         for vid, meta in invalid_backup_meta.items():
        #             pinecone_index.update(
        #                 id=vid, set_metadata=meta, namespace=PINECONE_NS
        #             )

        #         # ----------------------------------------------------------------
        #         # 4. Assert that DynamoDB state matches the BEFORE snapshot
        #         # ----------------------------------------------------------------
        #         final_rwls = dynamo_client.getReceiptWordLabelsByIndices(
        #             invalid_indices
        #         )
        #         assert {_serialize_rwl(lbl) for lbl in final_rwls} == before_snapshot
