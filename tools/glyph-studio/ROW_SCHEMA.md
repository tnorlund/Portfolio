# Row Schema — materialized visual rows, row-granularity sections

**Status:** row-anchoring amendment to FONT_INTELLIGENCE_EPIC.md (additive; no
existing consumer changes behavior)
**Depends on:** ReceiptSection v1 (canonical vocab + QA fields, merged M0/M1a-1/M1b)
**Feeds:** StyleRun (typography-runs pilot), M1a-2 ingest wiring, Chroma lines
metadata, embedding batch identity (task #19)

---

## 1. The hierarchy

```
ReceiptLetter ── char-level crops; attribution to word/line (exists today)
     │
  StyleRun ───── contiguous same-style span within a row
     │           [PLACEHOLDER — schema locks after the running typography
     │            pilot reports within-row style variance; see §7]
     │
  ReceiptRow ─── one printed row; groups the OCR lines Vision split apart
     │           (NEW — this amendment)
     │
ReceiptSection ─ v2: row-granular via optional row_ids
                 (AMENDED — additive; line_ids stays authoritative)
```

The unit the font model renders is the *row* (one pass of the print head);
the unit sections classify should therefore also be the row. Today both
sections and Chroma line-embeddings are built on OCR `ReceiptLine`s, which
are fragments of rows.

## 2. Motivation (measured 2026-07-10, QA'd 799-receipt corpus)

- **Visual rows are 97.0% section-atomic.** Of 18,803 sectioned rows, only
  562 straddle a section boundary — and the straddles are overwhelmingly
  label+amount columns that LINE-level sectioning tore in half (e.g.
  `TOTAL | 6.70` split SUMMARY/TOTAL_LINE). Sections *want* to be
  row-granular; line granularity is what lets them tear.
- **Only 54.3% of sections align to whole rows today.** The other half
  contain fragments of rows (one side of a label+amount pair) — exactly the
  incoherence row-granularity fixes by construction.

Materializing rows also fixes two standing defects:

1. **Chroma line-row metadata is a plurality vote.** The lines collection
   stores one embedding per visual row, but `section_label` is derived by
   majority vote over the row's lines (`row_section_from_map`) because
   sections are line-level. With row-granular sections the row's section is
   exact — the vote (and its tie→None ambiguity) disappears.
2. **Visual-row grouping is recomputed at submit vs poll time and is
   order-sensitive.** The embedding batch pipeline derives row identity
   twice; disagreement is the root cause of the stale-batch / PENDING-zombie
   saga. A persisted ReceiptRow is the durable identity both ends read —
   this is task #19's real fix.

## 3. ReceiptRow (new entity, `receipt_dynamo`)

| | |
|---|---|
| PK | `IMAGE#{image_id}` |
| SK | `RECEIPT#{receipt_id:05d}#ROW#{row_id:05d}` |
| TYPE | `RECEIPT_ROW` (GSITYPE only, like ReceiptSection) |

**`row_id` == the row's primary line id** — the leftmost member line, per
`receipt_chroma.embedding.formatting.line_format.get_primary_line_id`. This
is deliberately the id the Chroma lines collection already uses for the
row's embedding (`IMAGE#{image}#RECEIPT#{r:05d}#LINE#{primary:05d}`), so
ReceiptRow ⇄ lines-collection ⇄ ReceiptLine all join with zero translation.
Enforced: `row_id == line_ids[0]`.

Fields: `line_ids` (ordered left-to-right), `grouping_version`
(`"visual-rows-v1"` — a future grouping change mints new rows instead of
silently disagreeing with old ones), geometry summary (`y_min`/`y_max`
y-band, `x_min`/`x_max` x-extent, normalized coords), `created_at`.

Full CRUD on `DynamoClient` mirrors `_receipt_section.py`:
`add/update/delete/get_receipt_row`, batch variants,
`get_receipt_rows_from_receipt` (strongly consistent, paginated),
`list_receipt_rows` (GSITYPE).

## 4. ReceiptSection v2 (additive)

New optional field `row_ids: list[int] | None` (each id a ReceiptRow
primary line id). **`line_ids` remains authoritative for every existing
consumer** — `sections_to_line_map`, the stream consumer, MCP tools all
read `line_ids` and are unchanged (suites verified, §6).

**Invariant** (when `row_ids` is set): `line_ids` == union of the
referenced rows' `line_ids`. Enforced by
`validate_section_row_coverage(section, rows)`
(`receipt_dynamo.entities.receipt_section`); the backfill validates every
section it writes. Legacy sections (`row_ids=None`) are exempt.

## 5. Migration (`scripts/backfill_receipt_rows.py`)

One-shot, per-receipt cached/resumable (cache marker per receipt; re-runs
skip), dry-run by default, local-first (refuses non-local endpoints without
`--allow-remote`):

1. Group each receipt's lines via the *existing*
   `group_lines_into_visual_rows`; write ReceiptRow entities (batch put —
   idempotent).
2. A section claims a row when it contains any of the row's line_ids.
   Straddle rows (~3%) resolve by majority-of-lines; ties between the
   leading sections go to the leader owning the row's primary line; if the
   primary line's section is not among the leaders, a deterministic
   alphabetical fallback applies (logged as `tie-fallback-alpha`, 12/510 on
   the corpus). Every resolution is logged (JSONL).
3. Update sections with `row_ids` + reconciled `line_ids` (union of claimed
   rows). Sections *emptied* by straddle resolution (every row won by a
   competitor) are **deleted** — leaving them would keep stale `line_ids`
   authoritative and double-assign those lines; deletions are logged.
   Re-runs also prune stale rows whose primary id vanished under a grouping
   change, so a receipt never carries a mixed row generation.
   `validate_section_row_coverage` runs on every write.

Order of environments: local sandbox (validated — see PR) → dev → prod via
the standard mirror. **Dev/prod writes are a separate approval.**

Stream safety (verified): the stream consumer's SK matcher has no `#ROW#`
pattern, so ReceiptRow writes produce no messages; section updates flow
through the existing RECEIPT_SECTION path (`line_ids` is already a
monitored field, so reconciled sections correctly trigger Chroma metadata
recompute in environments with streams).

## 6. Consumer impact

| consumer | reads | impact now | later (opt-in) |
|---|---|---|---|
| `sections_to_line_map` (embed + compaction stamping) | `line_ids` | none — unchanged, suite green | none needed; stays exact because line_ids stays authoritative |
| `row_section_from_map` (plurality vote) | line map | none | replace vote with exact row→section lookup once rows are backfilled everywhere |
| `receipt_dynamo_stream` consumer | SK patterns, `line_ids` | none — `#ROW#` SKs ignored; suite green | optional RECEIPT_ROW handling if rows ever need Chroma sync |
| MCP section tools (`create/get/delete_receipt_section`, `update_section_status`) | entity fields | none — `row_ids` optional, omitted when unset | expose row_ids in tool output; row-granular section editing |
| embedding batch submit/poll | recomputes grouping | none | read ReceiptRow for durable row identity (task #19 fix) |
| M1a-2 ingest section wiring | sections at upload | none | write rows at ingest alongside sections |
| LayoutLM / synthesis legs | sections | none | row-conditioned features and rendering units |

## 7. Open questions for StyleRun (schema deliberately not locked)

- Does within-row style variance justify a run *entity*, or is a per-row
  style summary (fields on ReceiptRow) enough? Pilot must report the rate of
  multi-style rows (e.g. regular label + bold amount on one row).
- Run identity: char-span within the row (stable under re-OCR?) vs word-id
  list vs letter-id range.
- Whether StyleRun keys under the row
  (`...#ROW#{row_id:05d}#RUN#{n:02d}`) — natural if runs never cross rows;
  the pilot should confirm runs don't span rows (double-height amounts?).
- Attribution: ReceiptLetter → run assignment needs the letter-level
  face/weight classifier output; confidence field mirrors ReceiptSection's.

## 8. Decision log

| decision | choice | why |
|---|---|---|
| Row identity | `row_id == primary (leftmost) line_id` | identical to Chroma lines-collection ids → joins for free; no new id namespace |
| SK shape | `RECEIPT#{r:05d}#ROW#{row_id:05d}` | sibling of LINE/SECTION SKs; invisible to the stream matcher (verified) |
| Section change | additive `row_ids`, `line_ids` authoritative | zero breakage; consumers migrate opt-in; invariant validator keeps both honest |
| Straddle resolution | majority-of-lines; tie between leaders → the leader owning the primary line; else alphabetical fallback | matches the measured failure mode (label+amount tears); every resolution logged |
| Emptied sections | deleted (logged), not left stale | leaving them double-assigns lines because line_ids stays authoritative |
| Grouping versioning | `grouping_version` string on the row | grouping algorithm changes mint distinguishable generations instead of corrupting identity |
| Geometry summary | y-band + x-extent only | enough for section/band queries; full geometry lives on ReceiptLine |
| StyleRun | placeholder | typography pilot (within-row style variance) reports first; don't lock a schema without the measurement |
