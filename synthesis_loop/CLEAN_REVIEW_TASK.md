# Codex review — synthesis content-cleaning pass

You are reviewing a NEW pass that repairs source-OCR noise in synthesized receipt tokens. Be skeptical and
concrete; read the code and the regenerated bundles before answering.

## Background
The synthesis assembles receipts from REAL receipt OCR tokens, which carry the source OCR's errors
('Subtota!::' for 'Subtotal:', 'amasen.com' for 'amazon.com', 'briet' for 'brief'). Those errors were getting
baked into the ground truth and rendered as if printed. The principle: GROUND TRUTH should be CLEAN; realistic
OCR noise belongs in a downstream re-OCR pass, not baked into the GT.

## What to read (this worktree, ~/Portfolio_content)
- `receipt_agent/receipt_agent/agents/label_evaluator/synthesis_text_clean.py` — the new pass `clean_token_text`
  (punctuation normalization + vocab repair against a receipt-word list with OCR-confusion-aware edit distance +
  merchant-domain normalization). It is wired as the 1st pass in `synthesis_reconcile.reconcile_candidate`.
- Regenerated bundles: `/tmp/clean/{amazon_fresh,costco_wholesale,sprouts_farmers_market}/bundle.json`
  (cleaned) vs `/tmp/mm/{...}/bundle.json` (old, garbled). Compare `synthetic_training_examples[*].tokens`.
- Rendered hybrids of the cleaned bundles: `/tmp/clean/<merchant>/*.hybrid.png` (you may inspect the images).

## Measured result so far
Amazon: garbled tokens 8 -> 3. Fixed: Subtota!::->Subtotal:, briet->brief, tax::->tax:, total::->total:,
www.amasen.com->www.amazon.com. Still garbled: '1-300-258-8668' (phone, 800 misread 300), 'amazon-com/
frashfeedback' (hyphen-for-dot + fresh->frash), 'amazon.com/freshreturnpolder' (policy->polder).
Costco/Sprouts: no tokens in our garble-pattern set (but SCAN them yourself for ones we missed).

## Answer concretely
1. Is the approach SOUND and SAFE? Read clean_token_text and look for OVER-CORRECTION risk: could the vocab
   repair or domain fix damage legitimate product names, brand tokens, addresses, or values? Find a concrete
   failure case if one exists (the repair is gated to <=1.0 OCR-aware edit distance and skips PRODUCT_NAME/
   amounts/IDs/PAYMENT_METHOD — is that gating correct and sufficient?).
2. Scan the cleaned bundles for ALL THREE merchants and list any REMAINING garbled/OCR-error tokens our
   pattern set missed (esp. in Costco/Sprouts). Which labels do they fall on?
3. The 3 remaining Amazon garbles — what is the highest-value, still-SAFE way to fix them (phone toll-free
   prefix correction; URL-path word repair against the vocab; 'brand-com'->'brand.com' normalization)? Any
   that are too risky to auto-fix?
4. Separate known bug: a 'remove-line-item' candidate shows "Total number of items: 6" with only 2 line items.
   Where should the item-count be reconciled — in the synthesis transform or a reconcile pass? Point at the code.
5. VERDICT: does this pass meaningfully improve synthesis content quality, and what is the single highest-value
   next step?

Output: a one-line verdict, then numbered answers, then a short prioritized list.
