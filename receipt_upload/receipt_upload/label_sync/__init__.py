"""Cross-environment ReceiptWordLabel reconciliation (dev <-> prod).

Two operations, both gated (dry-run by default) and backed up:

* ``union`` — propagate a VALID label from one env to the other for words that
  exist in BOTH tables, but ONLY onto a word that has no VALID label yet. Never
  overrides an existing label and never creates a second VALID label. INVALID
  markers are deliberate and are never moved.
* ``single_valid`` — find/report words carrying more than one VALID label so
  they can be resolved down to exactly one.
"""

from receipt_upload.label_sync.union import (
    LabelAdd,
    apply_union,
    find_multivalid,
    load_labels_by_word,
    plan_union,
)

__all__ = [
    "LabelAdd",
    "apply_union",
    "find_multivalid",
    "load_labels_by_word",
    "plan_union",
]
