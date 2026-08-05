"""A run may skip the frozen validation split, but never silently.

``val_keys_s3: null`` used to be indistinguishable from a frozen-split run in
every artifact the run produced, so a ``val_f1`` computed on a run-local random
split read exactly like one computed on the shared split. These lock down both
halves of the fix: the run fails to start unless the choice is explicit, and
when it is explicit the result is stamped non-comparable everywhere.
"""

import pytest

from receipt_layoutlm.config import DataConfig
from receipt_layoutlm.exceptions import UnfrozenValidationSplitError

FROZEN = "s3://bucket/splits/adversarial_val_keys_v2_20260708.json"


def _cfg(**kwargs) -> DataConfig:
    return DataConfig(dynamo_table_name="ReceiptsTable-test", **kwargs)


def test_frozen_split_validates_and_is_comparable():
    cfg = _cfg(val_keys_s3=FROZEN)

    cfg.validate_comparability()  # does not raise

    assert cfg.metrics_comparable is True
    stamp = cfg.comparability()
    assert stamp["comparable"] is True
    assert stamp["val_keys_s3"] == FROZEN
    assert stamp["val_split_source"] == "frozen_val_keys_s3"


def test_missing_frozen_split_fails_fast():
    cfg = _cfg()

    with pytest.raises(UnfrozenValidationSplitError) as excinfo:
        cfg.validate_comparability()

    message = str(excinfo.value)
    # The message must name both escape routes, or it just blocks work.
    assert "--val-keys-s3" in message
    assert "--no-frozen-val" in message
    assert "not comparable" in message


def test_unfrozen_split_error_is_catchable_as_value_error():
    """Callers that already handle config ValueErrors keep working."""
    with pytest.raises(ValueError):
        _cfg().validate_comparability()


def test_explicit_opt_out_runs_but_is_stamped_non_comparable():
    cfg = _cfg(allow_unfrozen_val=True)

    cfg.validate_comparability()  # does not raise

    assert cfg.metrics_comparable is False
    stamp = cfg.comparability()
    assert stamp["comparable"] is False
    assert stamp["val_keys_s3"] is None
    assert stamp["val_split_source"] == "seeded_random_split"
    assert "no frozen validation split" in stamp["reason"]


def test_opt_out_does_not_downgrade_a_run_that_has_a_frozen_split():
    """The flag is an escape hatch, not an override."""
    cfg = _cfg(val_keys_s3=FROZEN, allow_unfrozen_val=True)

    assert cfg.metrics_comparable is True
    assert cfg.comparability()["comparable"] is True


def test_defaults_require_an_explicit_choice():
    """A brand-new DataConfig must not quietly be allowed to train."""
    with pytest.raises(UnfrozenValidationSplitError):
        _cfg().validate_comparability()
