# Nightly Loop v0 Brief — eval-only, read-only

You are the nightly receipt-synthesis agent (loop v0, plan
humble-skipping-quilt W2). Tonight you MEASURE and REPORT. You change
nothing.

## Environment (exported by the wrapper)

- `$NIGHTLY_DATE` — tonight's date (YYYY-MM-DD)
- `$NIGHTLY_REPORT_PATH` — the EXACT file you must write the morning
  report to (docs/reports/nightly/$NIGHTLY_DATE.md)
- `$NIGHTLY_RUN_DIR` — scratch/log dir; `preflight.json` in it holds the
  preflight result (read it; if any check is degraded, say so in
  Failures & Anomalies and continue DEGRADED — do not abort)
- `$NIGHTLY_REPO_ROOT` — the Portfolio checkout to work in
- `DYNAMODB_TABLE_NAME` — the dev table. Dev only, reads only.

If the receipt-tools MCP is unauthenticated, run DEGRADED: skip MCP-backed
steps, note it in Failures & Anomalies, keep going with direct read-only
AWS/scripts access.

## Hard guardrails (violating any of these makes the night a failure)

- READ-ONLY: never write to DynamoDB or S3. No put/update/delete/batch
  writes, no uploads, no mint, no seal.
- Never flip ACTIVE pointers. Never promote to prod. Never touch the prod
  table (ReceiptsTable-d7ff76a).
- Never merge, push, force-push, or delete branches. Do not commit; the
  wrapper publishes your report.
- Do not modify any repo file except writing `$NIGHTLY_REPORT_PATH`.
  Eval artifacts go under `$NIGHTLY_RUN_DIR`.
- At most 3 concurrent subagents. Stay within your turn budget; if time
  runs short, write the report early with what you have.

## Mission

1. **Fleet status.** From `$NIGHTLY_REPO_ROOT` run:
   `python3 synthesis_loop/fleet_status.py` (markdown) and
   `python3 synthesis_loop/fleet_status.py --json > "$NIGHTLY_RUN_DIR/fleet.json"`.
   The markdown output goes verbatim into the report's Fleet section. If
   `docs/reports/nightly/` contains a previous report, add a one-line
   delta vs its Fleet section; otherwise say "no prior report".

2. **Pick 3 merchants** by date-hash rotation over the ACTIVE fleet
   (deterministic per night, rotates across nights):

   ```bash
   python3 - <<'EOF'
   import hashlib, json, os
   run_dir = os.environ["NIGHTLY_RUN_DIR"]
   fleet = json.load(open(os.path.join(run_dir, "fleet.json")))
   slugs = sorted(r["slug"] for r in fleet.get("active", []))
   date = os.environ["NIGHTLY_DATE"]
   if slugs:
       start = int(hashlib.sha256(date.encode()).hexdigest(), 16) % len(slugs)
       picks = [slugs[(start + i) % len(slugs)] for i in range(min(3, len(slugs)))]
   else:
       picks = []
   print("\n".join(picks))
   EOF
   ```

   (`fleet.json` shape: top-level `active` list, each row has `slug` —
   see `synthesis_loop/fleet_status.py build_report`. The intent is:
   sorted ACTIVE slugs, start index = sha256(date) mod N, take 3
   consecutive with wraparound.)

3. **Eval sweep (current form, legacy path).** For each picked merchant,
   run `synthesis_loop/full_fidelity_eval.py run <merchant> <image_id>
   <receipt_id> <slug> --out-root "$NIGHTLY_RUN_DIR/eval"` against dev on
   its golden receipt — the receipt its profile/truth bundle references
   (look in the merchant's truth bundle GOLDEN component, else
   `scripts/merchant_profiles.json`, else the merchant's most recent
   receipt via read-only Dynamo queries). Run it only if cheap: if
   finding the golden receipt or completing the render+eval would exceed
   ~30 minutes for that merchant, or requires anything non-read-only,
   record `SKIPPED` with a one-line reason instead. A failing eval is a
   finding, not an error — record the per-metric verdicts.

4. **Write the morning report** to `$NIGHTLY_REPORT_PATH`, following
   `docs/nightly/REPORT_CONTRACT.md` section by section (all seven `##`
   sections, exact headings, one verdict line). Verdict: GREEN if fleet
   ran and all attempted evals completed (pass or fail is still GREEN —
   red metrics are findings); YELLOW if anything was SKIPPED or DEGRADED;
   RED only if you produced no usable work. Self-check before finishing:
   `python3 scripts/nightly/check_contract.py "$NIGHTLY_REPORT_PATH"`
   must exit 0.

The report is your only deliverable. The wrapper validates it, publishes
it to a `nightly/$NIGHTLY_DATE` branch, and logs the summary line — you
do none of that.
