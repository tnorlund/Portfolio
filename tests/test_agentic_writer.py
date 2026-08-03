"""P3 writer (scripts/agentic_writer.py) against a mock receipt world.

The writer is exercised with the REAL guard path — the actual
``extend_items_section_impl`` from scripts/receipt_mcp_server.py,
loaded exactly as the writer loads it — over a mutable stub dynamo
client, so these tests cover the full contract: confirm-after-apply,
halt-on-divergence, single-flight locking, prod-table refusal, and
T1 approval gating.

Stub world (same as tests/test_receipt_mcp_line_item_tools.py):
lines 1-4 = APPLES 3.00 / BANANAS 2.00 / ORANGES 4.00 / JUNK 50.00;
summary subtotal 9.00, tax 0.72, grand_total 9.72. ITEMS={1,2} is a
mismatch (5 vs 9); extending with line 3 is an exact match.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("receipt_dynamo")
pytest.importorskip("receipt_upload")

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


writer = _load(
    "agentic_writer_test", REPO_ROOT / "scripts" / "agentic_writer.py"
)
helpers = _load(
    "agentic_helpers_test",
    REPO_ROOT / "scripts" / "agentic_triage_helpers.py",
)
mcp_server = helpers.load_mcp_server()

PRICES = {1: ("APPLES", 3.00), 2: ("BANANAS", 2.00), 3: ("ORANGES", 4.00)}


def _word(line_id, word_id, text, x, y):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y - 0.01, "width": 0.1, "height": 0.02},
    )


def _summary_record():
    from receipt_dynamo.entities.receipt_summary import (
        MonetaryTotals,
        ReceiptSummary,
    )
    from receipt_dynamo.entities.receipt_summary_record import (
        ReceiptSummaryRecord,
    )

    return ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Test Mart",
            totals=MonetaryTotals(grand_total=9.72, subtotal=9.00, tax=0.72),
            item_count=3,
        ),
        timestamp_computed="2026-01-01T00:00:00+00:00",
    )


def _items_section(line_ids):
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    return ReceiptSection(
        receipt_id=1,
        image_id=IMAGE_ID,
        section_type="ITEMS",
        line_ids=list(line_ids),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model_source="section-seed-v0",
        validation_status="VALID",
    )


class MutableWorld:
    """Stub dynamo client whose stream stage can be turned off.

    ``update_receipt_summary`` (the guarded apply's final step) plays
    the role of the DynamoDB stream: when ``stream_works`` it
    regenerates the line items from the current ITEMS section, which
    is exactly what the writer's confirm step waits for.
    """

    def __init__(self, stream_works=True):
        self.stream_works = stream_works
        self.sections = [_items_section([1, 2])]
        self.summary_record = _summary_record()
        self.line_items = self._items_for(self.sections[0].line_ids)
        self.summary_updates = []
        self.section_updates = []

    def _items_for(self, line_ids):
        items = []
        for index, lid in enumerate(sorted(line_ids)):
            if lid not in PRICES:
                continue
            name, price = PRICES[lid]
            items.append(
                SimpleNamespace(
                    image_id=IMAGE_ID,
                    receipt_id=1,
                    item_index=index,
                    name=name,
                    price=str(price),
                    quantity=None,
                    unit_price=None,
                    is_discount=False,
                    line_ids=[lid],
                    name_quality="ok",
                    merchant_name="Test Mart",
                    reconciliation_status=None,
                    extractor_version="line-items-blocks-v2",
                )
            )
        return items

    def get_receipt_details(self, image_id, receipt_id):
        return SimpleNamespace(
            lines=[SimpleNamespace(line_id=i) for i in (1, 2, 3, 4)],
            words=[
                _word(1, 1, "APPLES", 0.05, 0.10),
                _word(1, 2, "3.00", 0.80, 0.10),
                _word(2, 1, "BANANAS", 0.05, 0.15),
                _word(2, 2, "2.00", 0.80, 0.15),
                _word(3, 1, "ORANGES", 0.05, 0.20),
                _word(3, 2, "4.00", 0.80, 0.20),
                _word(4, 1, "JUNKTHING", 0.05, 0.25),
                _word(4, 2, "50.00", 0.80, 0.25),
            ],
        )

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return self.sections

    def get_receipt_summary(self, image_id, receipt_id):
        return self.summary_record

    def get_receipt_line_items_from_receipt(self, image_id, receipt_id):
        return self.line_items

    def update_receipt_section(self, section):
        self.section_updates.append(section)
        self.sections = [
            section if s.section_type == "ITEMS" else s for s in self.sections
        ]

    def update_receipt_summary(self, record):
        self.summary_updates.append(record)
        if self.stream_works:
            items_section = next(
                s for s in self.sections if s.section_type == "ITEMS"
            )
            self.line_items = self._items_for(items_section.line_ids)


def _verdict(tier="T0", golden=False, group_id="test-mart::h"):
    return {
        "pass_id": "p1",
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "tier": tier,
        "reason": "auto-extension",
        "golden": golden,
        "group_id": group_id,
        "merchant": "Test Mart",
        "mode": "H-clean-extension",
        "proposal": {
            "add_line_ids": [3],
            "contiguous": True,
            "verified": True,
            "before": {"status": "mismatch", "delta": -4.0},
            "after": {"status": "match", "delta": 0.0},
            "vision_products_confirmed": True,
        },
        "verdict_by": "agent:p1",
    }


def _write_pass(tmp_path, entries, approvals=None, pass_id="p1"):
    verdicts_dir = tmp_path / "verdicts"
    approvals_dir = tmp_path / "approvals"
    verdicts_dir.mkdir(exist_ok=True)
    approvals_dir.mkdir(exist_ok=True)
    with (verdicts_dir / f"{pass_id}.jsonl").open("w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    if approvals is not None:
        (approvals_dir / f"{pass_id}.json").write_text(json.dumps(approvals))
    return verdicts_dir, approvals_dir


def _run(tmp_path, world, entries, approvals=None, apply=True, **kwargs):
    verdicts_dir, approvals_dir = _write_pass(tmp_path, entries, approvals)
    return writer.run_apply(
        "p1",
        client=world,
        mcp_server=mcp_server,
        table_name=writer.DEV_TABLE,
        apply=apply,
        verdicts_dir=verdicts_dir,
        approvals_dir=approvals_dir,
        freeze_dir=kwargs.pop("freeze_dir", tmp_path / "freeze"),
        lock_path=tmp_path / "writer.lock",
        poll_attempts=kwargs.pop("poll_attempts", 3),
        poll_interval=0.0,
        sleep=lambda _s: None,
        **kwargs,
    )


def _freeze(tmp_path, marker):
    freeze_dir = tmp_path / "freeze"
    freeze_dir.mkdir(exist_ok=True)
    (freeze_dir / marker).write_text("audit disagreement")
    return freeze_dir


# --------------------------------------------------------------------------
# Confirm-after-apply happy path
# --------------------------------------------------------------------------


def test_confirm_after_apply_happy_path(tmp_path, capsys):
    world = MutableWorld(stream_works=True)
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 0

    summary = json.loads(capsys.readouterr().out)
    (record,) = summary["applied"]
    assert record["applied"] is True
    assert record["confirmed"] is True
    assert record["predicted_delta"] == 0.0
    assert record["observed_delta"] == 0.0

    items_section = next(
        s for s in world.sections if s.section_type == "ITEMS"
    )
    assert sorted(items_section.line_ids) == [1, 2, 3]
    # Guarded path preserved the status; provenance suffix stamped on
    # top of the guard's own marker.
    assert items_section.validation_status == "VALID"
    assert items_section.model_source.endswith("+agentic-vision-v1")
    assert "mcp-extend-items-v1" in items_section.model_source
    # Summary timestamp was bumped (stream trigger).
    assert len(world.summary_updates) == 1
    # Lock released.
    assert not (tmp_path / "writer.lock").exists()


def test_dry_run_default_writes_nothing(tmp_path, capsys):
    world = MutableWorld()
    rc = _run(tmp_path, world, [_verdict()], apply=False)
    assert rc == 0
    assert world.summary_updates == []
    assert world.section_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "dry-run"
    assert summary["applied"][0]["applied"] is False


# --------------------------------------------------------------------------
# Halt on divergence / confirmation timing (pass-20260803 lessons)
# --------------------------------------------------------------------------


def test_halt_reports_timeout_when_stream_never_lands(tmp_path, capsys):
    # Observed delta stays at the PRE-APPLY value: the stream simply
    # has not landed, which refutes nothing — the halt must say
    # confirmation-timeout, not delta-divergence (the 112ed08b
    # misattribution).
    world = MutableWorld(stream_works=False)  # stream never regenerates
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 2

    report_path = tmp_path / "verdicts" / "p1.divergence.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["divergence"]["kind"] == "confirmation-timeout"
    assert report["divergence"]["predicted_delta"] == 0.0
    assert report["divergence"]["before_delta"] == -4.0
    assert report["divergence"]["observed_delta"] == -4.0
    assert not (tmp_path / "writer.lock").exists()


class _DelayedStreamWorld(MutableWorld):
    """Stream regeneration lands only after ``lag_reads`` re-reads.

    Models the pass-20260803 reality: ~33s of stream/SQS latency
    between the summary bump and the recompute landing, during which
    the stored rows still show the pre-apply delta.
    """

    def __init__(self, lag_reads):
        super().__init__(stream_works=False)
        self.lag_reads = lag_reads
        self._reads_since_apply = None

    def update_receipt_summary(self, record):
        super().update_receipt_summary(record)
        self._reads_since_apply = 0

    def get_receipt_line_items_from_receipt(self, image_id, receipt_id):
        if self._reads_since_apply is not None:
            self._reads_since_apply += 1
            if self._reads_since_apply > self.lag_reads:
                items_section = next(
                    s for s in self.sections if s.section_type == "ITEMS"
                )
                self.line_items = self._items_for(items_section.line_ids)
        return self.line_items


def test_late_stream_still_confirms_within_window(tmp_path, capsys):
    # 112ed08b regression: while observed == pre-apply delta the writer
    # must keep polling, and a stream landing late (but inside the
    # window) is a clean confirmation, not a halt.
    world = _DelayedStreamWorld(lag_reads=3)
    rc = _run(tmp_path, world, [_verdict()], poll_attempts=6)
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"][0]["confirmed"] is True
    assert summary["applied"][0]["observed_delta"] == 0.0


class _WrongRegenWorld(MutableWorld):
    """Stream lands on a delta that is neither before nor predicted."""

    def update_receipt_summary(self, record):
        self.summary_updates.append(record)
        # Regenerate to APPLES only: delta -6.00 (before was -4.00,
        # predicted 0.0) — a true divergence.
        self.line_items = self._items_for([1])


def test_true_divergence_halts_immediately(tmp_path):
    world = _WrongRegenWorld()
    rc = _run(tmp_path, world, [_verdict()], poll_attempts=10)
    assert rc == 2
    report = json.loads(
        (tmp_path / "verdicts" / "p1.divergence.json").read_text()
    )
    assert report["divergence"]["kind"] == "delta-divergence"
    assert report["divergence"]["observed_delta"] == -6.0
    assert report["divergence"]["before_delta"] == -4.0
    # Fail-fast: a genuinely wrong delta must not wait out the window.
    assert report["divergence"]["poll_attempts"] == 1


def test_halt_when_guard_refuses_at_write_time(tmp_path):
    # Adjudicated against a stale world: line 3 is already inside the
    # section, so the write-time dry run refuses and the run halts
    # without writing anything.
    world = MutableWorld()
    world.sections = [_items_section([1, 2, 3])]
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 2
    report = json.loads(
        (tmp_path / "verdicts" / "p1.divergence.json").read_text()
    )
    assert report["divergence"]["kind"] == "guard-refusal"
    assert world.summary_updates == []


def test_divergence_halts_remaining_entries(tmp_path):
    world = MutableWorld(stream_works=False)
    second = _verdict()
    second["image_id"] = IMAGE_ID  # same world; never reached
    rc = _run(tmp_path, world, [_verdict(), second])
    assert rc == 2
    report = json.loads(
        (tmp_path / "verdicts" / "p1.divergence.json").read_text()
    )
    assert len(report["remaining_unapplied"]) == 1


# --------------------------------------------------------------------------
# Lock contention
# --------------------------------------------------------------------------


def test_lock_contention_refuses_second_writer(tmp_path):
    lock_path = tmp_path / "writer.lock"
    with writer.WriterLock(lock_path):
        world = MutableWorld()
        verdicts_dir, approvals_dir = _write_pass(tmp_path, [_verdict()])
        with pytest.raises(writer.WriterLockedError):
            writer.run_apply(
                "p1",
                client=world,
                mcp_server=mcp_server,
                table_name=writer.DEV_TABLE,
                apply=True,
                verdicts_dir=verdicts_dir,
                approvals_dir=approvals_dir,
                lock_path=lock_path,
                sleep=lambda _s: None,
            )
        assert world.summary_updates == []
    # Releasing the first lock removes the file.
    assert not lock_path.exists()


# --------------------------------------------------------------------------
# Prod-table refusal
# --------------------------------------------------------------------------


def test_prod_table_refused(tmp_path):
    with pytest.raises(writer.ProdTableRefusedError):
        writer.run_apply(
            "p1",
            client=MutableWorld(),
            mcp_server=mcp_server,
            table_name="ReceiptsTable-d7ff76a",
            apply=True,
            verdicts_dir=tmp_path,
            approvals_dir=tmp_path,
            lock_path=tmp_path / "writer.lock",
        )


def test_prod_table_refused_from_cli(capsys):
    rc = writer.main(
        [
            "apply",
            "--pass-id",
            "p1",
            "--table",
            "ReceiptsTable-d7ff76a",
        ]
    )
    assert rc == 1
    assert "refusing table" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Approval gating
# --------------------------------------------------------------------------


def test_t1_without_approval_is_skipped(tmp_path, capsys):
    world = MutableWorld()
    rc = _run(
        tmp_path,
        world,
        [_verdict(tier="T1")],
        approvals={"approved_groups": []},
    )
    assert rc == 0
    assert world.summary_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"] == []
    assert summary["skipped"][0]["skip_reason"] == "t1-group-not-approved"


def test_t1_with_group_approval_is_applied(tmp_path, capsys):
    world = MutableWorld()
    rc = _run(
        tmp_path,
        world,
        [_verdict(tier="T1")],
        approvals={"approved_groups": ["test-mart::h"]},
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"][0]["confirmed"] is True


def test_golden_never_applied_even_when_group_approved(tmp_path, capsys):
    world = MutableWorld()
    rc = _run(
        tmp_path,
        world,
        [_verdict(tier="T1", golden=True)],
        approvals={"approved_groups": ["test-mart::h"]},
    )
    assert rc == 0
    assert world.summary_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"][0]["skip_reason"] == "golden-never-auto-applied"


def test_t2_never_applied(tmp_path, capsys):
    world = MutableWorld()
    rc = _run(tmp_path, world, [_verdict(tier="T2")])
    assert rc == 0
    assert world.summary_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"][0]["skip_reason"] == "tier-T2-not-writable"


# --------------------------------------------------------------------------
# Freeze markers: the writer honors them too, re-checked before every
# write (a mid-session audit freeze must stop the writer immediately,
# not merely the adjudicator's next run).
# --------------------------------------------------------------------------


def test_frozen_tier_skips_t0_entry(tmp_path, capsys):
    _freeze(tmp_path, "T0")
    world = MutableWorld()
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 0
    assert world.summary_updates == []
    assert world.section_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"] == []
    assert summary["skipped"][0]["skip_reason"] == "frozen:T0"


def test_frozen_mode_class_skips_entry(tmp_path, capsys):
    _freeze(tmp_path, "H")  # _verdict() mode is H-clean-extension
    world = MutableWorld()
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 0
    assert world.summary_updates == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"][0]["skip_reason"] == "frozen:H"


def test_unrelated_freeze_does_not_block(tmp_path, capsys):
    _freeze(tmp_path, "B")  # different mode class entirely
    world = MutableWorld()
    rc = _run(tmp_path, world, [_verdict()])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"][0]["confirmed"] is True


class _FreezeMidRunWorld(MutableWorld):
    """The audit deck writes a freeze marker while the writer runs.

    ``update_receipt_summary`` is the last write of the guarded apply,
    so dropping the marker there lands it exactly between entry #1's
    apply and entry #2's — the writer must re-check per receipt and
    skip #2.
    """

    def __init__(self, tmp_path):
        super().__init__()
        self._tmp_path = tmp_path

    def update_receipt_summary(self, record):
        super().update_receipt_summary(record)
        _freeze(self._tmp_path, "T0")


def test_freeze_appearing_mid_run_stops_later_entries(tmp_path, capsys):
    world = _FreezeMidRunWorld(tmp_path)
    # Two identical T0 verdicts: without the per-receipt re-check the
    # second would reach the guard (and diverge); with it, the freeze
    # written during the first apply skips the second cleanly.
    rc = _run(tmp_path, world, [_verdict(), _verdict()])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["applied"]) == 1
    assert summary["applied"][0]["confirmed"] is True
    assert summary["skipped"][0]["skip_reason"] == "frozen:T0"
    assert len(world.summary_updates) == 1


# --------------------------------------------------------------------------
# Duplicate retirement: approval gate + backup-first
# --------------------------------------------------------------------------


class _FakeS3:
    def __init__(self):
        self.copies = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                return [
                    {
                        "Contents": [
                            {"Key": f"{Prefix}.png"},
                            {"Key": f"{Prefix}_cluster.png"},
                        ]
                    }
                ]

        _ = fake
        return _Paginator()

    def copy_object(self, Bucket, Key, CopySource):
        self.copies.append((Bucket, Key, CopySource))


class _RetireWorld(MutableWorld):
    def __init__(self):
        super().__init__()
        self.deleted = []

    def get_image_details(self, image_id):
        return SimpleNamespace(
            images=[SimpleNamespace(image_id=image_id)],
            lines=[SimpleNamespace(line_id=1)],
        )

    def delete_image_details(self, image_id):
        self.deleted.append(image_id)
        return {"IMAGES": 1, "LINES": 1}


def _group_file(tmp_path, group_id="dup-grp-1"):
    path = tmp_path / "group.json"
    path.write_text(
        json.dumps(
            {
                "group_id": group_id,
                "keep": {"image_id": "keeper", "receipt_id": 1},
                "retire": [{"image_id": IMAGE_ID, "receipt_id": 1}],
            }
        )
    )
    return path


def _approvals_file(tmp_path, retirements):
    path = tmp_path / "approvals.json"
    path.write_text(json.dumps({"t2_retirements": retirements}))
    return path


def test_retire_refused_without_t2_signoff(tmp_path, capsys):
    world = _RetireWorld()
    s3 = _FakeS3()
    rc = writer.run_retire(
        _group_file(tmp_path),
        client=world,
        mcp_server=mcp_server,
        s3_client=s3,
        table_name=writer.DEV_TABLE,
        raw_bucket="raw-bucket",
        approvals_file=_approvals_file(tmp_path, []),
        apply=True,
        backup_dir=tmp_path / "backups",
        lock_path=tmp_path / "writer.lock",
    )
    assert rc == 1
    assert world.deleted == []
    assert s3.copies == []
    assert "no T2 sign-off" in capsys.readouterr().err


def test_retire_backs_up_both_stores_before_delete(tmp_path, capsys):
    world = _RetireWorld()
    s3 = _FakeS3()
    rc = writer.run_retire(
        _group_file(tmp_path),
        client=world,
        mcp_server=mcp_server,
        s3_client=s3,
        table_name=writer.DEV_TABLE,
        raw_bucket="raw-bucket",
        approvals_file=_approvals_file(tmp_path, ["dup-grp-1"]),
        apply=True,
        backup_dir=tmp_path / "backups",
        lock_path=tmp_path / "writer.lock",
    )
    assert rc == 0
    # Dynamo rows exported locally.
    backups = list((tmp_path / "backups").glob("retired-*.json"))
    assert len(backups) == 1
    exported = json.loads(backups[0].read_text())
    assert exported["image_id"] == IMAGE_ID
    assert exported["row_count"] == 2
    # Raw objects copied server-side into the raw bucket backup prefix.
    assert len(s3.copies) == 2
    bucket, key, source = s3.copies[0]
    assert bucket == "raw-bucket"
    assert key.startswith("rewarp-backups/retired-")
    assert source["Bucket"] == "raw-bucket"
    # And only then the delete ran.
    assert world.deleted == [IMAGE_ID]
    out = json.loads(capsys.readouterr().out)
    assert out["retired"][0]["deletion"]["deleted"] == 2


def test_retire_dry_run_backs_up_but_does_not_delete(tmp_path, capsys):
    world = _RetireWorld()
    s3 = _FakeS3()
    rc = writer.run_retire(
        _group_file(tmp_path),
        client=world,
        mcp_server=mcp_server,
        s3_client=s3,
        table_name=writer.DEV_TABLE,
        raw_bucket="raw-bucket",
        approvals_file=_approvals_file(tmp_path, ["dup-grp-1"]),
        apply=False,
        backup_dir=tmp_path / "backups",
        lock_path=tmp_path / "writer.lock",
    )
    assert rc == 0
    assert world.deleted == []
    out = json.loads(capsys.readouterr().out)
    assert out["retired"][0]["deletion"]["dry_run"] is True


# --------------------------------------------------------------------------
# Simulator fidelity: 112ed08b (Sprouts) shape. OCR split each visual
# item row into a name-only line and a price-only line (15/34, 16/35,
# 17/36); the section is row-anchored, so extending with the two name
# lines widens to their price lines (+9.98). The dry-run prediction
# must equal a Lambda-style recompute (line_item_processor's exact
# word/summary construction) over the section as stored post-apply.
# --------------------------------------------------------------------------


class _SproutsWorld:
    """Row-anchored stub shaped like dev receipt 112ed08b r1."""

    LINES = {
        15: ("ORG SWT POTATO 3LB", 0.10),
        34: ("4.99", 0.10),
        16: ("SEEDLESS LEMONS BAG", 0.15),
        35: ("4.99", 0.15),
        17: ("SHALLOTS", 0.20),
        36: ("0.84", 0.20),
    }

    def __init__(self):
        from receipt_dynamo.entities.receipt_section import ReceiptSection

        self.words = [
            _word(lid, 1, text, 0.80 if text[0].isdigit() else 0.05, y)
            for lid, (text, y) in self.LINES.items()
        ]
        self.sections = [
            ReceiptSection(
                receipt_id=1,
                image_id=IMAGE_ID,
                section_type="ITEMS",
                line_ids=[17, 36],
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                model_source="section-seed-v0",
                validation_status="VALID",
                row_ids=[17],
            )
        ]
        self.rows = [
            SimpleNamespace(row_id=15, line_ids=[15, 34]),
            SimpleNamespace(row_id=16, line_ids=[16, 35]),
            SimpleNamespace(row_id=17, line_ids=[17, 36]),
        ]
        self.summary_updates = []

    def get_receipt_details(self, image_id, receipt_id):
        return SimpleNamespace(
            lines=[SimpleNamespace(line_id=i) for i in self.LINES],
            words=self.words,
        )

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return self.sections

    def get_receipt_rows_from_receipt(self, image_id, receipt_id):
        return self.rows

    def get_receipt_summary(self, image_id, receipt_id):
        from receipt_dynamo.entities.receipt_summary import (
            MonetaryTotals,
            ReceiptSummary,
        )
        from receipt_dynamo.entities.receipt_summary_record import (
            ReceiptSummaryRecord,
        )

        return ReceiptSummaryRecord(
            summary=ReceiptSummary(
                image_id=IMAGE_ID,
                receipt_id=1,
                merchant_name="Sprouts Farmers Market",
                totals=MonetaryTotals(
                    grand_total=10.82, subtotal=None, tax=None
                ),
                item_count=3,
            ),
            timestamp_computed="2026-01-01T00:00:00+00:00",
        )

    def update_receipt_section(self, section):
        self.sections = [
            section if s.section_type == "ITEMS" else s for s in self.sections
        ]

    def update_receipt_summary(self, record):
        self.summary_updates.append(record)


def test_sprouts_row_widening_prediction_matches_lambda_recompute():
    from receipt_upload.line_items.geometry import (
        extract_items,
        reconcile_detailed,
    )

    world = _SproutsWorld()

    dry = writer._run(
        mcp_server.extend_items_section_impl(
            world, IMAGE_ID, 1, [15, 16], dry_run=True
        )
    )
    assert dry.get("verified") is True, dry
    # The 112ed08b shape: adding the two NAME lines widens (via the row
    # anchor) to their price lines, moving -9.98 -> 0.0.
    assert dry["before"]["delta"] == -9.98
    assert dry["after"] == {
        "status": "match",
        "items_sum": 10.82,
        "baseline": 10.82,
        "delta": 0.0,
        "n_items": 3,
        "collapsed_banding": False,
    }
    assert set(dry["extended_line_ids"]) == {15, 16, 17, 34, 35, 36}

    applied = writer._run(
        mcp_server.extend_items_section_impl(
            world, IMAGE_ID, 1, [15, 16], dry_run=False
        )
    )
    assert applied.get("applied") is True

    # Lambda-style recompute over the section AS STORED, using
    # line_item_processor.update_receipt_line_items's exact input
    # construction: word dicts from the word entities and a summary
    # dict from the inner ReceiptSummary attributes.
    stored = next(s for s in world.sections if s.section_type == "ITEMS")
    lambda_words = [
        {
            "line_id": w.line_id,
            "word_id": w.word_id,
            "text": w.text,
            "x": w.bounding_box.get("x", 0.0),
            "y_mid": w.bounding_box.get("y", 0.0)
            + w.bounding_box.get("height", 0.0) / 2,
            "h": w.bounding_box.get("height", 0.0),
        }
        for w in world.words
    ]
    inner = world.get_receipt_summary(IMAGE_ID, 1).summary
    lambda_summary = {
        "subtotal": getattr(inner, "subtotal", None),
        "grand_total": getattr(inner, "grand_total", None),
        "tax": getattr(inner, "tax", None),
    }
    items, _ = extract_items(
        lambda_words,
        {int(x) for x in stored.line_ids},
        summary=lambda_summary,
    )
    recon = reconcile_detailed(
        [i for i in items if not i.get("is_discount")], lambda_summary
    )

    # Parity: what the stream stage will store equals the prediction.
    assert recon.status == dry["after"]["status"]
    assert recon.item_sum == dry["after"]["items_sum"]
    assert round(recon.item_sum - recon.baseline, 2) == dry["after"]["delta"]
    assert len(items) == dry["after"]["n_items"]
