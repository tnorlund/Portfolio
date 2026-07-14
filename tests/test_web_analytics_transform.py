"""Tests for the atomic web_events partition rebuild."""

# Pytest injects fixtures through arguments with the fixture's declared name.
# pylint: disable=redefined-outer-name

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest


@pytest.fixture
def transform_module(monkeypatch):
    """Load the Lambda handler without constructing real AWS clients."""
    clients = {
        "athena": Mock(),
        "glue": Mock(),
        "s3": Mock(),
    }
    monkeypatch.setenv("CURATED_BUCKET", "curated-bucket")
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **_kwargs: clients[service],
    )
    path = (
        Path(__file__).parents[1]
        / "infra/components/web_analytics/transform_lambda/handler.py"
    )
    spec = importlib.util.spec_from_file_location(
        "web_analytics_transform_handler_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_partition_swaps_only_after_unload(
    transform_module, monkeypatch
):
    """An existing complete partition remains registered until staging ends."""
    dt = "2026-07-14"
    previous = f"s3://curated-bucket/web_events/dt={dt}/"
    staged_prefix = f"staging/web_events/dt={dt}/run={'a' * 32}/"
    staged = f"s3://curated-bucket/{staged_prefix}"
    calls = []

    transform = transform_module
    monkeypatch.setattr(transform, "_partition_location", lambda _dt: previous)
    monkeypatch.setattr(
        transform.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="a" * 32),
    )
    monkeypatch.setattr(
        transform,
        "_cleanup_partition_objects",
        lambda _dt, active, retired_location=None: calls.append(
            ("cleanup", active, retired_location)
        ),
    )
    monkeypatch.setattr(
        transform,
        "_run",
        lambda sql, _deadline: calls.append(("sql", sql)),
    )

    assert transform._rebuild_partition(dt, 123.0) == dt

    assert calls[0] == ("cleanup", previous, None)
    assert calls[1][0] == "sql"
    assert calls[1][1].startswith("UNLOAD (")
    assert staged in calls[1][1]
    assert calls[2] == (
        "sql",
        f"ALTER TABLE portfolio_analytics.web_events PARTITION (dt='{dt}') "
        f"SET LOCATION '{staged}'",
    )
    assert calls[3] == ("cleanup", staged, previous)
    assert "DROP" not in calls[2][1]


def test_first_partition_is_added_only_after_unload(
    transform_module, monkeypatch
):
    """A missing partition is registered only after its files are complete."""
    dt = "2026-07-14"
    transform = transform_module
    calls = []
    monkeypatch.setattr(transform, "_partition_location", lambda _dt: None)
    monkeypatch.setattr(
        transform.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="b" * 32),
    )
    monkeypatch.setattr(
        transform,
        "_cleanup_partition_objects",
        lambda _dt, active, retired_location=None: calls.append(
            ("cleanup", active, retired_location)
        ),
    )
    monkeypatch.setattr(
        transform,
        "_run",
        lambda sql, _deadline: calls.append(("sql", sql)),
    )

    transform._rebuild_partition(dt, 123.0)

    assert calls[0][1].startswith("UNLOAD (")
    assert " ADD PARTITION " in calls[1][1]
    assert calls[2][0] == "cleanup"


def test_failed_unload_never_changes_partition(transform_module, monkeypatch):
    """Partial staging files are deleted while the old partition stays live."""
    transform = transform_module
    dt = "2026-07-14"
    previous = f"s3://curated-bucket/web_events/dt={dt}/"
    sql_calls = []
    deleted = []
    monkeypatch.setattr(transform, "_partition_location", lambda _dt: previous)
    monkeypatch.setattr(
        transform.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="c" * 32),
    )
    monkeypatch.setattr(
        transform,
        "_cleanup_partition_objects",
        lambda _dt, _active, retired_location=None: None,
    )

    def fail_unload(sql, _deadline):
        sql_calls.append(sql)
        raise RuntimeError("UNLOAD failed")

    monkeypatch.setattr(transform, "_run", fail_unload)
    monkeypatch.setattr(
        transform,
        "_delete_prefix",
        lambda prefix, keep_prefix=None: deleted.append(prefix),
    )

    with pytest.raises(RuntimeError, match="UNLOAD failed"):
        transform._rebuild_partition(dt, 123.0)

    assert len(sql_calls) == 1
    assert sql_calls[0].startswith("UNLOAD (")
    assert deleted == [f"staging/web_events/dt={dt}/run={'c' * 32}/"]


def test_uncertain_swap_result_preserves_staging(
    transform_module, monkeypatch
):
    """A swap timeout cannot delete a prefix Athena may have made active."""
    transform = transform_module
    dt = "2026-07-14"
    previous = f"s3://curated-bucket/web_events/dt={dt}/"
    sql_calls = []
    deleted = []
    monkeypatch.setattr(transform, "_partition_location", lambda _dt: previous)
    monkeypatch.setattr(
        transform.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="d" * 32),
    )
    monkeypatch.setattr(
        transform,
        "_cleanup_partition_objects",
        lambda _dt, _active, retired_location=None: None,
    )

    def timeout_during_swap(sql, _deadline):
        sql_calls.append(sql)
        if len(sql_calls) == 2:
            raise TimeoutError("swap result unknown")

    monkeypatch.setattr(transform, "_run", timeout_during_swap)
    monkeypatch.setattr(
        transform,
        "_delete_prefix",
        lambda prefix, keep_prefix=None: deleted.append(prefix),
    )

    with pytest.raises(TimeoutError, match="swap result unknown"):
        transform._rebuild_partition(dt, 123.0)

    assert sql_calls[0].startswith("UNLOAD (")
    assert " SET LOCATION " in sql_calls[1]
    assert not deleted


def test_unload_schema_matches_non_partition_columns(transform_module):
    """The partition key is metadata-only; enrichment and dedup stay intact."""
    sql = transform_module._unload_sql(
        "2026-07-14",
        "s3://curated-bucket/staging/web_events/dt=2026-07-14/run=x/",
    )

    assert "cast(date as varchar) AS dt" not in sql
    assert "g.country AS country" in sql
    assert "g.city AS city" in sql
    assert "g.org AS org" in sql
    assert "g.asn AS asn" in sql
    assert "AS is_hosting" in sql
    assert "ELSE request_id END" in sql
    assert "LEFT JOIN portfolio_analytics.ip_geo" in sql
    assert "format = 'PARQUET', compression = 'SNAPPY'" in sql


def test_delete_prefix_preserves_active_staging_run(transform_module):
    """Cleanup removes superseded runs without touching the active files."""
    transform = transform_module
    root = "staging/web_events/dt=2026-07-14/"
    active = f"{root}run=active/"
    paginator = transform._s3.get_paginator.return_value
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": f"{active}data.parquet"},
                {"Key": f"{root}run=old/data.parquet"},
            ]
        }
    ]

    transform._delete_prefix(root, keep_prefix=active)

    transform._s3.delete_objects.assert_called_once_with(
        Bucket="curated-bucket",
        Delete={"Objects": [{"Key": f"{root}run=old/data.parquet"}]},
    )


def test_cleanup_includes_previous_custom_partition_location(
    transform_module, monkeypatch
):
    """Cleanup includes a prior location outside the known layouts."""
    dt = "2026-07-14"
    active_prefix = f"staging/web_events/dt={dt}/run=active/"
    active = f"s3://curated-bucket/{active_prefix}"
    retired = f"s3://curated-bucket/older-layout/dt={dt}/run=old/"
    calls = []
    monkeypatch.setattr(
        transform_module,
        "_delete_prefix",
        lambda prefix, keep_prefix=None: calls.append((prefix, keep_prefix)),
    )

    transform_module._cleanup_partition_objects(
        dt,
        active,
        retired_location=retired,
    )

    assert (f"older-layout/dt={dt}/run=old/", active_prefix) in calls
    assert all(keep == active_prefix for _prefix, keep in calls)


def test_backfill_range_still_expands_inclusively(transform_module):
    """Atomic writes do not change the explicit backfill event contract."""
    assert transform_module._target_dates(
        {"start": "2026-07-12", "end": "2026-07-14"}
    ) == ["2026-07-12", "2026-07-13", "2026-07-14"]
