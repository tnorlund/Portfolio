# The Merchant Review Operating Model

Metric for a review session (one number, declared up front): **golden-set entries added + PROVEN-eligible repairs approved**. Reviewing without a verdict that changes a row is not a session.

## (a) Session shape — 45–60 min, ~30 receipts, ~90 s each

**Pre-session (agents, read-only, ~20 min before the user sits down).** A scout agent writes two artifacts under `.dev-harness/`: `queues/<session>.json` — an ordered receipt list (the shim currently orders only by status then |delta|; a curated queue is what makes a session a *session*), and `dossiers/<image_id>-<receipt_id>.json` — per receipt: the failure-mode letter from the A–J taxonomy with its closing evidence (which bands, what sum), the truth-chain hop that broke, whether the image itself is suspect (crop/CDN), and where fixable, a **proposed fix**: `extend_items_section` with `dry_run=True` output showing delta and status before/after. Receipts whose fix does not strictly improve both get `abstain` plus the reason — an honest abstention is more useful than a guess.

**Live loop (user alone in the harness).** Read dossier → look at the image → Confirm / Flag / Approve-fix, note optional. No agent touches DynamoDB while the user is reviewing. Agents consume `GET /review` **at merchant boundaries**, not continuously: when the user finishes a merchant, one triage agent reads the new entries and refreshes dossiers for the *next* merchant with what it just learned. Continuous polling buys nothing at 90 s/receipt and invites a racing writer.

**Post-session.** ONE writer agent, its own thread, no analysis mixed in: applies `dry_run=False` for approved fixes one receipt at a time, re-reads `/receipt` to confirm delta moved as the dry run predicted, and rolls back nothing silently — a fix that lands differently than predicted is a bug report, not a retry. Then a scribe promotes `golden` verdicts into `line_items_golden*.json`, opens issues for flags with no proposed fix, and appends the session's numbers to the plan doc.

## (b) Merchant order, and why

1. **Smith's (8) + Gelson's (6), all statuses.** Both are near-pure zone-gap (H×4 of 5, H×3 of 4) at small n. This is the cheapest possible falsification of PLAN item 3, the ITEMS-boundary extension, *before* it ships against 47 receipts. If the user's eyes say the missing rows really are products, the fix is de-risked; if not, we saved the most expensive planned change.
2. **The 6 bank-contradicted printed totals** (bank amount ≠ printed total, from the 162/168 tender result). Six receipts where two independent truths disagree — only a human can say which one lies, and they gate the whole "bank as positive signal" premise.
3. **~10 PROVEN controls** (match + bank-matched). These test the harness, not the receipts: if the user disagrees with even one green row, the 1%/10% tolerance ladder is admitting false accepts and every count above is inflated.
4. **Costco (10 mismatch, H×7)** next session — zone-gap arithmetically, but tangled with garbled OCR and bad crops, so the judgment is "is this image even readable," which no agent can make.
5. **Sprouts (188 receipts, 48 mismatch)** last, and stratified — sample ~10 across H/D/A, never all 48. Its dominant residue is `grand_total` 0/None, a code bug the summary sweep fixes; spending a human hour on it would be measuring a known defect.

Skip Amazon Fresh, AIM Mail, Home Depot for now: n≤5 or mode B (broken printed baseline), where the receipt, not the decoder, is wrong.

## (c) Agent roles and boundaries

Four roles, at most two running at once. **Scout** (read-only): queue + dossiers, before the session. **Triage** (read-only): consumes `/review` at merchant boundaries, refreshes downstream dossiers, flags contradictions between the user's verdicts and the dossier's diagnosis — those contradictions are the session's real output. **Writer** (the only agent with a write path, strictly serialized, post-session only): guarded MCP tools only, dev table only, one receipt at a time. **Scribe** (read-only + repo writes): golden fixtures, issues, plan update. Rules that are not negotiable: no agent writes DynamoDB during the live loop; the writer never runs inside a thread that also did analysis; dossiers are files, never rows; a dossier that cannot justify its proposal abstains.

Verdict routing: `confirm` on a match+bank receipt → golden promotion. `approve-fix` → writer queue. `flag` with a note naming the problem → issue. **`flag` on a green row → stop-everything signal**: it means the tolerance ladder or the baseline rule is producing false accepts, and every downstream count is suspect until explained.

## (d) Gaps to close first — four small changes, no rebuild

1. **The harness cannot show agent work.** `/receipt` returns pipeline data and prior reviews; there is no dossier surface, and `failureHint()` in `truthChain.ts` is a client-side heuristic, not an analysis. Fix: shim reads `.dev-harness/dossiers/<image_id>-<receipt_id>.json` and returns it as `dossier`; `TruthPanel` renders diagnosis, evidence, and proposed fix above the note box. ~40 lines.
2. **No curated queue.** Add `queue=<name>` to `/worklist`, reading an ordered id list from `.dev-harness/queues/`. Without it the user reviews whatever sorts worst, not the 30 receipts that answer a question. ~20 lines.
3. **The verdict vocabulary is too thin to act on.** `review_log` records only confirm/flag/resolved plus free text — no reason code, no pointer to *which* rows the user judged, no way to say "this one belongs in the golden set." Add optional `reason` (the A–J code or hint code), `line_ids`, and the verdicts `approve-fix` and `golden`; wire a third button. Note `resolved` is currently accepted by the shim but has no button — dead path. ~30 lines.
4. **Nothing durable comes out.** `.dev-harness/review_log.jsonl` does not exist and is not in the repo; the seed corpus for the bank-proven golden loop would live only on one machine. Commit it under `docs/line-items/reviews/` (or sync it there post-session) so a verdict survives the laptop.

Explicitly not built: an apply button in the UI (breaks the one-writer rule), live agent chat in the harness, and any new MCP tool — the three that exist cover diagnose, repair, and queue.
