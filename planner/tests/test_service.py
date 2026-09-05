"""User-visible invariants across edits, retries, and agent proposals."""

import copy
from concurrent.futures import ThreadPoolExecutor

import boto3
import pytest
from moto import mock_aws

from planner.data.client import table_definition
from planner.service import Conflict, Planner, ValidationError
from planner.store import Store


@pytest.fixture
def planner():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(**table_definition("PlannerTest"))
        yield Planner(Store("PlannerTest", client=client))


def call(planner, action, **args):
    return planner.execute({"action": action, **args})


def task(planner, **args):
    return call(planner, "save_item", text="Write a draft", **args)["result"]


def test_retry_does_not_reopen_or_duplicate_completed_task(planner):
    command = {"action": "save_item", "text": "Write a draft"}
    created = planner.execute(command, request_id="create-draft")
    item = created["result"]
    call(
        planner,
        "save_item",
        id=item["id"],
        revision=item["revision"],
        done=True,
    )
    retried = planner.execute(command, request_id="create-draft")
    assert retried == created
    state = planner.snapshot()
    assert len(state["items"]) == 1
    assert state["items"][0]["done"] is True
    assert state["version"] == 2
    with pytest.raises(Conflict):
        planner.execute(
            {**command, "text": "Different"}, request_id="create-draft"
        )


def test_stable_identity_move_and_edit_preserve_completion(planner):
    item = task(planner, date="2026-09-07")
    edited = call(
        planner,
        "save_item",
        id=item["id"],
        revision=1,
        text="Finish draft",
        date="2026-09-09",
        done=True,
    )["result"]
    assert edited["id"] == item["id"]
    assert edited["done"]
    assert edited["date"] == "2026-09-09"
    assert len(planner.snapshot()["items"]) == 1
    with pytest.raises(Conflict):
        call(
            planner, "save_item", id=item["id"], revision=1, text="Stale edit"
        )


def test_proposal_is_atomic_and_acceptance_repeat_is_noop(planner):
    item = task(planner)
    proposed = call(
        planner,
        "propose",
        title="Make room on Monday",
        rationale="The draft fits before the deadline.",
        changes=[
            {
                "action": "save_item",
                "id": item["id"],
                "revision": 1,
                "date": "2026-09-07",
            }
        ],
    )["result"]
    assert planner.snapshot()["items"][0]["date"] is None
    result = call(
        planner, "resolve_proposal", id=proposed["id"], decision="accept"
    )
    assert result["result"]["status"] == "accepted"
    version = planner.snapshot()["version"]
    call(planner, "resolve_proposal", id=proposed["id"], decision="accept")
    assert planner.snapshot()["version"] == version
    assert planner.snapshot()["items"][0]["date"] == "2026-09-07"


def test_stale_proposal_does_not_overwrite_later_work(planner):
    item = task(planner)
    proposal = call(
        planner,
        "propose",
        title="Move draft",
        rationale="More time",
        changes=[
            {
                "action": "save_item",
                "id": item["id"],
                "revision": 1,
                "date": "2026-09-08",
            }
        ],
    )["result"]
    call(planner, "save_item", id=item["id"], revision=1, done=True)
    with pytest.raises(Conflict):
        call(planner, "resolve_proposal", id=proposal["id"], decision="accept")
    assert planner.snapshot()["items"][0]["done"]
    assert planner.snapshot()["proposals"][0]["status"] == "proposed"


def test_invalid_batch_rolls_back_every_change_and_clock(planner):
    before = copy.deepcopy(planner.snapshot())
    with pytest.raises(ValidationError):
        call(
            planner,
            "batch",
            changes=[
                {"action": "save_item", "text": "Valid"},
                {
                    "action": "save_item",
                    "text": "Invalid",
                    "date": "2026-02-30",
                },
            ],
        )
    assert planner.snapshot() == before


def test_routines_are_target_date_based_and_never_reset_instances(planner):
    routine = call(
        planner,
        "save_routine",
        text="Walk",
        weekdays=[0, 2],
        starts_on="2026-09-08",
    )["result"]
    call(planner, "plan_week", week="2026-09-07")
    items = planner.snapshot()["items"]
    assert len(items) == 1
    assert items[0]["date"] == "2026-09-09"
    assert items[0]["routine_id"] == routine["id"]
    call(planner, "save_item", id=items[0]["id"], revision=1, done=True)
    before = planner.snapshot()["version"]
    call(planner, "plan_week", week="2026-09-07")
    assert planner.snapshot()["version"] == before
    assert planner.snapshot()["items"][0]["done"]


def test_carry_is_explicit_and_preserves_task_identity(planner):
    item = task(planner, date="2026-09-07")
    call(
        planner,
        "close_week",
        week="2026-09-07",
        wins="Started",
        misses="Time",
        carry_ids=[item["id"]],
    )
    state = planner.snapshot()
    assert len(state["items"]) == 1
    assert state["items"][0]["id"] == item["id"]
    assert state["items"][0]["date"] is None
    assert state["items"][0]["week"] == "2026-09-14"
    assert state["items"][0]["carried_from"] == "2026-09-07"
    assert state["weeks"]["2026-09-07"]["status"] == "closed"


def test_rename_and_archive_area_keep_tasks(planner):
    area = call(planner, "save_area", name="Reading", color="#65816a")[
        "result"
    ]
    item = task(planner, area_id=area["id"])
    call(planner, "save_area", id=area["id"], revision=1, name="Learning")
    call(planner, "save_area", id=area["id"], revision=2, archived=True)
    assert planner.snapshot()["items"][0]["id"] == item["id"]
    assert planner.snapshot()["areas"][0]["name"] == "Learning"


def test_concurrent_writers_keep_both_edits(planner):
    second = Planner(Store("PlannerTest", client=planner.store._client))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: task(p), [planner, second]))
    assert len(planner.snapshot()["items"]) == 2
    assert results[0]["id"] != results[1]["id"]
    assert planner.snapshot()["version"] == 2


def test_unknown_fields_and_unsupported_actions_are_rejected(planner):
    with pytest.raises(ValidationError):
        task(planner, arbitrary=True)
    with pytest.raises(ValidationError):
        call(planner, "delete_everything")
    assert planner.snapshot()["version"] == 0


@pytest.mark.parametrize("args", [{"id": ""}, {"id": False}, {"kind": []}])
def test_malformed_item_fields_do_not_create_records(planner, args):
    with pytest.raises(ValidationError):
        task(planner, **args)
    assert planner.snapshot()["items"] == []


def test_blank_retry_id_is_rejected(planner):
    with pytest.raises(ValidationError):
        planner.execute({"action": "save_item", "text": "Example"}, "")
    assert planner.snapshot()["version"] == 0
