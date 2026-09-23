"""Offline agreement audit: label-driven section roles vs stylescan regexes.

Synthesis v2 M1 (``SYNTHESIS_V2_EPIC.md``). For every visual line of every
committed labeled receipt, run both classifiers and tally where they agree
before any per-merchant ``_<SLUG>_RULES`` block is considered for deletion:

* ``stylescan._classify`` (per-merchant regex rules, production path)
  projected onto the role vocabulary via the canonical section fold;
* ``stylescan._classify_from_labels`` (shared ``CORE_LABELS`` -> role table).

Inputs (all in-repo, no AWS, no network):

* ``fixtures/source_snapshots/<slug>.json`` -- words grouped by ``line_id``;
* ``portfolio/public/synthetic-receipts/pipeline/<slug>/final.labels.json``
  -- parallel ``tokens``/``bboxes``/``ner_tags`` grouped into lines by bbox
  vertical overlap.

Each disagreement gets an automatic verdict from the line's own evidence:

* ``regex_wins`` -- the regex role is one the label taxonomy cannot express
  (footer/survey/section_header/barcode/separator), or at least one word's
  label on the line votes for the regex role (the aggregation outvoted it);
* ``label_wins`` -- the regex fell through (``other``) or no label on the
  line supports the regex role.

The verdict is a triage hint, not ground truth; the sampled lines are there
so a reviewer can judge. Output lands in ``tools/glyph-studio/.out/``.

Usage:
  python -m glyphstudio.label_role_audit [--samples 8] [--merchant costco]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass, field
from statistics import median

from .sections import normalize_stylescan_section
from .source_snapshot import SNAPSHOT_DIR
from .stylescan import (
    LABEL_ROLE_UNLABELED,
    _classify,
    _classify_from_labels,
    _word_core_labels,
    label_role_votes,
    line_has_price,
)

_STUDIO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_ROOT = os.path.dirname(os.path.dirname(_STUDIO))
PIPELINE_DIR = os.path.join(
    _ROOT, "portfolio", "public", "synthetic-receipts", "pipeline"
)
OUT_DIR = os.path.join(_STUDIO, ".out", "label_role_audit")

# Canonical section (sections.CANONICAL_SECTIONS) -> label-role vocabulary.
_CANONICAL_TO_ROLE = {
    "storefront": "header",
    "address": "header",
    "items": "item",
    "section_header": "section_header",
    "summary": "summary",
    "total_line": "total_line",
    "payment": "payment",
    "survey": "survey",
    "footer": "footer",
    "barcode": "barcode",
}
# Raw regex names the canonical fold merges into summary, but the label
# table keeps as their own role.
_RAW_SAVINGS = {"savings", "discount"}
# Roles no CORE label maps to: labels cannot contradict these.
LABEL_INEXPRESSIBLE = {
    "section_header",
    "survey",
    "footer",
    "barcode",
    "separator",
}
VERDICTS = ("agree", "label_wins", "regex_wins", "unlabeled")
ROLE_ORDER = (
    "header",
    "item",
    "savings",
    "summary",
    "total_line",
    "payment",
    "section_header",
    "footer",
    "survey",
    "barcode",
    "separator",
    "other",
    LABEL_ROLE_UNLABELED,
)
# Vertical overlap (fraction of the shorter box) that joins two tokens.
_LINE_OVERLAP = 0.5


def regex_role(raw: str) -> str:
    """stylescan raw section name -> label-role vocabulary."""
    if raw in _RAW_SAVINGS:
        return "savings"
    if raw == "separator":
        return "separator"
    canonical = normalize_stylescan_section(raw)
    return _CANONICAL_TO_ROLE.get(canonical or "", "other")


def verdict(label: str, regex: str, votes: dict[str, int]) -> str:
    """One of ``VERDICTS`` for a line (see module docstring)."""
    if label == LABEL_ROLE_UNLABELED:
        return "unlabeled"
    if label == regex:
        return "agree"
    if regex == "other":
        return "label_wins"
    if regex in LABEL_INEXPRESSIBLE or votes.get(regex, 0) > 0:
        return "regex_wins"
    return "label_wins"


# --- line grouping --------------------------------------------------------


def snapshot_lines(snapshot: dict) -> list[list[dict]]:
    """Snapshot words grouped by OCR ``line_id``, words by ``word_id``."""
    by_line: dict[int, list[dict]] = {}
    for word in snapshot.get("words", []):
        by_line.setdefault(int(word["line_id"]), []).append(word)
    return [
        sorted(by_line[k], key=lambda w: int(w.get("word_id", 0)))
        for k in sorted(by_line)
    ]


def _y_span(bbox) -> tuple[float, float]:
    y0, y1 = float(bbox[1]), float(bbox[3])
    return min(y0, y1), max(y0, y1)


def _overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        return 1.0 if inter >= 0 else 0.0
    return max(0.0, inter) / shorter


def group_words_by_overlap(
    words: list[dict], min_overlap: float = _LINE_OVERLAP
) -> list[list[dict]]:
    """Words with a ``bbox`` -> visual lines by bbox vertical overlap.

    A word joins the existing line whose median y-band it overlaps most
    (at least ``min_overlap`` of the shorter height); otherwise it starts a
    new line. Works for y-up and y-down boxes. Lines keep reading order
    (first word's input position); words within a line sort left to right.
    """
    lines: list[list[dict]] = []
    bands: list[tuple[float, float]] = []
    for word in words:
        span = _y_span(word["bbox"])
        best, best_frac = None, min_overlap
        for li, band in enumerate(bands):
            frac = _overlap_frac(span, band)
            if frac >= best_frac:
                best, best_frac = li, frac
        if best is None:
            lines.append([word])
            bands.append(span)
            continue
        lines[best].append(word)
        spans = [_y_span(w["bbox"]) for w in lines[best]]
        bands[best] = (
            median(s[0] for s in spans),
            median(s[1] for s in spans),
        )
    for line in lines:
        line.sort(key=lambda w: float(min(w["bbox"][0], w["bbox"][2])))
    return lines


def group_tokens_by_overlap(
    tokens: list[str],
    bboxes: list,
    ner_tags: list[str],
    min_overlap: float = _LINE_OVERLAP,
) -> list[list[dict]]:
    """Parallel ``final.labels.json`` lists -> visual lines of words."""
    if not (len(tokens) == len(bboxes) == len(ner_tags)):
        raise ValueError("tokens, bboxes and ner_tags must be parallel")
    words = [
        {
            "text": text,
            "bbox": list(bbox),
            "labels": _word_core_labels({"ner_tag": tag}),
            "index": idx,
        }
        for idx, (text, bbox, tag) in enumerate(zip(tokens, bboxes, ner_tags))
    ]
    return group_words_by_overlap(words, min_overlap)


def snapshot_rows(snapshot: dict) -> list[list[dict]]:
    """Snapshot words regrouped into visual rows by bbox overlap.

    OCR ``line_id`` often splits one printed row (Costco prints the price
    as its own OCR line), so this is the like-for-like view against the
    pipeline receipts; ``snapshot_lines`` is the default.
    """
    words = [w for line in snapshot_lines(snapshot) for w in line]
    return group_words_by_overlap(words)


def pipeline_lines(labels: dict) -> list[list[dict]]:
    return group_tokens_by_overlap(
        labels["tokens"], labels["bboxes"], labels["ner_tags"]
    )


# --- audit ----------------------------------------------------------------


@dataclass
class MerchantAudit:
    source: str
    merchant: str
    counts: dict[str, int] = field(
        default_factory=lambda: {v: 0 for v in VERDICTS}
    )
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)
    lines: int = 0

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "merchant": self.merchant,
            "lines": self.lines,
            "counts": self.counts,
            "agreement_of_labeled": agreement_rate(self.counts),
            "confusion": self.confusion,
            "disagreements": self.disagreements,
        }


def agreement_rate(counts: dict[str, int]) -> float | None:
    labeled = sum(counts[v] for v in VERDICTS if v != "unlabeled")
    return round(counts["agree"] / labeled, 4) if labeled else None


def classify_line(line: list[dict], merchant: str) -> dict:
    """Both verdicts for one line of words (dicts with text + labels)."""
    texts = [str(w.get("text") or "") for w in line]
    text = " ".join(texts)
    raw = _classify(text, line_has_price(texts), merchant)
    rrole = regex_role(raw)
    lrole = _classify_from_labels(line)
    votes = label_role_votes(line)
    return {
        "text": text,
        "labels": ["|".join(_word_core_labels(w)) or "O" for w in line],
        "label_role": lrole,
        "label_votes": votes,
        "regex_raw": raw,
        "regex_role": rrole,
        "verdict": verdict(lrole, rrole, votes),
    }


def audit_lines(
    source: str, merchant: str, lines: list[list[dict]]
) -> MerchantAudit:
    audit = MerchantAudit(source=source, merchant=merchant)
    for line in lines:
        row = classify_line(line, merchant)
        audit.lines += 1
        audit.counts[row["verdict"]] += 1
        cell = audit.confusion.setdefault(row["label_role"], {})
        cell[row["regex_role"]] = cell.get(row["regex_role"], 0) + 1
        if row["verdict"] in ("label_wins", "regex_wins"):
            audit.disagreements.append(row)
    return audit


def load_sources(
    snapshot_dir: str = SNAPSHOT_DIR,
    pipeline_dir: str = PIPELINE_DIR,
    which: str = "all",
    merchants: set[str] | None = None,
    snapshot_grouping: str = "line_id",
) -> list[tuple[str, str, list[list[dict]]]]:
    """``(source, merchant, lines)`` for every committed labeled receipt.

    ``snapshot_grouping`` is ``line_id`` (OCR lines) or ``overlap`` (visual
    rows, as for the pipeline receipts).
    """
    group = snapshot_rows if snapshot_grouping == "overlap" else snapshot_lines
    src_name = (
        "snapshot" if snapshot_grouping == "line_id" else "snapshot-rows"
    )
    out = []
    if which in ("all", "snapshot"):
        for path in sorted(glob.glob(os.path.join(snapshot_dir, "*.json"))):
            with open(path, encoding="utf-8") as fh:
                snap = json.load(fh)
            slug = snap.get("slug") or os.path.basename(path)[:-5]
            if merchants and slug not in merchants:
                continue
            out.append((src_name, slug, group(snap)))
    if which in ("all", "pipeline"):
        pattern = os.path.join(pipeline_dir, "*", "final.labels.json")
        for path in sorted(glob.glob(pattern)):
            slug = os.path.basename(os.path.dirname(path))
            if merchants and slug not in merchants:
                continue
            with open(path, encoding="utf-8") as fh:
                labels = json.load(fh)
            out.append(("pipeline", slug, pipeline_lines(labels)))
    return out


def run_audit(sources) -> list[MerchantAudit]:
    return [audit_lines(src, slug, lines) for src, slug, lines in sources]


# --- report ---------------------------------------------------------------


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{100 * rate:.0f}%"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def summary_table(audits: list[MerchantAudit]) -> list[str]:
    rows = [
        "| source | merchant | lines | agree | label-wins | regex-wins "
        "| unlabeled | agree / labeled |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    total = {v: 0 for v in VERDICTS}
    for a in audits:
        c = a.counts
        for v in VERDICTS:
            total[v] += c[v]
        rows.append(
            f"| {a.source} | {a.merchant} | {a.lines} | {c['agree']} "
            f"| {c['label_wins']} | {c['regex_wins']} | {c['unlabeled']} "
            f"| {_pct(agreement_rate(c))} |"
        )
    rows.append(
        f"| **all** | | **{sum(a.lines for a in audits)}** "
        f"| **{total['agree']}** | **{total['label_wins']}** "
        f"| **{total['regex_wins']}** | **{total['unlabeled']}** "
        f"| **{_pct(agreement_rate(total))}** |"
    )
    return rows


def confusion_table(audit: MerchantAudit) -> list[str]:
    label_roles = [r for r in ROLE_ORDER if r in audit.confusion]
    regex_roles = [
        r
        for r in ROLE_ORDER
        if any(r in cols for cols in audit.confusion.values())
    ]
    rows = [
        "| label \\ regex | " + " | ".join(regex_roles) + " |",
        "|---|" + "---:|" * len(regex_roles),
    ]
    for lr in label_roles:
        cells = []
        for rr in regex_roles:
            n = audit.confusion[lr].get(rr, 0)
            cells.append(f"**{n}**" if n and lr == rr else str(n or ""))
        rows.append(f"| {lr} | " + " | ".join(cells) + " |")
    return rows


def disagreement_patterns(
    audits: list[MerchantAudit],
) -> list[tuple[str, str, str, int]]:
    """``(label_role, regex_role, verdict, count)`` most common first."""
    tally: dict[tuple[str, str, str], int] = {}
    for a in audits:
        for d in a.disagreements:
            key = (d["label_role"], d["regex_role"], d["verdict"])
            tally[key] = tally.get(key, 0) + 1
    return sorted(
        ((k[0], k[1], k[2], n) for k, n in tally.items()),
        key=lambda t: (-t[3], t[0], t[1]),
    )


def sample_disagreements(rows: list[dict], n: int) -> list[dict]:
    """Up to ``n`` rows, round-robin over (label, regex) patterns so each
    distinct disagreement shows up before any repeats."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        buckets.setdefault((row["label_role"], row["regex_role"]), []).append(
            row
        )
    queues = sorted(buckets.values(), key=len, reverse=True)
    out: list[dict] = []
    depth = 0
    while len(out) < n and any(depth < len(q) for q in queues):
        for q in queues:
            if depth < len(q) and len(out) < n:
                out.append(q[depth])
        depth += 1
    return out


def render_markdown(audits: list[MerchantAudit], samples: int = 8) -> str:
    out = [
        "# Label-role vs regex section audit (synthesis v2 M1)",
        "",
        "Per visual line: `_classify_from_labels` (shared CORE_LABELS "
        "table) vs `_classify` (per-merchant regex, projected to roles). "
        "`label-wins`: regex fell through or no label on the line supports "
        "the regex role. `regex-wins`: the regex role is label-inexpressible "
        "(footer/survey/section_header/barcode/separator) or some label on "
        "the line votes for it. `unlabeled`: no role-bearing label.",
        "",
        "## Agreement",
        "",
        *summary_table(audits),
        "",
        "## Most common disagreements",
        "",
        "| label role | regex role | verdict | lines |",
        "|---|---|---|---:|",
    ]
    for lr, rr, v, n in disagreement_patterns(audits)[:10]:
        out.append(f"| {lr} | {rr} | {v} | {n} |")
    for a in audits:
        out += [
            "",
            f"## {a.source} / {a.merchant}",
            "",
            *confusion_table(a),
        ]
        if not a.disagreements:
            continue
        out += [
            "",
            f"Disagreeing lines ({len(a.disagreements)}; first "
            f"{min(samples, len(a.disagreements))}):",
            "",
            "| text | labels | label role | regex (raw) | verdict |",
            "|---|---|---|---|---|",
        ]
        for d in sample_disagreements(a.disagreements, samples):
            out.append(
                f"| `{_cell(d['text'])}` | {_cell(' '.join(d['labels']))} "
                f"| {d['label_role']} | {d['regex_role']} "
                f"({d['regex_raw']}) | {d['verdict']} |"
            )
    return "\n".join(out) + "\n"


def write_report(
    audits: list[MerchantAudit], out_dir: str, samples: int
) -> tuple[str, str, str]:
    os.makedirs(out_dir, exist_ok=True)
    md = render_markdown(audits, samples)
    md_path = os.path.join(out_dir, "report.md")
    json_path = os.path.join(out_dir, "report.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump([a.to_json() for a in audits], fh, indent=2)
        fh.write("\n")
    return md, md_path, json_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshots-dir", default=SNAPSHOT_DIR)
    ap.add_argument("--pipeline-dir", default=PIPELINE_DIR)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument(
        "--source", choices=("all", "snapshot", "pipeline"), default="all"
    )
    ap.add_argument(
        "--snapshot-grouping",
        choices=("line_id", "overlap"),
        default="line_id",
        help="group snapshot words by OCR line_id or by bbox overlap",
    )
    ap.add_argument(
        "--merchant",
        action="append",
        help="limit to this slug (repeatable)",
    )
    ap.add_argument(
        "--samples",
        type=int,
        default=8,
        help="disagreeing lines shown per merchant",
    )
    args = ap.parse_args(argv)
    sources = load_sources(
        args.snapshots_dir,
        args.pipeline_dir,
        args.source,
        set(args.merchant) if args.merchant else None,
        args.snapshot_grouping,
    )
    audits = run_audit(sources)
    md, md_path, json_path = write_report(audits, args.out_dir, args.samples)
    print(md)
    print(f"wrote {md_path}\nwrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
