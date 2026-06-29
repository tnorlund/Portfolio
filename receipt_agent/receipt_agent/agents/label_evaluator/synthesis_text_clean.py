"""synthesis_text_clean.py -- repair source-OCR noise in synthesized receipt tokens.

The synthesis assembles receipts from REAL receipt OCR tokens, which carry the source OCR's errors
(e.g. 'Subtota!::' for 'Subtotal:', 'amasen.com' for 'amazon.com', 'briet' for 'brief'). Those errors
get baked into the ground truth and rendered as if printed -- making the synthetic image look wrong AND
poisoning the labels. The principled split: GROUND TRUTH should be CLEAN (what the receipt actually
printed); realistic OCR noise belongs in the downstream re-OCR pass, not here.

This pass makes high-confidence, SAFE repairs only:
  1. punctuation normalization ('::'->':' , stray repeats)
  2. vocabulary repair: a standalone non-PRODUCT word that is OCR-close to a common receipt word is
     corrected to it ('Subtota!::'->'Subtotal:', 'briet'->'brief'). Skips amounts/IDs/product names.
  3. merchant-domain normalization: a URL/domain token OCR-close to the merchant's own name is fixed
     ('www.amasen.com'->'www.amazon.com'), driven off merchant_name (generalizable, not hardcoded).
"""
from __future__ import annotations
import re

RECEIPT_VOCAB = {
    "subtotal", "total", "tax", "sales", "change", "balance", "due", "purchase", "cash", "credit",
    "debit", "card", "member", "receipt", "thank", "you", "for", "shopping", "store", "hours", "open",
    "daily", "please", "come", "again", "save", "money", "paper", "brief", "survey", "return", "returns",
    "policy", "feedback", "emailed", "questions", "call", "customer", "service", "read", "scan", "code",
    "view", "your", "order", "online", "number", "items", "promo", "markdown", "savings", "ending",
    "approved", "cashier", "transaction", "reference", "auth", "account", "payment", "method", "amount",
    "tendered", "visa", "mastercard", "discover", "express", "verified", "terminal", "register", "date",
    "time", "phone", "welcome", "original", "weekly", "ads", "sign", "keep", "required", "without", "did",
    "how", "tell", "the", "and", "our", "gift", "win", "chance", "quick", "fresh", "market", "wholesale",
}
# OCR-confusable character pairs (symmetric); a substitution between these costs 0.5 instead of 1.0.
_CONF = [("l", "1"), ("l", "i"), ("l", "!"), ("l", "|"), ("i", "1"), ("i", "!"), ("o", "0"), ("s", "5"),
         ("s", "z"), ("e", "c"), ("a", "o"), ("n", "m"), ("r", "n"), ("c", "e"), ("g", "9"), ("b", "6"),
         ("t", "f"), ("u", "v"), ("a", "e"), ("h", "b")]
_CONF_SET = {frozenset(p) for p in _CONF}
_WORDISH = re.compile(r"^[A-Za-z!|0-9]+$")
# Real trailing punctuation to preserve. '!' and '|' are EXCLUDED: in OCR they are misread letters
# (l/i/1), so when we vocab-correct a word they belong to the word, not as trailing punctuation.
_TRAIL = re.compile(r"[:.,;]+$")


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.5 if frozenset((a.lower(), b.lower())) in _CONF_SET else 1.0


def _ocr_dist(a: str, b: str) -> float:
    n, m = len(a), len(b)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + _sub_cost(a[i - 1], b[j - 1]))
    return dp[n][m]


def _best_vocab(core_lower: str) -> str | None:
    if len(core_lower) < 3 or core_lower in RECEIPT_VOCAB:
        return None
    best, bestd = None, 99.0
    for w in RECEIPT_VOCAB:
        if abs(len(w) - len(core_lower)) > 2:
            continue
        d = _ocr_dist(core_lower, w)
        if d < bestd:
            bestd, best = d, w
    return best if best and bestd <= 1.0 else None


def _match_case(src: str, repl: str) -> str:
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl.capitalize()
    return repl


def _merchant_core(merchant_name: str | None) -> str | None:
    if not merchant_name:
        return None
    for w in re.split(r"\s+", merchant_name):
        wl = re.sub(r"[^A-Za-z]", "", w).lower()
        if len(wl) >= 5 and wl not in ("fresh", "market", "wholesale", "store", "farmers"):
            return wl  # the distinctive brand word (amazon, costco, sprouts, gelsons, ...)
    return None


def _fix_domain(token: str, brand: str) -> str:
    # find a dotted host segment OCR-close to the brand and replace it
    def repl(m):
        seg = m.group(0)
        return brand if 0 < _ocr_dist(seg.lower(), brand) <= 1.5 else seg
    return re.sub(r"[A-Za-z][A-Za-z0-9]{3,}", repl, token)


def clean_token_text(tokens, bboxes, ner_tags, merchant_name=None):
    """5th reconcile pass: repair source-OCR noise in the token TEXT. bboxes/tags unchanged."""
    brand = _merchant_core(merchant_name)
    out = []
    for tok, tag in zip(tokens, ner_tags):
        ent = tag.split("-", 1)[-1] if tag and tag != "O" else "O"
        t = str(tok)
        # 1. punctuation normalization
        t = re.sub(r"::+", ":", t)
        t = re.sub(r"([:.,;])\1+", r"\1", t)
        # 2. merchant-domain normalization (URL/domain tokens)
        if brand and (".com" in t.lower() or "www." in t.lower() or "://" in t.lower()):
            t = _fix_domain(t, brand)
        # 3. vocabulary repair -- only safe label classes (never amounts / ids / product names)
        if ent not in ("PRODUCT_NAME", "LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL", "PHONE_NUMBER",
                       "LOYALTY_ID", "PAYMENT_METHOD"):
            trail_m = _TRAIL.search(t)
            trail = trail_m.group(0) if trail_m else ""
            body = t[: len(t) - len(trail)] if trail else t
            core = re.sub(r"[^A-Za-z]", "", body)
            if body and _WORDISH.match(body) and core and not any(ch.isdigit() for ch in body):
                repl = _best_vocab(core.lower())
                if repl:
                    t = _match_case(core, repl) + trail
        out.append(t)
    return out, bboxes, ner_tags
