"""Skeleton pixels -> polylines (graph walk), pure numpy + python.

Endpoints have 8-degree 1, junctions >= 3. Thinning smears junctions into
2-3 px clumps, so adjacent junction pixels are contracted into single
junction clusters before edges are walked. Pure loops (O, 0) have no
endpoints/junctions and are walked from an arbitrary start, marked closed.
"""
from __future__ import annotations

import numpy as np

from .thin import degree_map

_OFFSETS = [
    (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)
]


def _ink_neighbors(skel: np.ndarray, y: int, x: int) -> list[tuple[int, int]]:
    out = []
    for dy, dx in _OFFSETS:
        ny, nx = y + dy, x + dx
        if 0 <= ny < skel.shape[0] and 0 <= nx < skel.shape[1] and skel[ny, nx]:
            out.append((ny, nx))
    return out


def _junction_clusters(
    skel: np.ndarray, deg: np.ndarray
) -> tuple[dict[tuple[int, int], int], list[list[tuple[int, int]]]]:
    """Contract 8-adjacent junction pixels into clusters."""
    pixel_to_cluster: dict[tuple[int, int], int] = {}
    clusters: list[list[tuple[int, int]]] = []
    for y, x in map(tuple, np.argwhere((deg >= 3) & skel)):
        if (y, x) in pixel_to_cluster:
            continue
        cid = len(clusters)
        stack = [(y, x)]
        members: list[tuple[int, int]] = []
        pixel_to_cluster[(y, x)] = cid
        while stack:
            cy, cx = stack.pop()
            members.append((cy, cx))
            for ny, nx in _ink_neighbors(skel, cy, cx):
                if deg[ny, nx] >= 3 and (ny, nx) not in pixel_to_cluster:
                    pixel_to_cluster[(ny, nx)] = cid
                    stack.append((ny, nx))
        clusters.append(members)
    return pixel_to_cluster, clusters


def extract_paths(skel: np.ndarray) -> list[dict]:
    """Return [{"points": [(y,x)...], "closed": bool}] covering the skeleton.

    Edges run endpoint/junction -> endpoint/junction through degree-2 chains.
    Junction clusters contribute their centroid as the shared end vertex so
    strokes meeting at a junction agree on a single point.
    """
    deg = degree_map(skel)
    pixel_to_cluster, clusters = _junction_clusters(skel, deg)
    centroids = [
        (float(np.mean([p[0] for p in c])), float(np.mean([p[1] for p in c])))
        for c in clusters
    ]

    visited = np.zeros_like(skel, dtype=bool)
    for (y, x) in pixel_to_cluster:
        visited[y, x] = True  # cluster pixels are vertices, not chain members

    paths: list[dict] = []

    def walk(start: tuple[int, int], first: tuple[int, int]) -> list[tuple[int, int]]:
        """Follow a degree-2 chain from ``first`` (start vertex excluded)."""
        pts = [first]
        visited[first] = True
        prev, cur = start, first
        while True:
            nbrs = [
                p for p in _ink_neighbors(skel, *cur)
                if p != prev and (p in pixel_to_cluster or not visited[p])
            ]
            nxt = None
            for p in nbrs:
                if p in pixel_to_cluster:
                    nxt = p
                    break
            if nxt is None and nbrs:
                nxt = nbrs[0]
            if nxt is None:
                break
            if nxt in pixel_to_cluster:
                pts.append(nxt)
                break
            visited[nxt] = True
            pts.append(nxt)
            prev, cur = cur, nxt
        return pts

    def vertex_point(p: tuple[int, int]) -> tuple[float, float]:
        if p in pixel_to_cluster:
            return centroids[pixel_to_cluster[p]]
        return (float(p[0]), float(p[1]))

    # 1) Chains from endpoints.
    for y, x in map(tuple, np.argwhere((deg == 1) & skel)):
        if visited[y, x]:
            continue
        pts = [(y, x)]
        visited[y, x] = True
        nbrs = [p for p in _ink_neighbors(skel, y, x)
                if p in pixel_to_cluster or not visited[p]]
        if nbrs:
            pts.extend(walk((y, x), nbrs[0]))
        points = [vertex_point(p) if p in pixel_to_cluster else (float(p[0]), float(p[1]))
                  for p in pts]
        if len(points) >= 2:
            paths.append({"points": points, "closed": False})

    # 2) Chains between junction clusters (edges not yet walked).
    for cid, members in enumerate(clusters):
        for my, mx in members:
            for ny, nx in _ink_neighbors(skel, my, mx):
                if (ny, nx) in pixel_to_cluster or visited[ny, nx]:
                    continue
                chain = walk((my, mx), (ny, nx))
                points = [centroids[cid]]
                for p in chain:
                    points.append(
                        vertex_point(p) if p in pixel_to_cluster
                        else (float(p[0]), float(p[1]))
                    )
                if len(points) >= 2:
                    paths.append({"points": points, "closed": False})

    # 3) Pure loops: any remaining unvisited skeleton pixels.
    remaining = np.argwhere(skel & ~visited)
    for y, x in map(tuple, remaining):
        if visited[y, x]:
            continue
        pts = [(y, x)]
        visited[y, x] = True
        prev: tuple[int, int] | None = None
        cur = (y, x)
        while True:
            nbrs = [p for p in _ink_neighbors(skel, *cur)
                    if p != prev and not visited[p]]
            if not nbrs:
                break
            nxt = nbrs[0]
            visited[nxt] = True
            pts.append(nxt)
            prev, cur = cur, nxt
        if len(pts) >= 4:
            paths.append({
                "points": [(float(p[0]), float(p[1])) for p in pts],
                "closed": True,
            })
    return paths
