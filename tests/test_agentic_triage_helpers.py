"""Triage helper digest (scripts/agentic_triage_helpers.py).

Candidate enumeration must stay read-only and delegate every guard
decision to the real ``extend_items_section_impl`` — these tests run
the digest over the shared stub world and check that the simulated
candidates carry the guard's own verdicts.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("receipt_dynamo")
pytest.importorskip("receipt_upload")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helpers = _load(
    "agentic_helpers_test",
    REPO_ROOT / "scripts" / "agentic_triage_helpers.py",
)

from test_agentic_writer import IMAGE_ID, MutableWorld  # noqa: E402


def test_enumerate_candidates_marks_contiguity():
    world = MutableWorld()
    world.sections[0].line_ids = [2, 3]
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    other = ReceiptSection(
        receipt_id=1,
        image_id=IMAGE_ID,
        section_type="SUMMARY",
        line_ids=[1],
        created_at=world.sections[0].created_at,
        model_source="seed",
        validation_status="NONE",
    )
    candidates = helpers.enumerate_extension_candidates(
        [1, 2, 3, 4], [world.sections[0], other]
    )
    # Line 1 is claimed by SUMMARY; line 4 is the only unclaimed run,
    # adjacent below the ITEMS span.
    assert candidates == [{"add_line_ids": [4], "contiguous": True}]


def test_enumerate_candidates_grows_adjacent_runs_cumulatively():
    world = MutableWorld()
    world.sections[0].line_ids = [1, 2]
    candidates = helpers.enumerate_extension_candidates(
        [1, 2, 3, 4], world.sections
    )
    assert {"add_line_ids": [3], "contiguous": True} in candidates
    assert {"add_line_ids": [3, 4], "contiguous": True} in candidates


def test_digest_simulates_through_the_real_guard():
    world = MutableWorld()
    digest = helpers.build_digest(world, IMAGE_ID, 1)

    assert digest["line_items"]["delta"] == -4.0
    assert digest["summary"]["merchant_name"] == "Test Mart"
    assert digest["is_proven"] is False  # mismatch, no bank amount

    by_lids = {
        tuple(c["add_line_ids"]): c for c in digest["extension_candidates"]
    }
    # Adding ORANGES (line 3) passes the guard; absorbing JUNKTHING
    # (line 4, $50) must be refused by the same math the MCP tool uses.
    assert by_lids[(3,)]["verified"] is True
    assert by_lids[(3,)]["after"]["status"] == "match"
    assert by_lids[(3, 4)]["verified"] is False
    assert digest["best_extension"]["add_line_ids"] == [3]
    # Read-only: the simulations must not have written anything.
    assert world.summary_updates == []
    assert world.section_updates == []


def test_dossier_skeleton_prefills_v2_fields():
    world = MutableWorld()
    digest = helpers.build_digest(world, IMAGE_ID, 1)
    skeleton = helpers.dossier_skeleton(digest)

    assert skeleton["schema"] == "dossier-v2"
    assert skeleton["recon"]["status"] is None or isinstance(
        skeleton["recon"]["status"], str
    )
    assert skeleton["recon"]["delta"] == -4.0
    assert skeleton["proposal"]["add_line_ids"] == [3]
    # Vision judgement is the scout's job, never prefilled.
    assert skeleton["proposal"]["vision_products_confirmed"] is None
    assert skeleton["signals_concurring"] == []
    assert skeleton["verdict_by"] is None
