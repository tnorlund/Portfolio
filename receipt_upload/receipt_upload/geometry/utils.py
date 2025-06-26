"""
Utility functions for geometric operations.
"""

from typing import Dict, List, Tuple


def theil_sen(pts: List[Tuple[float, float]]) -> Dict[str, float]:
    """Perform Theil–Sen regression to estimate a line."""
    if len(pts) < 2:
        return {"slope": 0.0, "intercept": pts[0][1] if pts else 0.0}

    slopes: List[float] = []
    for i, pt_i in enumerate(pts):
        for j in range(i + 1, len(pts)):
            if pt_i[1] == pts[j][1]:
                continue
            slopes.append((pts[j][0] - pt_i[0]) / (pts[j][1] - pt_i[1]))

    if not slopes:
        return {"slope": 0.0, "intercept": pts[0][1]}

    slopes.sort()
    slope = slopes[len(slopes) // 2]

    intercepts = sorted(p[0] - slope * p[1] for p in pts)
    intercept = intercepts[len(intercepts) // 2]

    return {"slope": slope, "intercept": intercept}