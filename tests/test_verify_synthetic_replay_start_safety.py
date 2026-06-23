"""Tests for real-infra synthetic replay start safety."""

import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest


def _load_replay_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify_synthetic_replay.py"
    )
    spec = importlib.util.spec_from_file_location(
        "verify_synthetic_replay_start_safety_for_test",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _valid_args(**overrides):
    values = {
        "confirm_cost_ack": True,
        "limit": 3,
        "instance_count": 1,
        "use_spot": True,
        "max_runtime_hours": 1,
        "epochs": 1,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("confirm_cost_ack", False, "--confirm-cost-ack"),
        ("limit", None, "--limit"),
        ("limit", 4, "--limit"),
        ("instance_count", 2, "--instance-count"),
        ("use_spot", False, "managed spot"),
        ("max_runtime_hours", 2, "--max-runtime-hours"),
        ("epochs", 2, "--epochs"),
    ],
)
def test_start_validation_rejects_uncapped_replay(field, value, message):
    module = _load_replay_module()

    with pytest.raises(RuntimeError, match=message):
        module.validate_start_args(_valid_args(**{field: value}))


def test_start_validation_accepts_one_shot_smoke_caps():
    module = _load_replay_module()

    module.validate_start_args(_valid_args())
