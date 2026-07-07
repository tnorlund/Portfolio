"""Costco logo asset: manifest agrees with the committed SVG."""
import json
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "receipt_logo" / "assets" / "merchant_logos"


def test_costco_manifest_matches_svg():
    manifest = json.loads((ASSETS / "costco_wholesale.manifest.json").read_text())
    svg = (ASSETS / manifest["assets"]["color_svg"]).read_text()
    vb = re.search(r'viewBox="([\d. ]+)"', svg).group(1).split()
    assert float(vb[2]) == manifest["source_dimensions"]["width"]
    assert float(vb[3]) == manifest["source_dimensions"]["height"]
    assert manifest["source_kind"] == "vector"
    paths = len(re.findall(r"<path", svg))
    assert paths == manifest["svg_path_count"] > 0
    for fill in manifest["palette"]:
        assert fill.startswith("#")
