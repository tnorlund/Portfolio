"""Zhang–Suen skeletonization + spur pruning, pure numpy.

scipy/scikit-image are deliberately absent from the venv; the thinning
conditions are computed vectorized over the whole array with padded shifts.
"""
from __future__ import annotations

import numpy as np


def _neighbors(img: np.ndarray) -> list[np.ndarray]:
    """P2..P9 (clockwise from north) for every pixel, via padded shifts."""
    p = np.pad(img, 1)
    return [
        p[:-2, 1:-1],   # P2 N
        p[:-2, 2:],     # P3 NE
        p[1:-1, 2:],    # P4 E
        p[2:, 2:],      # P5 SE
        p[2:, 1:-1],    # P6 S
        p[2:, :-2],     # P7 SW
        p[1:-1, :-2],   # P8 W
        p[:-2, :-2],    # P9 NW
    ]


def zhang_suen(mask: np.ndarray, max_iters: int = 200) -> np.ndarray:
    """1-px skeleton of a boolean ink mask."""
    img = mask.astype(np.uint8).copy()
    for _ in range(max_iters):
        changed = False
        for pass_two in (False, True):
            n = _neighbors(img)
            b = sum(n)
            seq = n + [n[0]]
            a = sum(
                ((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
                for i in range(8)
            )
            if not pass_two:
                cond = (n[0] * n[2] * n[4] == 0) & (n[2] * n[4] * n[6] == 0)
            else:
                cond = (n[0] * n[2] * n[6] == 0) & (n[0] * n[4] * n[6] == 0)
            kill = (
                (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & cond
            )
            if kill.any():
                img[kill] = 0
                changed = True
        if not changed:
            break
    return img.astype(bool)


def degree_map(skel: np.ndarray) -> np.ndarray:
    """8-neighbor ink count for every skeleton pixel (0 elsewhere)."""
    img = skel.astype(np.uint8)
    deg = sum(_neighbors(img))
    deg[~skel] = 0
    return deg


def _ink_nbrs(mask: np.ndarray, y: int, x: int) -> list[tuple[int, int]]:
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx]:
                out.append((ny, nx))
    return out


def _mutually_connected(pixels: list[tuple[int, int]]) -> bool:
    """True if the pixel set is 8-connected among themselves."""
    if len(pixels) <= 1:
        return True
    seen = {pixels[0]}
    frontier = [pixels[0]]
    rest = set(pixels[1:])
    while frontier:
        cy, cx = frontier.pop()
        for p in list(rest):
            if abs(p[0] - cy) <= 1 and abs(p[1] - cx) <= 1:
                rest.discard(p)
                seen.add(p)
                frontier.append(p)
    return not rest


def prune_spurs(skel: np.ndarray, min_len: float) -> np.ndarray:
    """Remove endpoint branches shorter than ``min_len`` pixels.

    Walks inward from each degree-1 endpoint until reaching the attachment
    pixel — the first pixel with >= 2 ink neighbors outside the walked path.
    The path is deleted; the attachment pixel itself is also deleted when its
    external neighbors stay mutually 8-connected without it (thinning smears
    junctions so the attachment usually sits one pixel proud of the stroke).
    Real stroke ends survive because their walk exceeds ``min_len``.
    """
    out = skel.copy()
    limit = max(1, int(round(min_len)))
    for _ in range(8):
        deg = degree_map(out)
        endpoints = np.argwhere((deg == 1) & out)
        removed_any = False
        for y0, x0 in endpoints:
            if not out[y0, x0]:
                continue  # removed earlier this pass
            path = [(int(y0), int(x0))]
            attachment = None
            while len(path) <= limit:
                cur = path[-1]
                ext = [p for p in _ink_nbrs(out, *cur) if p not in path]
                if len(ext) == 0:
                    break  # isolated segment; leave it
                if len(ext) >= 2:
                    attachment = cur
                    break
                path.append(ext[0])
            if attachment is None:
                continue
            ext = [p for p in _ink_nbrs(out, *attachment) if p not in path]
            drop = path if _mutually_connected(ext) else path[:-1]
            if not drop:
                continue
            for py, px in drop:
                out[py, px] = False
            removed_any = True
        if not removed_any:
            break
    return out
