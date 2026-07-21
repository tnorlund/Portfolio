#!/usr/bin/env python3.12
"""Build the staged Dollar Tree atlas: bitMatrix-B2 chart + double-strike.

The raw B2 ROM renders ~0.69 of the real receipts' ink density (the dev
sources are dark flash photos). A 1px right+down double-strike (the thermal
double-print) lifts it to 0.81, the practical ceiling before letterforms
distort. Usage:

  build_dollartree_font.py <B2_chart.png> <out.npz>

Chart source: receiptfont.com specimen 626436-bitMatrix-B2.png (626x436).
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(chart: str, out_npz: str) -> int:
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="b2_")
    subprocess.run(
        [
            sys.executable,
            os.path.join(HERE, "extract_bitmatrix.py"),
            chart,
            tmp,
            "bitMatrix-B2",
        ],
        check=True,
    )
    src = np.load(os.path.join(tmp, "bitMatrix-B2.glyphs.npz"))
    dilated = {}
    for k in src.files:
        a = src[k]
        if k.startswith("c") and a.ndim == 2:
            b = a.astype(bool)
            s = b.copy()
            s[:, 1:] |= b[:, :-1]
            s[1:, :] |= b[:-1, :]
            dilated[k] = s.astype(a.dtype)
        else:
            dilated[k] = a
    np.savez_compressed(out_npz, **dilated)
    print(f"wrote {out_npz} (double-struck B2)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
