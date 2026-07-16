# Work Order: Add `TRANSACTION_INFO` section type

**Target base:** `codex/d2-measured-rework` @ `5ab0ef75e` (worktree `/Users/tnorlund/.codex/worktrees/9d3c/Portfolio`)
**Patch:** `enum_work/transaction_info.patch` (applies cleanly; `git apply --check` = exit 0)
**Status:** DRAFT ONLY — not applied, not committed. No priors rebuilt, no DynamoDB writes.

---

## 1. Motivation

The 2026-07-16 blind validation of the D2 deterministic section labeler (PR #1145)
found that a large, coherent slice of held-out rows fits none of the ten canonical
`SectionType` values.

- **P1 (holdout section accuracy) = 0.761 → FAIL** against the pre-registered gate.
- **0.846 on emittable rows** — i.e. once the un-modellable rows are excluded, the
  decoder is materially better than the headline number suggests, which localizes
  the miss to a *missing type* rather than a *bad model*.
- **45 / 518 holdout rows** are operator / register / date-time / order-id / invoice
  lines. An independent open-vocabulary labeler, run blind, converged on a single
  name for them: **"Transaction Info"**.
- **Probe evidence:** these rows are not noise — they cluster (ticket#/station/cashier
  trailers, `REG#/TRN#/CSHR#` register metadata, transaction date-time, order/invoice
  ids). Today they are force-fit into `FOOTER`/`SUMMARY`/`PAYMENT`, which both
  depresses P1 and pollutes the neighboring sections' priors.

Adding a first-class `TRANSACTION_INFO` type gives these rows a home so they can be
labeled VALID, measured honestly, and (after a priors rebuild) emitted by the decoder.

## 2. Patch summary

Four files change. The enum is the single source of truth; the two MCP servers and the
entity validator all derive their accept-list from `SectionType`, so only the enum plus
documentation/tests need touching.

| File | Change | Why |
|------|--------|-----|
| `receipt_dynamo/receipt_dynamo/constants.py` | Add `TRANSACTION_INFO = "TRANSACTION_INFO"` to the canonical block of `SectionType`; note it in the class docstring. | Canonical vocabulary. Everything downstream that validates a section type reads `{t.value for t in SectionType}` from here. |
| `receipt_dynamo/tests/unit/test_receipt_section.py` | Add `"TRANSACTION_INFO"` to the `test_canonical_section_types_accepted` parametrize list; docstring "ten" → "eleven". | The one exhaustive per-value acceptance test. Without this it silently under-covers the new value. |
| `scripts/receipt_mcp_server.py` | `create_receipt_section` tool docstring: append `TRANSACTION_INFO` to the enumerated canonical values. | Operator-facing tool text; the label cleanup (below) creates `TRANSACTION_INFO` rows through this tool, so it must advertise the value. Validation itself is enum-derived, so this is documentation only. |
| `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py` | Same docstring append as above. | Deployed Lambda copy of the same tool text. |

### Decoder safety (no code change required)

The semi-Markov decoder (`receipt_upload/receipt_upload/section_assignment.py`) builds
its entire type vocabulary from `global_prior["sections"]` (see `_ordered_sections`,
`assign_feature_sections`) — it never enumerates `SectionType` directly. Consequences:

- It **accepts** `TRANSACTION_INFO` the moment the priors artifact lists it (no
  hardcoded vocabulary to extend).
- It **will not emit** `TRANSACTION_INFO` until then, because a type absent from
  `global_prior["sections"]` is never a candidate state.
- **No `KeyError` on the unseen type:** because the decoder only iterates the types
  already present in the priors, an enum value that is not yet in the priors is simply
  never looked up. The one place a mismatch *would* KeyError is `_ordered_sections`,
  which does `global_prior["section_models"][section]` for each section in
  `global_prior["sections"]`. That is why the priors JSON must be rebuilt atomically
  (§3) — never hand-edit `"sections"` to add `TRANSACTION_INFO` without a matching
  `section_models` entry.

## 3. Retraining path (post-merge, separate change)

This work order does **not** perform any of the following. They are the ordered
prerequisites before `TRANSACTION_INFO` can be emitted or re-evaluated.

1. **Land the enum patch** so `TRANSACTION_INFO` is a valid, creatable, canonical type
   (`create_receipt_section` uses `canonical_only=True`, which now permits it).
2. **Label cleanup in dev:** relabel the qualifying operator/register/date-time/
   order-id/invoice rows as `TRANSACTION_INFO` and mark them **VALID**. Priors are built
   from VALID-only sections, so until VALID `TRANSACTION_INFO` sections exist, a rebuild
   produces no `TRANSACTION_INFO` model and nothing changes.
3. **Rebuild priors:** run `scripts/build_section_order_priors.py` (dev, read-only from
   Dynamo) to regenerate
   `receipt_upload/receipt_upload/assets/section_order_priors_v2.json`. This adds
   `TRANSACTION_INFO` to `"sections"` **and** its `section_models` entry together,
   keeping the decoder invariant intact. Commit the regenerated artifact.
4. **Refresh golden fixtures** if the rebuilt priors change decoder output on the
   determinism goldens (`receipt_upload/tests/fixtures/upload_determinism_golden.json`,
   `section_assignment_real_golden.json`). These are decoder *outputs*, not vocab lists;
   leave them untouched unless the rebuild actually shifts predictions.
5. **CRITICAL — the 22-receipt holdout is SPENT.** It was consumed by the 2026-07-16
   blind validation. Per the pre-registered protocol §7, no re-evaluation may reuse it.
   Before any re-scoring of the `TRANSACTION_INFO`-aware model, **declare a NEW holdout**
   (fresh manifest, recorded before priors are rebuilt / before any tuning) and pass it
   to `build_section_order_priors.py --exclude-manifest` so training never sees it.

## 4. Acceptance criteria

- `git apply --check enum_work/transaction_info.patch` passes on `5ab0ef75e`. *(verified)*
- `SectionType.TRANSACTION_INFO` exists and equals `"TRANSACTION_INFO"`.
- `ReceiptSection(section_type="TRANSACTION_INFO", ...)` constructs (entity validator
  derives `valid_lns` from the enum).
- `create_receipt_section` (both MCP servers) accepts `TRANSACTION_INFO` with
  `canonical_only=True` and rejects it as deprecated nowhere.
- `test_canonical_section_types_accepted` is parametrized over all eleven values and
  passes; `receipt_dynamo` unit suite stays green.
- `tools/glyph-studio/py/tests/test_sections.py` still passes unchanged (its
  `len(...) == 10` assertions are intentionally not touched — see §5).
- The decoder still loads and runs with the *unchanged* priors artifact and emits no
  `TRANSACTION_INFO` (proves the accept-but-don't-emit / no-KeyError property).

## 5. Out of scope (deliberately not touched)

- **Priors artifact** `section_order_priors_v2.json` — a rebuild output, not a hand-edit
  (§3). Editing `"sections"` here without a matching `section_models` entry would
  `KeyError` in `_ordered_sections`.
- **Golden fixtures** — decoder outputs; only regenerate after a real priors rebuild.
- **`tools/glyph-studio/py/glyphstudio/sections.py`** (`CANONICAL_SECTIONS`,
  `STYLESCAN_TO_SECTION`) — a *separate*, lowercase font-intelligence seed vocabulary
  that is **not** derived from `SectionType`. It currently folds stylescan `"transaction"`
  and `"reg_line"` into `footer`. Re-routing those to a new `transaction_info` seed is a
  distinct semantic decision with its own seed/QA implications, and its tests assert
  `len(CANONICAL_SECTIONS) == 10`. It is intentionally left at ten so those tests stay
  green; fold-rerouting belongs in a follow-up font-intel change, not this enum addition.
- **Label cleanup / relabeling of rows** — data work, done in dev via MCP, not in this
  patch.
- **Any DynamoDB write, priors rebuild, or model re-scoring** — see §3.
