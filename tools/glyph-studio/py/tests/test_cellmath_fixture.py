"""Guard: the committed cell-math fixture matches the CURRENT renderer code.

If bitmap_font.py's derivation or scaling changes, this fails and the fixture
(and the TS port) must be regenerated — that's the WYSIWYG parity contract.
"""
import json
import os

from glyphstudio.cellmath import emit

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "fixtures",
    "cellmath_cases.json",
)


def test_fixture_up_to_date(tmp_path):
    committed = json.load(open(FIXTURE, encoding="utf-8"))
    regenerated = emit(str(tmp_path / "cellmath_cases.json"))
    assert committed["cases"] == regenerated["cases"], (
        "cellmath fixture is stale — regenerate with "
        "`python -m glyphstudio.cellmath --emit fixtures/cellmath_cases.json` "
        "and update app/src/cellMath.ts if the renderer math changed"
    )
