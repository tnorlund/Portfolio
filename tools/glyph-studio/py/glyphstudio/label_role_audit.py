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
  vertical overlap;
* optionally ``--corpus-dir DIR``: ``DIR/<slug>/*.json`` source snapshots,
  many receipts per merchant (source ``corpus``).

Each disagreement gets an automatic verdict from the line's own evidence:

* ``regex_wins`` -- the regex role is one the label taxonomy cannot express
  (footer/survey/section_header/barcode/separator), or at least one word's
  label on the line votes for the regex role (the aggregation outvoted it);
* ``label_wins`` -- the regex fell through (``other``) or no label on the
  line supports the regex role.

The verdict is a triage hint, not ground truth; the sampled lines are there
so a reviewer can judge. Output lands in ``tools/glyph-studio/.out/``.

Stage S1 replaces self-scoring with hand-adjudicated truth
(``fixtures/section_role_truth/<slug>.jsonl``, schema in that directory's
README). ``adjudicate-template`` writes the disagreeing lines there with
``role: null`` for a human to fill; ``score`` measures both classifiers,
and label-with-regex-fallback, against the filled records and prints the
proposed S3 regex-retirement gate per merchant.

Usage:
  python -m glyphstudio.label_role_audit [--samples 8] [--merchant costco]
  python -m glyphstudio.label_role_audit adjudicate-template [--per-pattern 20]
  python -m glyphstudio.label_role_audit score [--truth DIR]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
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
TRUTH_DIR = os.path.join(_STUDIO, "fixtures", "section_role_truth")

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
# Roles a truth record may carry (``None`` = undecidable). ``other`` and
# ``unlabeled`` are classifier outputs, never truth: every printed line
# belongs to some block.
TRUTH_ROLES = tuple(
    r for r in ROLE_ORDER if r not in ("other", LABEL_ROLE_UNLABELED)
)
TRUTH_SOURCES = ("snapshot", "pipeline", "corpus")
TRUTH_FIELDS = (
    "source",
    "merchant",
    "line_key",
    "text",
    "role",
    "note",
    "adjudicated_by",
    "date",
)
# S3 regex-retirement gate, PROPOSED in SYNTHESIS_UNIFIED_PLAN section 6.2
# (the owner still holds the decision): label-with-fallback agreement at or
# above regex agreement on the same adjudicated truth, and no truth role on
# which label-with-fallback gets more than this many fewer lines right.
S3_GATE_MAX_ROLE_DEFICIT = 5
S3_GATE_STATUS = "PROPOSED in plan section 6.2; owner decision pending"


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
    lines = group_tokens_by_overlap(
        labels["tokens"], labels["bboxes"], labels["ner_tags"]
    )
    receipt = labels.get("receipt_key")
    if receipt:
        for line in lines:
            for word in line:
                word["receipt"] = str(receipt)
    return lines


def snapshot_receipt_key(snapshot: dict) -> str | None:
    """``<image_id>#<receipt_id>`` of the geometry row whose words a
    snapshot pinned, or None when the snapshot does not name one."""
    geo = snapshot.get("geometry_receipt") or {}
    if not geo.get("image_id") or geo.get("receipt_id") is None:
        return None
    return f"{geo['image_id']}#{int(geo['receipt_id'])}"


def tag_snapshot_receipt(snapshot: dict) -> dict:
    """Shallow copy of ``snapshot`` whose words carry ``receipt`` (the
    geometry receipt key), so ``line_key`` can name their lines."""
    receipt = snapshot_receipt_key(snapshot)
    if receipt is None:
        return snapshot
    words = [dict(w, receipt=receipt) for w in snapshot.get("words", [])]
    return dict(snapshot, words=words)


def line_key(line: list[dict]) -> str | None:
    """Run-stable identity of one line of words, or None.

    Snapshot / corpus words: ``<image_id>#<receipt_id>/w:<line_id>.<word_id>,
    ...``; word ids restart on every OCR line, so each id is qualified by its
    line. Pipeline tokens: ``<receipt_key>/t:<index>,...``. Ids are sorted, so
    the key ignores word order and depends only on which words the line holds
    (a row regrouped by overlap from two OCR lines gets its own key).
    """
    receipts = {str(w.get("receipt") or "") for w in line}
    if len(receipts) != 1 or "" in receipts:
        return None
    receipt = receipts.pop()
    if all("index" in w for w in line):
        idx = sorted(int(w["index"]) for w in line)
        return f"{receipt}/t:" + ",".join(str(i) for i in idx)
    if all("line_id" in w and "word_id" in w for w in line):
        ids = sorted((int(w["line_id"]), int(w["word_id"])) for w in line)
        return f"{receipt}/w:" + ",".join(f"{a}.{b}" for a, b in ids)
    return None


def truth_source(source: str) -> str:
    """Audit source name -> truth ``source`` (``snapshot-rows`` and
    ``corpus-rows`` are regroupings of ``snapshot`` / ``corpus`` words)."""
    return source.split("-", 1)[0]


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
    # Every classified row in reading order (not serialized: report.json
    # keeps its M1 shape; the template and scorer read these).
    rows: list[dict] = field(default_factory=list, repr=False)

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
        "line_key": line_key(line),
    }


def audit_lines(
    source: str, merchant: str, lines: list[list[dict]]
) -> MerchantAudit:
    audit = MerchantAudit(source=source, merchant=merchant)
    for line in lines:
        row = classify_line(line, merchant)
        audit.lines += 1
        audit.rows.append(row)
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
    corpus_dir: str | None = None,
) -> list[tuple[str, str, list[list[dict]]]]:
    """``(source, merchant, lines)`` for every committed labeled receipt.

    ``snapshot_grouping`` is ``line_id`` (OCR lines) or ``overlap`` (visual
    rows, as for the pipeline receipts). ``corpus_dir`` adds one
    ``corpus`` entry per ``<corpus_dir>/<slug>/`` holding the lines of all
    its ``*.json`` snapshots, receipts in file-name order; without it the
    output is unchanged.
    """
    group = snapshot_rows if snapshot_grouping == "overlap" else snapshot_lines
    suffix = "" if snapshot_grouping == "line_id" else "-rows"
    out = []
    if which in ("all", "snapshot"):
        for path in sorted(glob.glob(os.path.join(snapshot_dir, "*.json"))):
            with open(path, encoding="utf-8") as fh:
                snap = json.load(fh)
            slug = snap.get("slug") or os.path.basename(path)[:-5]
            if merchants and slug not in merchants:
                continue
            out.append(
                ("snapshot" + suffix, slug, group(tag_snapshot_receipt(snap)))
            )
    if which in ("all", "pipeline"):
        pattern = os.path.join(pipeline_dir, "*", "final.labels.json")
        for path in sorted(glob.glob(pattern)):
            slug = os.path.basename(os.path.dirname(path))
            if merchants and slug not in merchants:
                continue
            with open(path, encoding="utf-8") as fh:
                labels = json.load(fh)
            out.append(("pipeline", slug, pipeline_lines(labels)))
    if corpus_dir and which in ("all", "corpus"):
        for merchant_dir in sorted(glob.glob(os.path.join(corpus_dir, "*"))):
            slug = os.path.basename(merchant_dir)
            if not os.path.isdir(merchant_dir):
                continue
            if merchants and slug not in merchants:
                continue
            lines: list[list[dict]] = []
            paths = sorted(glob.glob(os.path.join(merchant_dir, "*.json")))
            for path in paths:
                with open(path, encoding="utf-8") as fh:
                    snap = json.load(fh)
                lines += group(tag_snapshot_receipt(snap))
            if paths:
                out.append(("corpus" + suffix, slug, lines))
    return out


def run_audit(sources) -> list[MerchantAudit]:
    return [audit_lines(src, slug, lines) for src, slug, lines in sources]


# --- adjudicated truth (stage S1) -----------------------------------------


def validate_truth_record(rec: dict, where: str = "record") -> dict:
    """Return ``rec`` or raise ``ValueError`` naming the bad field."""
    if not isinstance(rec, dict):
        raise ValueError(f"{where}: not an object")
    missing = [f for f in TRUTH_FIELDS if f not in rec]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    if rec["source"] not in TRUTH_SOURCES:
        raise ValueError(f"{where}: source {rec['source']!r}")
    for key in ("merchant", "line_key"):
        if not isinstance(rec[key], str) or not rec[key]:
            raise ValueError(f"{where}: {key} must be a non-empty string")
    if rec["role"] is not None and rec["role"] not in TRUTH_ROLES:
        raise ValueError(f"{where}: role {rec['role']!r} not in TRUTH_ROLES")
    if rec["role"] is not None and not rec["adjudicated_by"]:
        raise ValueError(f"{where}: a filled role needs adjudicated_by")
    return rec


def read_truth_file(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            where = f"{os.path.basename(path)}:{lineno}"
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            records.append(validate_truth_record(rec, where))
    return records


def load_truth(truth_dir: str) -> dict[tuple[str, str], dict]:
    """``(source, line_key) -> record`` over every ``*.jsonl`` in the dir.

    A key adjudicated twice is an error, not a silent last-wins.
    """
    truth: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(os.path.join(truth_dir, "*.jsonl"))):
        for rec in read_truth_file(path):
            key = (rec["source"], rec["line_key"])
            if key in truth:
                raise ValueError(f"{path}: duplicate line_key {key[1]}")
            truth[key] = rec
    return truth


def _template_record(audit: MerchantAudit, i: int) -> dict:
    row = audit.rows[i]
    prev_text = audit.rows[i - 1]["text"] if i > 0 else None
    next_text = audit.rows[i + 1]["text"] if i + 1 < len(audit.rows) else None
    return {
        "source": truth_source(audit.source),
        "merchant": audit.merchant,
        "line_key": row["line_key"],
        "text": row["text"],
        "role": None,
        "note": "",
        "adjudicated_by": None,
        "date": None,
        "pattern": f"{row['label_role']}/{row['regex_role']}",
        "context": {"prev": prev_text, "next": next_text},
    }


def template_records(
    audits: list[MerchantAudit], per_pattern: int
) -> dict[str, list[dict]]:
    """Per merchant, the disagreeing lines as ``role: null`` truth records.

    Patterns ``(label_role, regex_role)`` are ranked by how many lines they
    hold for that merchant (all sources together), most first; at most
    ``per_pattern`` lines each (``0`` = no cap), in source then reading
    order. Lines without a ``line_key`` cannot be adjudicated and are skipped.
    """
    by_merchant: dict[str, list[tuple[tuple[str, str], dict]]] = {}
    for a in audits:
        for i, row in enumerate(a.rows):
            if row["verdict"] not in ("label_wins", "regex_wins"):
                continue
            if row["line_key"] is None:
                continue
            pattern = (row["label_role"], row["regex_role"])
            by_merchant.setdefault(a.merchant, []).append(
                (pattern, _template_record(a, i))
            )
    out: dict[str, list[dict]] = {}
    for merchant, items in by_merchant.items():
        freq: dict[tuple[str, str], int] = {}
        for pattern, _ in items:
            freq[pattern] = freq.get(pattern, 0) + 1
        ranked = sorted(freq, key=lambda p: (-freq[p], p))
        records = []
        for pattern in ranked:
            picked = [rec for p, rec in items if p == pattern]
            records += picked[:per_pattern] if per_pattern > 0 else picked
        out[merchant] = records
    return out


def write_templates(
    audits: list[MerchantAudit], truth_dir: str, per_pattern: int
) -> list[tuple[str, int, int]]:
    """Write ``<truth_dir>/<slug>.jsonl`` per merchant; returns
    ``(path, new_records, kept_records)``.

    Re-running never loses an adjudication: a line already in the file keeps
    its record verbatim (in the new ranked position), and records no longer
    selected (the audit moved, or the cap shrank) are kept at the end.
    """
    os.makedirs(truth_dir, exist_ok=True)
    written = []
    for merchant, fresh in sorted(
        template_records(audits, per_pattern).items()
    ):
        path = os.path.join(truth_dir, f"{merchant}.jsonl")
        existing = read_truth_file(path) if os.path.exists(path) else []
        old = {(r["source"], r["line_key"]): r for r in existing}
        out, seen, new = [], set(), 0
        for rec in fresh:
            key = (rec["source"], rec["line_key"])
            if key in old:
                out.append(old[key])
            else:
                out.append(rec)
                new += 1
            seen.add(key)
        out += [
            r for r in existing if (r["source"], r["line_key"]) not in seen
        ]
        with open(path, "w", encoding="utf-8") as fh:
            for rec in out:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written.append((path, new, len(out) - new))
    return written


def fallback_role(label: str, regex: str) -> str:
    """Label role, or the regex role where no label bears a role."""
    return regex if label == LABEL_ROLE_UNLABELED else label


CLASSIFIERS = ("label", "regex", "fallback")


@dataclass
class TruthScore:
    merchant: str
    scored: int = 0
    null: int = 0
    correct: dict[str, int] = field(
        default_factory=lambda: {c: 0 for c in CLASSIFIERS}
    )
    # classifier -> truth role -> predicted role -> lines
    confusion: dict[str, dict[str, dict[str, int]]] = field(
        default_factory=lambda: {c: {} for c in CLASSIFIERS}
    )
    stale_text: list[str] = field(default_factory=list)

    def add(self, truth: str, predicted: dict[str, str]) -> None:
        self.scored += 1
        for c in CLASSIFIERS:
            p = predicted[c]
            self.correct[c] += int(p == truth)
            cell = self.confusion[c].setdefault(truth, {})
            cell[p] = cell.get(p, 0) + 1

    def merge(self, other: "TruthScore") -> None:
        self.scored += other.scored
        self.null += other.null
        self.stale_text += other.stale_text
        for c in CLASSIFIERS:
            self.correct[c] += other.correct[c]
            for truth, cols in other.confusion[c].items():
                cell = self.confusion[c].setdefault(truth, {})
                for p, n in cols.items():
                    cell[p] = cell.get(p, 0) + n

    def rate(self, classifier: str) -> float | None:
        if not self.scored:
            return None
        return round(self.correct[classifier] / self.scored, 4)

    def role_correct(self, classifier: str) -> dict[str, int]:
        return {
            truth: cols.get(truth, 0)
            for truth, cols in self.confusion[classifier].items()
        }

    def gate(self) -> tuple[str, list[str]]:
        """Proposed S3 gate: ``(PASS|FAIL|NO TRUTH, reasons)``."""
        if not self.scored:
            return "NO TRUTH", ["no adjudicated lines"]
        reasons = []
        fb, rx = self.correct["fallback"], self.correct["regex"]
        if fb < rx:
            reasons.append(f"label+fallback {fb} < regex {rx} lines")
        fb_role = self.role_correct("fallback")
        for role, n in sorted(self.role_correct("regex").items()):
            deficit = n - fb_role.get(role, 0)
            if deficit > S3_GATE_MAX_ROLE_DEFICIT:
                reasons.append(
                    f"{role}: label+fallback {deficit} lines worse "
                    f"(> {S3_GATE_MAX_ROLE_DEFICIT})"
                )
        return ("FAIL" if reasons else "PASS"), reasons

    def to_json(self) -> dict:
        status, reasons = self.gate()
        return {
            "merchant": self.merchant,
            "scored": self.scored,
            "null": self.null,
            "correct": self.correct,
            "agreement": {c: self.rate(c) for c in CLASSIFIERS},
            "confusion": self.confusion,
            "gate": {"status": status, "reasons": reasons},
            "stale_text": self.stale_text,
        }


def score_audits(
    audits: list[MerchantAudit], truth: dict[tuple[str, str], dict]
) -> tuple[list[TruthScore], TruthScore, list[str]]:
    """Per-merchant scores (sorted by slug), the overall score, and the
    truth keys no audited line matched (stale or from sources not loaded).
    """
    scores: dict[str, TruthScore] = {}
    matched: set[tuple[str, str]] = set()
    for a in audits:
        sc = scores.setdefault(a.merchant, TruthScore(a.merchant))
        for row in a.rows:
            key = (truth_source(a.source), row["line_key"])
            rec = truth.get(key)
            if rec is None or rec["merchant"] != a.merchant:
                continue
            matched.add(key)
            if rec["text"] != row["text"]:
                sc.stale_text.append(row["line_key"])
            if rec["role"] is None:
                sc.null += 1
                continue
            sc.add(
                rec["role"],
                {
                    "label": row["label_role"],
                    "regex": row["regex_role"],
                    "fallback": fallback_role(
                        row["label_role"], row["regex_role"]
                    ),
                },
            )
    overall = TruthScore("all")
    per_merchant = [scores[m] for m in sorted(scores)]
    for sc in per_merchant:
        overall.merge(sc)
    unmatched = sorted(
        k[1]
        for k, rec in truth.items()
        if k not in matched and rec["merchant"] in scores
    )
    return per_merchant, overall, unmatched


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


def _count_pct(score: TruthScore, classifier: str) -> str:
    return f"{score.correct[classifier]} ({_pct(score.rate(classifier))})"


def score_table(scores: list[TruthScore], overall: TruthScore) -> list[str]:
    rows = [
        "| merchant | scored | null | label | regex | label+fallback "
        "| S3 gate |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for sc in scores:
        status, _ = sc.gate()
        rows.append(
            f"| {sc.merchant} | {sc.scored} | {sc.null} "
            f"| {_count_pct(sc, 'label')} | {_count_pct(sc, 'regex')} "
            f"| {_count_pct(sc, 'fallback')} | {status} |"
        )
    rows.append(
        f"| **all** | **{overall.scored}** | **{overall.null}** "
        f"| **{_count_pct(overall, 'label')}** "
        f"| **{_count_pct(overall, 'regex')}** "
        f"| **{_count_pct(overall, 'fallback')}** | |"
    )
    return rows


def truth_confusion_table(score: TruthScore, classifier: str) -> list[str]:
    conf = score.confusion[classifier]
    truths = [r for r in ROLE_ORDER if r in conf]
    preds = [r for r in ROLE_ORDER if any(r in c for c in conf.values())]
    rows = [
        f"| truth \\ {classifier} | " + " | ".join(preds) + " | correct |",
        "|---|" + "---:|" * (len(preds) + 1),
    ]
    for t in truths:
        cells = []
        for p in preds:
            n = conf[t].get(p, 0)
            cells.append(f"**{n}**" if n and t == p else str(n or ""))
        total = sum(conf[t].values())
        rows.append(
            f"| {t} | " + " | ".join(cells) + f" | {conf[t].get(t, 0)}"
            f"/{total} |"
        )
    return rows


def gate_lines(scores: list[TruthScore]) -> list[str]:
    out = [
        "S3 gate threshold: label+fallback >= regex, max role deficit "
        f"{S3_GATE_MAX_ROLE_DEFICIT} [{S3_GATE_STATUS}]"
    ]
    for sc in scores:
        status, reasons = sc.gate()
        why = (
            "; ".join(reasons)
            if reasons
            else (
                f"label+fallback {sc.correct['fallback']} >= regex "
                f"{sc.correct['regex']}, no role worse by "
                f"> {S3_GATE_MAX_ROLE_DEFICIT}"
            )
        )
        out.append(f"S3 gate {sc.merchant}: {status} ({why})")
    return out


def render_score_markdown(
    scores: list[TruthScore], overall: TruthScore, unmatched: list[str]
) -> str:
    out = [
        "# Section-role agreement against adjudicated truth (stage S1)",
        "",
        "Per merchant, over audited lines whose truth record has a non-null "
        "role: lines each classifier gets right. `label` is "
        "`_classify_from_labels` (unlabeled counts as wrong); `regex` is "
        "`_classify` projected to roles; `label+fallback` is the label role, "
        "or the regex role where no label bears one. `null` = adjudicated "
        "undecidable, not scored.",
        "",
        f"S3 gate threshold [{S3_GATE_STATUS}]: PASS when label+fallback "
        "agreement >= regex agreement and no truth role has label+fallback "
        f"more than {S3_GATE_MAX_ROLE_DEFICIT} lines worse "
        "(`S3_GATE_MAX_ROLE_DEFICIT`).",
        "",
        "## Agreement",
        "",
        *score_table(scores, overall),
        "",
        "## S3 gate",
        "",
        "```",
        *gate_lines(scores),
        "```",
    ]
    if unmatched:
        out += [
            "",
            f"{len(unmatched)} truth record(s) matched no audited line "
            "(stale line_key, or a source not loaded):",
            "",
            *[f"- `{k}`" for k in unmatched],
        ]
    stale = overall.stale_text
    if stale:
        out += [
            "",
            f"{len(stale)} truth record(s) whose text differs from the "
            "audited line (OCR changed; re-adjudicate):",
            "",
            *[f"- `{k}`" for k in stale],
        ]
    for sc in [overall, *scores]:
        if not sc.scored:
            continue
        out += ["", f"## {sc.merchant}"]
        for c in ("label", "regex", "fallback"):
            out += ["", *truth_confusion_table(sc, c)]
    return "\n".join(out) + "\n"


def write_score_report(
    scores: list[TruthScore],
    overall: TruthScore,
    unmatched: list[str],
    out_dir: str,
) -> tuple[str, str, str]:
    os.makedirs(out_dir, exist_ok=True)
    md = render_score_markdown(scores, overall, unmatched)
    md_path = os.path.join(out_dir, "report.md")
    json_path = os.path.join(out_dir, "report.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "gate": {
                    "status": S3_GATE_STATUS,
                    "max_role_deficit": S3_GATE_MAX_ROLE_DEFICIT,
                },
                "merchants": [sc.to_json() for sc in scores],
                "overall": overall.to_json(),
                "unmatched": unmatched,
            },
            fh,
            indent=2,
        )
        fh.write("\n")
    return md, md_path, json_path


# --- CLI ------------------------------------------------------------------


def _source_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--snapshots-dir", default=SNAPSHOT_DIR)
    ap.add_argument("--pipeline-dir", default=PIPELINE_DIR)
    ap.add_argument(
        "--corpus-dir",
        default=None,
        help="also audit DIR/<slug>/*.json source snapshots (source corpus)",
    )
    ap.add_argument(
        "--source",
        choices=("all", "snapshot", "pipeline", "corpus"),
        default="all",
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


def _audits_from_args(args) -> list[MerchantAudit]:
    return run_audit(
        load_sources(
            args.snapshots_dir,
            args.pipeline_dir,
            args.source,
            set(args.merchant) if args.merchant else None,
            args.snapshot_grouping,
            args.corpus_dir,
        )
    )


def main_template(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="label_role_audit adjudicate-template",
        description="Write disagreeing lines as role:null truth records.",
    )
    _source_args(ap)
    ap.add_argument("--truth-dir", default=TRUTH_DIR)
    ap.add_argument(
        "--per-pattern",
        type=int,
        default=20,
        help="lines kept per (label_role, regex_role) pattern per "
        "merchant; 0 = all",
    )
    args = ap.parse_args(argv)
    written = write_templates(
        _audits_from_args(args), args.truth_dir, args.per_pattern
    )
    for path, new, kept in written:
        print(f"wrote {path} ({new} new, {kept} kept)")
    return 0


def main_score(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="label_role_audit score",
        description="Score both classifiers against adjudicated truth.",
    )
    _source_args(ap)
    ap.add_argument("--truth", default=TRUTH_DIR, help="truth JSONL dir")
    ap.add_argument("--out-dir", default=os.path.join(OUT_DIR, "score"))
    args = ap.parse_args(argv)
    truth = load_truth(args.truth)
    scores, overall, unmatched = score_audits(_audits_from_args(args), truth)
    md, md_path, json_path = write_score_report(
        scores, overall, unmatched, args.out_dir
    )
    print(md)
    print(f"wrote {md_path}\nwrote {json_path}")
    return 0


SUBCOMMANDS = {
    "adjudicate-template": main_template,
    "score": main_score,
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return SUBCOMMANDS[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="subcommands: " + ", ".join(SUBCOMMANDS),
    )
    _source_args(ap)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument(
        "--samples",
        type=int,
        default=8,
        help="disagreeing lines shown per merchant",
    )
    args = ap.parse_args(argv)
    audits = _audits_from_args(args)
    md, md_path, json_path = write_report(audits, args.out_dir, args.samples)
    print(md)
    print(f"wrote {md_path}\nwrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
