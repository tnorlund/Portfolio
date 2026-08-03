# Historian report — session 7a30c911 (2026-07-29 17:21 → 08-03 12:19)

Evidence: session transcript (6,598 lines; 117 real user directives, 50 agent relays), `git log origin/main`, `gh pr list`, tmp/ file mtimes (PDT, align exactly with PR times), the three agent reports, memory `project_line_item_extraction.md`. **Narrative correction:** the session did NOT start on the line-item epic. It opened 07-29 17:21 with *"there's a bunch of slop from the codex and claude sessions… let's use this session to clean up all the open PRs."* The narrative's goal list omits this entirely; the line-item epic emerged ~16h in. `#1324` merged 08-03, not 08-02. Otherwise the narrative's shipped-list and incident-list check out.

## (a) Phases

| # | Window (PDT) | Driving goal | Directives / relays / span | Merged PRs |
|---|---|---|---|---|
| P0 | 07-29 17:21 → 07-30 09:50 | Clean up open PRs; then prove v31+Swift+CoreML end-to-end | 5 / 0 / 16.3h | #1291 |
| P1 | 07-30 09:50 → 16:52 | Find a deterministic way to get line items ("reduce LLM dependency") | 44 / 8 / 6.7h | #1299–#1305 (7) |
| P2 | 07-30 16:52 → 07-31 09:11 | Execute the TODO list; prod sections rollout | 4 / 0 / 5.4h | #1306–#1313 (7) |
| P3 | 07-31 09:11 → 11:30 | Catch up, delegate to codex, visualize the pipeline | 17 / 0 / 2.3h | #1314–#1317 (4) |
| P4 | 07-31 11:30 → 14:50 | **Truth-driven validation** — failure modes + bank/tender ground truth | 24 / 10 / 3.2h | #1318–#1322 (5) |
| P5 | 07-31 14:50 → 18:45 | Ad-hoc broken receipts → asset repair + resegmentation | 17 / **31** / 3.9h | #1323 (3 lines) |
| P6 | 08-03 11:12 → 12:19 | Handoff; session declared unusable | 6 / 1 / 1.2h | — |

P2 is peak autonomy (4 directives → 7 PRs in 5.4h). P5 inverts: agent chatter outnumbered user turns 31:17 and produced one three-line dependency pin plus a stalled draft.

## (b) Effort accounting (~25h active, wall-clock between first and last turn of each phase)

- **Original stated goal (PR/slop cleanup): ~2h, 8%.** Partly achieved — #1291 settled, #1308 closed as a dup, but #1298 was opened during P1 and is *still open today*.
- **Emergent but goal-aligned (line-item epic → TODO list → truth validation): ~17h, 68%.** Note the TODO list itself was replaced mid-session: at 07-31 13:33 the user said *"clear the TODOs and add these here"*, swapping the 07-30 task list for the bank-truth plan.
- **Pure incident response: ~6h, 24%.** Reseg saga ~2.5h of overrun; #1310 prod deploy break ~1h; CDN backup wipe ~0.5h; layer-v95 DLQ ~0.5h; isort ×3 ~0.5h; MCP outage + viz revert ~1h.

## (c) Drift map

| # | When | Pivot | Initiator | Returned? |
|---|---|---|---|---|
| 1 | 07-29 19:08 | PR cleanup → prove v31/Swift end-to-end | User | **No** — cleanup never resumed (#1298 open) |
| 2 | 07-30 09:50 | E2E proof → line-item extraction R&D | User (bad Smokes & Vapes receipt) | Became the session's real spine |
| 3 | 07-30 14:11–14:49 | Deterministic → ML row-role classifier → back to deterministic | User floated it; lead argued back after HD failed | Yes, ~40 min |
| 4 | 07-30 16:52 | R&D → explicit TODO execution | User | Yes — the most productive stretch |
| 5 | 07-31 09:11 | Execution → "what did we do yesterday? prod component is ugly" | User | Yes, via codex delegation |
| 6 | 07-31 10:08 | → visualization | User | Partly — the viz became the /dev/validation harness |
| 7 | 07-31 10:58 | Viz → emergency revert (#1317, 839 files) | **Lead's own-goal** (merged unreviewed) | Yes, ~30 min; produced a durable rule |
| 8 | 07-31 11:22 | Viz review → CDN re-warp | User (spotted bad Costco crop) | **No** — 3 agents, ~7h elapsed, #1318 merged only 08-03 |
| 9 | 07-31 11:30 | → per-merchant failure modes | User | Best artifact of the session |
| 10 | 07-31 12:59–13:32 | → Chase/Apple Card as ground truth | User | Best strategic pivot; became the new TODO list |
| 11 | 07-31 14:50 | Truth plan → 3 broken prod receipts → reseg saga | User (ad-hoc flag) | **No** — consumed the rest; tasks #13/#14 still pending |

## (d) Three most valuable outcomes

1. **Prod line-item rollout (P2, ~6h).** 821 receipts / 29,234 rows / 795 sections backfilled by *computation, not copy*; section INSERTs auto-triggered the new stream stage, so line items materialized without a backfill script. Prod went 3 → ~2,600 line items. Highest durable value in the session.
2. **The truth join (P4, ~2h).** `failure_modes_report.md` + `tender_report.md` proved the baseline is trustworthy (162/168 printed totals match Chase exactly), then showed the re-OCR loop built the night before targets the *rarest* mode (digit fragmentation ~0%) while zone-gap (45%) is untouched. This one finding retargeted the roadmap and directly produced #1320/#1321/#1324 (corpus match 411 → 452). It also corrected the coverage story from a naive 26% to an honest 75.9%.
3. **Swift parity port (#1313/#1314/#1315, ~62k lines, 33/33 exact parity).** Moves extraction on-device, which is what the user asked for at 07-30 13:44. Delivered largely by codex on the Mac mini at near-zero session cost — the best delegation of the session.

## (e) Three most expensive detours

1. **Resegmentation saga (~3.9h wall, ~2.5h overrun) for 2 images.** Four independent failures stacked: MCP gateway 503s while the Lambda succeeded, CAS-loser rollback clobbering the winner, deployed Lambda OOM at 3GB, deployed Lambda behind main. Shipped no merged code that session; the actual fix (#1327) came 3 days later from the successor. User frustration is on the record at 15:47 and 15:57 (*"can't we do this fast and in parallel???"*).
2. **CDN re-warp thread (~7h elapsed across 4 agents).** Real output (~424 assets repaired), but the site deploy's `s3 sync --delete` destroyed its own backups on an unversioned bucket, leaving ~129 receipts with no rollback, and #1318 sat unmerged for 3 days. Worst cost/benefit of the "user spotted a bad image" threads.
3. **07-30 midday architecture churn (12:33–14:49, ~2h).** Chroma section filter → alignment bug → row-role classifier → ML-vs-deterministic → back to the deterministic block decoder it started from. The user flagged it in real time twice: *"it's almost like we're not really making progress here?"* and *"we're regressing while making progress in other areas."* Defensible as necessary exploration, but it ended where it began.

Honorable mention: the unreviewed-viz merge/revert cost only ~30 min but required an 839-file revert because it dragged #1291's frontend slice with it.
