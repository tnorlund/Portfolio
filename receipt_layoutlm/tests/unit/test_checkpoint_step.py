"""Tests for _checkpoint_step numeric ordering."""

import pytest

from receipt_layoutlm.trainer import _checkpoint_step


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/tmp/job/checkpoint-100/trainer_state.json", 100),
        ("/tmp/job/checkpoint-950/trainer_state.json", 950),
        ("/tmp/job/checkpoint-1000/trainer_state.json", 1000),
        ("/tmp/job/checkpoint-24354/trainer_state.json", 24354),
        ("no-checkpoint-here.json", 0),
    ],
)
def test_checkpoint_step_extracts_number(path: str, expected: int) -> None:
    assert _checkpoint_step(path) == expected


def test_checkpoint_step_sorts_numerically() -> None:
    """Verify checkpoint-950 sorts before checkpoint-1000 (not after, as
    lexicographic ordering would produce)."""
    paths = [
        "/tmp/job/checkpoint-950/trainer_state.json",
        "/tmp/job/checkpoint-1000/trainer_state.json",
        "/tmp/job/checkpoint-24354/trainer_state.json",
        "/tmp/job/checkpoint-100/trainer_state.json",
    ]
    result = sorted(paths, key=_checkpoint_step)
    assert result == [
        "/tmp/job/checkpoint-100/trainer_state.json",
        "/tmp/job/checkpoint-950/trainer_state.json",
        "/tmp/job/checkpoint-1000/trainer_state.json",
        "/tmp/job/checkpoint-24354/trainer_state.json",
    ]
