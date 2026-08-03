# Devil's advocate — 5 attacks, ranked

**1. The Swift port silently forked the decoder within 24h, and CI says it's fine.**
Evidence: `receipt_ocr_swift/Sources/ReceiptOCRCore/LineItems/` last touched by #1313 (07-30 22:07); Python `receipt_upload/line_items/` changed twice after — #1320 non-product band filter, #1321 printed-total fallback. `LineItemDecoder.swift` has no band filter. The parity fixture `line_items_parity_expected.json` is frozen at #1313, so "33/33 parity" now proves Swift matches a *snapshot*, not the shipping decoder. On-device extraction produces pre-#1320 results while the cloud produces post-#1320; nothing fails.
Should have been: port after the decoder stabilized, or make the parity job regenerate expectations from live Python so drift is a red build.
KILL: the frozen-expectations parity job as written. Either regenerate-in-CI or delete the Swift decoder until the Python one stops changing weekly.

**2. Prod was mass-populated *before* the three quality fixes, guaranteeing a re-do that is still open.**
Evidence: prod rows/sections backfill ran 07-31 morning; #1320 merged 12:31, #1321 13:59, #1324 08-02. Result (`dev_prod_divergence.md`): prod 2572 line items with **848 mismatch** vs dev 2084 / 452 — prod's mismatch rate is 33% vs dev's 22%. Prod also has 3 rows on the dead `line-items-geom-v1`. The 822-receipt sweep to fix it is open item #2 at handoff, plus 20 grand_total disagreements and prod `07550815` reading **910664.0**.
Should have been: land the band filter + baseline hardening on dev, verify, *then* one prod pass. The user approved a rollout, not a rollout-then-rollout.
KILL: any further mass prod write until the sweep runs. Do the sweep first; it is the cheapest metric move left.

**3. The #1-ranked fix was never started; ~64k lines of ports/tools/viz shipped instead.**
Evidence: `failure_modes_report.md` fix #2 = ITEMS boundary repair, **47 receipts**, the single largest. Still listed as "biggest remaining decoder fix" at handoff. What shipped instead: #1313/#1314 Swift (57k lines incl. priors), #1316 viz (reverted by #1317 across 839 files), #1318 rewarp CDN (1727), #1319 MCP tools (2125), the /dev/validation harness. Metric actually moved: 411→452 receipts (+41) against the report's available +113. The three PRs that moved it total ~1,262 lines.
Should have been: zone-gap boundary before anything with a UI or a second language.
KILL: new tool surfaces. Nothing new until H-mode is closed.

**4. The bank "source of truth" produced a negative result and never reached prod.**
Evidence: `tender_report.md` — 162/168 printed totals match bank, i.e. it *confirmed* the baseline everyone already trusted. Eligible slice is 460/821 = 56%, and the report itself says a non-match is "an unusable negative signal." The realistic ceiling after card 8712 + Amex exports is ~510/821 ≈ 62%. Cost: #1322 (+1804 lines), 5 analysis scripts, an agent-day. Prod carries `ledger` 0/822, `bank_amount` 0/822 — none of it is live where the site reads. And its own top finding (Sprouts `grand_total` = 0.0/None, 34 of 111 misses) is a receipt-side bug, not a bank one.
Should have been: stop at the 96% validation result — that was the whole deliverable — and spend the rest on Sprouts GRAND_TOTAL.
KILL: matcher tuning, the 8712/Amex export chase, and the "bank as gate" framing. Keep the tender classifier (it is genuinely reusable); drop the gate.

**5. Parallel agents on shared mutable prod state cost more than they saved.**
Evidence: resegmentation took ~3h for ~30min of work — MCP gateway 503s while the Lambda kept working, refires spawned *racing* executions, a CAS loser clobbered the winner's status. The actual root cause (S3 ETag ABA) was found by the **successor single session**, not the 16-agent team. Add: agents idling without delivering, a phantom delegation from a mis-set task owner, and permission denials landing mid-sequence — one left prod labels-stripped-but-unapplied for minutes. The harness built for the user has **zero** entries; `review_log.jsonl` is 0 bytes and exists nowhere in the repo.
Should have been: one serialized writer for prod mutations; fan out only on read-only analysis (where it did work — the failure-mode and tender reports are the session's best artifacts).
KILL: concurrent agents holding write paths to the same table. Also stop building for a reviewer who has not reviewed once — ask before the next UI.

**Premise correction:** the "1am" re-architecting is a UTC artifact. Git author dates are PT: last commit 07-31 15:05, last tmp artifact 18:38. The scope drift is real (15:05→18:38 went entirely to CDN/reseg/divergence, none to zone-gap) — it just happened at 6pm.

**Sixth, briefly:** two-stack drift isn't the model's fault, it's that dev stopped being a rehearsal. Reseg landed on prod from *local* Lambda code, CDN repairs applied to prod, sections backfilled on prod — all while dev sat in a different state (tender 815 vs 5, 123 label drifts, line items 2084 vs 2572 in opposite directions). Fix the discipline, not the topology.
