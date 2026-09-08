# synthesis_loop/ (receipt font and render synthesis)

Deltas to the root `AGENTS.md`.

- `corpus_baseline.json` and `render_regression_baseline.json` are never edited
  to make a metric pass. A regression is fixed in code or explicitly accepted
  by the owner with a new capture; the PR must say which.
- `corpus_regression_gate.py` pins evaluation logic (it compares an image to
  itself). It is not proof that rendering is unchanged; use
  `render_regression_guard.py compare` for byte-identical pixel proof and quote
  its MAD numbers in the PR.
- MerchantTruth is owner-gated: `scripts/mint_merchant_truth_v2.py`,
  `scripts/activate_merchant_truth.py`, and `scripts/promote_merchant_truth.py`
  are not run by agents, and a FAIL from a truth gate is authoritative.
- Merchants share `layout_template`s. A change "for one merchant" must be
  measured across every merchant on that template before it ships.
- When two merchant paths conflict on merge, unify the helpers; never delete
  one merchant's path to resolve the conflict.
- Generated npz/PNG output goes to `.out/` (gitignored); JSON sources and
  manifests are the committable truth.
