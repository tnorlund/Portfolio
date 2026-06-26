# Service-receipt synthesis — coordination flag (M7)

This branch (`feat/merchant-intelligence-agents`) PRODUCES the structural
taxonomy data that lets synthesis work for service merchants. The final
**unblocking** of the synthesis pipeline is a one-place change in the gate /
orchestration layer that CONSUMES this data — flagged here as the CHARTER M7
note says ("orchestration will consume `applicable_operations` to request the
right ops per merchant — flag that when M7 lands").

## What this branch delivers (done)

- Each `merchant_intelligence/<slug>.json` carries a `structure` block with
  `structure_type` (`line_item` | `service` | `hybrid`), `applicable_operations`
  (service excludes line-item ops), `archetype_mix`, `confidence`, and a gate
  `status`.
- The approval gate applies to structure: a new/low-confidence service
  assignment is parked at `needs_review` until a human signs it off
  (`known_merchants approve <slug> --kind structure`).
- A consumable hook:
  ```python
  from receipt_agent.agents.label_evaluator.merchant_research import (
      service_grounding_for,            # service_grounding_for(slug) -> contract
  )
  c = service_grounding_for("tan_l_a")
  # {is_service, valid_grounding_without_line_items, applicable_operations, reason}
  ```
  `valid_grounding_without_line_items` is True **only** for an APPROVED
  (auto/human) service merchant, so a parked structural assignment never grants
  the override.

## The blocked path (today)

A service merchant's receipts have no line-item grid, so:

1. `merchant_synthesis` (`_summarize_source_quality`) adds the **`no_line_items`
   hard blocker** → `synthesis_readiness.status = "blocked"`.
2. `receipt_layoutlm/data_loader.py` (`_synthetic_example_rejection`, ~line 1716)
   rejects any candidate whose `artifact_synthesis_readiness.status` is **not in
   `{"ready", "partial"}`** → `merchant_synthesis_not_ready`.

Both files are OUT OF THIS BRANCH'S BOUNDARY (the CHARTER forbids editing
`merchant_synthesis.py` gate logic and `data_loader.py`).

## The one change the gate-owner / orchestration needs

When `service_grounding_for(merchant).valid_grounding_without_line_items` is
True, treat `no_line_items` / `no_labeled_line_items` as an expected
**LIMITATION**, not a hard blocker — so `synthesis_readiness.status` becomes
`"partial"` instead of `"blocked"`. `data_loader` already accepts `"partial"`,
so the service example then flows through the (unchanged) train-only gates
(structure similarity, bbox validity, real-only validation). Orchestration also
requests only `applicable_operations` for the merchant (no line-item ops).

Net: a service merchant (e.g. Tan L.A., once its structure is approved) yields
field/amount/header synthetic examples, while a line-item merchant (Vons) is
unaffected — its 5 taxable adds at 7.25% still flow.
