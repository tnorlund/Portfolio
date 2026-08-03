# Agent-review pilot: 14 receipts, session1

**Verdicts:** 12 flag, 2 golden, 0 confirm, 0 approve-fix. Dossier diagnosis held in 9/14, refuted or materially incomplete in 5/14.

## Where vision changed the verdict vs. data alone
- **91b14596 (reversal).** Dossier: "rows never reached OCR, needs re-OCR or a better crop", `image_suspect=true`. Image is pristine and the missing item is right there in OCR (L8/L12), just filed under SUMMARY. Data alone would have bought a re-OCR for a section-assignment bug.
- **1828b9ba (vision-only).** Printed tax is 0.97, stored 1.07. Both arithmetics close, so nothing in the data can see it. It turned a "proven control" into a confirmed false accept.
- **750e0675 / b44d58bc / 758cbedf.** Data had the *what* (bank disagrees) but no cause, so no fix. The image gave the mechanism: Costco prints TOTAL in reverse video (white-on-black) and OCR mangles it every time; plus a trailing-minus discount `3.80-` dropped, and a pen stroke crossing a decimal turning `7.99` into `7/99`.
- **75ef30e5.** Only the image can adjudicate 39.99 vs 59.95. Data can only report that two numbers disagree.
- **a3e910b8.** Image proved the SC savings rows are *not* deducted from the balance (80.71 + 4.69 = 85.40), which reverses how they must be treated and refutes the dossier's "zone gap".
- **Near-redundant (2/14):** 58dfb81d, 838093ec — the tip-shaped-margin reasoning was already conclusive; the image only ratified it.

## Receipts I'd genuinely want a human for (3, one decision)
- **5985d2dd + 6e58ca91** (and 223c03e2, unreviewed). Not a perception gap — I read both scans fine. The gap is **authority over a destructive cross-record action**: three DB rows for one physical receipt, and choosing a survivor implies deleting or merging the others. Nothing in the dossier tells me the downstream blast radius (labels, training splits, prod parity).
- **55af0e9b.** Repair means hand-transcribing 31 rows into the DB as truth. The gap is **absence of an independent check**: I read the items *knowing* the target is 117.55, so my sum matching 117.55 is not independent confirmation of my reading. A human has the same problem, but a human is accountable for it.

## False-confidence risks
1. **All 14 came back "high" confidence.** That is itself the finding — I never produced a "genuinely ambiguous" call, which is implausible over 14 receipts and means my confidence signal carries almost no information as calibrated.
2. **Sum-to-target confirmation bias.** On 6 receipts my transcription hit the printed total exactly and I stopped looking. Compensating misreads (one row +0.10, another -0.10) would survive that check invisibly.
3. **Resolution.** I viewed downscaled JPEGs (~550-830px wide). On the Smith's phone photos the cent digits were at my limit; I read `STO CRT BABY ORGNC 1.99` partly because the arithmetic demanded it, not purely from pixels.
4. **Merchant-format priors.** After two Costcos I *expected* the reverse-video TOTAL and read the third the same way. Correct here, but that is pattern completion, not reading.
5. **Semantics the image cannot settle** — e.g. Hedary's `Fries $4.00` as its own item vs. a priced modifier. I picked one silently.

## Time
~5 min one-time setup (fetch script, 14 image downloads), then **~26 s/receipt wall clock** — 7m18s for all 14 including DynamoDB reads. Two tool round-trips each (digest + image).
