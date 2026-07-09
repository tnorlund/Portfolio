#!/usr/bin/env python3
"""Fleet glyph-identity audit -> ranked handcraft queue.

Usage: python glyph_gate_cli.py [--fonts DIR]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyphstudio.glyph_gate import audit_fleet  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument(
        "--fonts", default=os.path.abspath(os.path.join(here, "..", "fonts"))
    )
    args = ap.parse_args(argv)
    font_dirs = {
        n: os.path.join(args.fonts, n)
        for n in sorted(os.listdir(args.fonts))
        if os.path.exists(os.path.join(args.fonts, n, "font.json"))
    }
    findings = audit_fleet(font_dirs)
    print(f"{len(findings)} findings across {len(font_dirs)} merchants\n")
    for f in findings:
        print(f"{f.kind:14s} {f.merchant:12s} {f.char!r:5s} {f.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
