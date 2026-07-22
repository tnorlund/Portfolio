# full_fidelity_eval -- costco_wholesale

- merchant: COSTCO WHOLESALE
- receipt: 01c0bc98-4fdc-4f7d-baf6-72c2e3a9564d#1
- git: `f137a174ee41`
- truth: `costco_wholesale` v1 `c5cd31202f7a52cee5f5b492c675ae4ed1d94241529bdc3d16d85b18bf7fd064` (online-active)
- atlas: `3d0a24ac0ba6e9bb`

## OVERALL: FAIL

| metric | verdict |
|---|---|
| columns | FAIL |
| style | PASS_WITH_GAPS |
| tokens | FAIL |
| separators | FAIL |
| graphics | SKIPPED |
| logo | FAIL |
| arithmetic | FAIL |

```json
{
 "arithmetic": {
  "identities": [
   {
    "detail": "6 items",
    "lhs": 233.98,
    "name": "sum_lines_eq_subtotal",
    "rhs": 110.29,
    "status": "VIOLATED"
   },
   {
    "detail": "",
    "lhs": 116.99,
    "name": "subtotal_plus_tax_eq_total",
    "rhs": 116.99,
    "status": "HOLDS"
   }
  ],
  "n_items": 6,
  "summary": {
   "change": 0.0,
   "subtotal": 110.29,
   "tax": 6.7,
   "total": 116.99
  },
  "testable": 2,
  "verdict": "FAIL",
  "violated": 1
 },
 "columns": {
  "bands": {
   "ALL": {
    "cell_w_px": 20.03,
    "columns": [
     {
      "abs_drift_limit_px": 30.05,
      "abs_drift_px": 1.0,
      "column": {
       "anchor": "right",
       "role": "amount",
       "spread": 0.0122,
       "support": 10,
       "x": 0.9461
      },
      "failed_on": [
       "wobble",
       "outliers"
      ],
      "outlier_limit": 0.35,
      "real": {
       "lane_mid_y_px": 664.8,
       "lane_x_px": 713.02,
       "median_dev_px": -5.04,
       "n_rows": 10,
       "outlier_frac": 0.2,
       "tilt_px_per_100px": -1.142,
       "wobble_iqr_px": 2.01
      },
      "shear_px_per_100px": 0.01,
      "synth": {
       "lane_mid_y_px": 664.8,
       "lane_x_px": 715.46,
       "median_dev_px": -6.04,
       "n_rows": 10,
       "outlier_frac": 0.4,
       "tilt_px_per_100px": -1.134,
       "wobble_iqr_px": 30.01
      },
      "verdict": "FAIL",
      "wobble_limit_px": 6.52
     }
    ],
    "lane_gaps": [],
    "source": "bootstrap",
    "untested_roles": [],
    "verdict": "FAIL"
   }
  },
  "verdict": "FAIL"
 },
 "coverage_gaps": [
  "graphics",
  "style",
  "style:barcode_caption",
  "style:transaction"
 ],
 "graphics": {
  "note": "barcode detector unavailable (/Users/tnorlund/Portfolio/.claude/worktrees/merchant-truth-gate-records/receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr)",
  "verdict": "SKIPPED"
 },
 "logo": {
  "area_ratio": 1.309,
  "center_offset_frac": 0.1691,
  "real": {
   "area": 9320.0,
   "cx": 168.0,
   "cy": 57.5,
   "h": 108.0,
   "w": 155.0
  },
  "size_ratio": 0.806,
  "synth": {
   "area": 12203.0,
   "cx": 296.5,
   "cy": 109.0,
   "h": 87.0,
   "w": 204.0
  },
  "verdict": "FAIL",
  "width_ratio": 1.316
 },
 "overall": "FAIL",
 "separators": {
  "kind_mismatches": 0,
  "matched": [
   {
    "dy": 0.006,
    "real": {
     "height_px": 5,
     "kind": "dash",
     "y_frac": 0.3563
    },
    "synth": {
     "height_px": 3,
     "kind": "dash",
     "y_frac": 0.3503
    }
   },
   {
    "dy": 0.009,
    "real": {
     "height_px": 4,
     "kind": "dash",
     "y_frac": 0.5817
    },
    "synth": {
     "height_px": 3,
     "kind": "dash",
     "y_frac": 0.5907
    }
   }
  ],
  "missing_in_synth": [],
  "phantom_in_synth": [
   {
    "height_px": 7,
    "kind": "dash",
    "y_frac": 0.7563
   },
   {
    "height_px": 8,
    "kind": "dash",
    "y_frac": 0.7713
   }
  ],
  "real_count": 2,
  "synth_count": 4,
  "verdict": "FAIL"
 },
 "style": {
  "body_stroke_fail": false,
  "body_stroke_rel": {
   "real": 0.1548,
   "synth": 0.1048
  },
  "classes": [
   {
    "class": "barcode_caption",
    "real": {
     "bold": 0,
     "n": 1,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 1,
     "underline": 0
    },
    "verdict": "UNTESTED"
   },
   {
    "class": "payment",
    "real": {
     "bold": 0,
     "n": 4,
     "underline": 0
    },
    "synth": {
     "bold": 1,
     "n": 5,
     "underline": 0
    },
    "verdict": "PASS"
   },
   {
    "class": "summary",
    "real": {
     "bold": 0,
     "n": 2,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 2,
     "underline": 0
    },
    "verdict": "PASS"
   },
   {
    "class": "total_line",
    "real": {
     "bold": 0,
     "n": 3,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 3,
     "underline": 1
    },
    "verdict": "PASS"
   },
   {
    "class": "transaction",
    "real": {
     "bold": 0,
     "n": 1,
     "underline": 0
    },
    "synth": {
     "bold": 1,
     "n": 2,
     "underline": 0
    },
    "verdict": "UNTESTED"
   }
  ],
  "untested_classes": [
   "barcode_caption",
   "transaction"
  ],
  "verdict": "PASS_WITH_GAPS"
 },
 "tokens": {
  "composed": false,
  "ink_checked": 112,
  "ink_evidence_missing": false,
  "ink_missing_tokens": [
   "2619",
   "WHSKY",
   "A",
   "KEYED",
   "XXXXXXXXXXXX1454",
   "H",
   "BY",
   "PIN",
   "APPROVED",
   "PURCHASE",
   "10",
   "241",
   "49",
   "EFT/DEBIT",
   "A",
   "TAX",
   "NU",
   "BE",
   "EMS",
   "SOLD",
   "513",
   "10",
   "116.9%",
   "OP#:",
   "49"
  ],
  "ink_recall": 0.7232,
  "missing_tokens": [
   "0672972026",
   "SEG#"
  ],
  "precision_warn": false,
  "text_precision": 0.9821,
  "text_recall": 0.9821,
  "verdict": "FAIL"
 }
}
```
