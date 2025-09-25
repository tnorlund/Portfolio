# pylint: disable=redefined-outer-name
from uuid import uuid4

import pytest
from moto import mock_aws

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun


@pytest.mark.integration
def test_compaction_run_crud_and_queries(dynamodb_table):
    client = DynamoClient(table_name=dynamodb_table)

    image_id = str(uuid4())
    receipt_id = 1
    run_id = str(uuid4())

    run = CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        lines_delta_prefix="chromadb/lines/delta/run/",
        words_delta_prefix="chromadb/words/delta/run/",
    )

    # add
    client.add_compaction_run(run)

    # get
    got = client.get_compaction_run(image_id, receipt_id, run_id)
    assert got is not None
    assert got.run_id == run_id
    assert got.image_id == image_id
    assert got.receipt_id == receipt_id

    # list for receipt
    items, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
    assert any(x.run_id == run_id for x in items)

    # list recent
    recent, _ = client.list_recent_compaction_runs(limit=10)
    assert any(x.run_id == run_id for x in recent)

    # mark started/completed
    client.mark_compaction_run_started(image_id, receipt_id, run_id, "lines")
    client.mark_compaction_run_completed(
        image_id, receipt_id, run_id, "lines", merged_vectors=5
    )
    client.mark_compaction_run_started(image_id, receipt_id, run_id, "words")
    client.mark_compaction_run_completed(
        image_id, receipt_id, run_id, "words", merged_vectors=7
    )

    updated = client.get_compaction_run(image_id, receipt_id, run_id)
    assert updated is not None
    assert updated.lines_merged_vectors == 5
    assert updated.words_merged_vectors == 7
