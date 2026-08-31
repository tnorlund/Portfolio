"""Factory singleton for shared label-evaluator resources (#1050)."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    from components.shared_label_evaluator_resources import (
        _reset_shared_label_evaluator_resources_for_tests,
    )

    _reset_shared_label_evaluator_resources_for_tests()
    yield
    _reset_shared_label_evaluator_resources_for_tests()


def test_create_shared_label_evaluator_resources_is_idempotent(monkeypatch):
    """A second factory call must reuse the instance, not re-register the URN."""
    import components.shared_label_evaluator_resources as mod

    constructed: list[object] = []

    class FakeShared:
        def __init__(self, name, *, opts=None):
            self.name = name
            self.opts = opts
            constructed.append(self)

    monkeypatch.setattr(mod, "SharedLabelEvaluatorResources", FakeShared)
    monkeypatch.setattr(
        mod.pulumi, "get_stack", lambda: "dev"
    )

    first = mod.create_shared_label_evaluator_resources()
    second = mod.create_shared_label_evaluator_resources(opts=MagicMock())

    assert first is second
    assert len(constructed) == 1
    assert first.name == "label-evaluator-dev"
