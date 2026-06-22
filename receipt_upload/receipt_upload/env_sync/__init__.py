"""Cross-environment record migration (dev <-> prod).

Brings each table up to the union of receipts that exist in either env, by
copying genuinely-new records (and their S3 objects) from one env to the other.
Additive and idempotent: a receipt is migrated only when its exact key is absent
in the target, so re-runs are safe and nothing is overwritten.
"""

from receipt_upload.env_sync.plan import (
    MigrationPlan,
    build_plan,
    remap_bucket,
)

__all__ = ["MigrationPlan", "build_plan", "remap_bucket"]
