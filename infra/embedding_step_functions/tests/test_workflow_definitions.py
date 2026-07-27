"""Contract tests for embedding Step Functions definitions."""

import json

from embedding_step_functions.components.backfill_workflow import (
    build_backfill_definition,
)
from embedding_step_functions.components.embedding_workflow import (
    build_ingest_definition,
    build_submit_definition,
)


def _assert_targets_exist(definition: dict) -> None:
    states = definition["States"]
    for state in states.values():
        targets = []
        if "Next" in state:
            targets.append(state["Next"])
        if "Default" in state:
            targets.append(state["Default"])
        targets.extend(choice["Next"] for choice in state.get("Choices", []))
        targets.extend(catch["Next"] for catch in state.get("Catch", []))
        assert set(targets) <= set(states)
        if "Iterator" in state:
            _assert_targets_exist(state["Iterator"])
        for branch in state.get("Branches", []):
            _assert_targets_exist(branch)


def test_submit_definition_is_bounded_and_discards_map_output() -> None:
    definition = json.loads(
        build_submit_definition("words", "find-arn", "submit-arn")
    )
    find = definition["States"]["FindAndClaimUnembedded"]
    submit = definition["States"]["SubmitBatches"]

    assert "Parameters" not in find
    assert submit["MaxConcurrency"] == 10
    assert submit["ResultPath"] is None
    _assert_targets_exist(definition)


def test_ingest_definition_polls_active_batches_and_finalizes_last() -> None:
    definition = json.loads(
        build_ingest_definition(
            "lines", "list", "poll", "compact", "normalize", "mark"
        )
    )
    states = definition["States"]

    assert definition["StartAt"] == "ListActiveBatches"
    assert states["ListActiveBatches"]["Parameters"]["batch_type"] == "line"
    assert states["FinalMerge"]["Next"] == "PrepareFinalization"
    assert states["FinalizeBatches"]["Resource"] == "mark"
    assert states["FinalizeBatches"]["End"] is True
    _assert_targets_exist(definition)


def test_embed_all_definition_is_manual_resumable_and_payload_bounded() -> (
    None
):
    definition = json.loads(
        build_backfill_definition(
            "control",
            "line-submit",
            "word-submit",
            "line-ingest",
            "word-ingest",
        )
    )
    states = definition["States"]

    assert definition["TimeoutSeconds"] == 172800
    assert definition["StartAt"] == "AcquireLease"
    assert states["LeaseAcquired"]["Default"] == "AlreadyRunning"
    assert states["InitializeOnce"]["Parameters"]["action"] == "initialize"
    assert states["WaitForFixedPoint"]["Next"] == "ConfirmFixedPoint"
    assert states["MarkBackfillComplete"]["Parameters"]["action"] == "complete"
    for branch in states["SubmitMissing"]["Branches"]:
        child_input = branch["States"][branch["StartAt"]]["Parameters"][
            "Input"
        ]
        assert child_input["submission_namespace"] == "backfill-v1"

    for state_name in ("SubmitMissing", "IngestActive"):
        state = states[state_name]
        assert state["ResultPath"] is None
        branch_start_names = [b["StartAt"] for b in state["Branches"]]
        assert len(set(branch_start_names)) == len(branch_start_names)
        for branch in state["Branches"]:
            child = branch["States"][branch["StartAt"]]
            assert child["Resource"].endswith("startExecution.sync:2")
            assert child["ResultPath"] is None

    _assert_targets_exist(definition)
