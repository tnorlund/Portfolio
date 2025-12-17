# infra/lambda_layer/python/dynamo/data/_cluster.py
import math
from typing import Dict, List, Optional, Tuple

from receipt_upload.geometry import box_points, min_area_rect

from receipt_dynamo.entities import Line


def dbscan_lines(
    lines: List[Line], eps: float = 10.0, min_samples: int = 2
) -> Dict[int, List[Line]]:
    """
    Clusters a list of Line objects using a custom DBSCAN implementation
    based on the centroids of each Line.

    Args:
        lines (List[Line]): List of Line objects to cluster.
        eps (float, optional): Maximum distance between two points to be
            considered as neighbors. Defaults to 10.0.
        min_samples (int, optional): Minimum number of points required to
            form a dense region. Defaults to 2.

    Returns:
        Dict[int, List[Line]]: A dictionary mapping cluster labels to lists
            of Line objects. A label of -1 indicates a noise point.
    """

    # Extract centroids for each line.
    points = [line.calculate_centroid() for line in lines]
    n = len(points)

    # Initialize bookkeeping lists:
    visited = [False] * n  # Tracks if a point has been visited.
    cluster_labels: List[Optional[int]] = [
        None
    ] * n  # None = not assigned; -1 = noise; other integers = cluster ID.
    current_cluster = 1

    def region_query(idx: int) -> List[int]:
        """Returns indices for points within eps distance of points[idx]."""
        neighbors = []
        x1, y1 = points[idx]
        for j in range(n):
            x2, y2 = points[j]
            if math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2) <= eps:
                neighbors.append(j)
        return neighbors

    def expand_cluster(point_idx: int, neighbors: List[int], cluster_id: int):
        """Expand cluster from a core point."""
        cluster_labels[point_idx] = cluster_id
        seeds = neighbors.copy()

        while seeds:
            current_point = seeds.pop(0)

            # Skip if already visited
            if visited[current_point]:
                # Assign to cluster if not already assigned
                if (
                    cluster_labels[current_point] is None
                    or cluster_labels[current_point] == -1
                ):
                    cluster_labels[current_point] = cluster_id
                continue

            # Mark as visited and check neighbors
            visited[current_point] = True
            new_neighbors = region_query(current_point)

            # If it's a core point, add unvisited neighbors to seeds
            if len(new_neighbors) >= min_samples:
                for neighbor in new_neighbors:
                    if neighbor not in seeds:
                        seeds.append(neighbor)

            # Assign to cluster
            if (
                cluster_labels[current_point] is None
                or cluster_labels[current_point] == -1
            ):
                cluster_labels[current_point] = cluster_id

    # DBSCAN algorithm
    for i in range(n):
        if visited[i]:
            continue

        visited[i] = True
        neighbors = region_query(i)

        if len(neighbors) < min_samples:
            # Label as noise if not enough neighbors.
            cluster_labels[i] = -1
        else:
            # Start a new cluster and expand it.
            expand_cluster(i, neighbors, current_cluster)
            current_cluster += 1

    # Group Line objects by cluster label.
    clusters: Dict[int, List[Line]] = {}
    for idx, label in enumerate(cluster_labels):
        if label is None:
            continue
        clusters.setdefault(label, []).append(lines[idx])

    return clusters


def dbscan_lines_x_axis(
    lines: List[Line], eps: float = 0.08, min_samples: int = 2
) -> Dict[int, List[Line]]:
    """
    Clusters a list of Line objects using a custom DBSCAN implementation
    based on the centroids of each Line.
    """
    if not lines:
        return {}

    # Compute an x–centroid for each line and sort.
    lines_with_x = [(line, line.calculate_centroid()[0]) for line in lines]
    lines_with_x.sort(key=lambda pair: pair[1])

    # Start with cluster_id 1 (not 0)
    current_cluster_id = 1
    clusters: List[List[Tuple[Line, float]]] = [[]]
    clusters[0].append(lines_with_x[0])

    for i in range(1, len(lines_with_x)):
        current_line, current_x = lines_with_x[i]
        _, prev_x = lines_with_x[i - 1]
        if abs(current_x - prev_x) <= eps:
            # Add to current cluster
            clusters[current_cluster_id - 1].append((current_line, current_x))
        else:
            # Start new cluster
            current_cluster_id += 1
            clusters.append([(current_line, current_x)])

    # Mark clusters that have too few lines as noise (cluster_id = -1)
    # Start valid clusters at ID 1
    cluster_id_counter = 1
    for cluster in clusters:
        if len(cluster) < min_samples:
            for line_obj, _ in cluster:
                line_obj.cluster_id = -1
        else:
            for line_obj, _ in cluster:
                line_obj.cluster_id = cluster_id_counter
            cluster_id_counter += 1

    # Build final dictionary, skipping noise points
    cluster_dict: Dict[int, List[Line]] = {}
    for line_obj, _ in lines_with_x:
        if line_obj.cluster_id == -1:
            continue
        cluster_dict.setdefault(line_obj.cluster_id, []).append(line_obj)

    return cluster_dict


def join_overlapping_clusters(
    cluster_dict: Dict[int, List[Line]],
    image_width: int,
    image_height: int,
    iou_threshold: float = 0.01,
) -> Dict[int, List[Line]]:
    """
    Merge clusters whose bounding boxes overlap above the given iou_threshold.
    This returns a new dictionary of cluster_id -> List[Line] with no overlaps.
    We ignore cluster_id == -1 (noise).
    """
    # Step 1: Collect valid cluster_ids (excluding -1)
    valid_cluster_ids = [cid for cid in cluster_dict.keys() if cid != -1]
    if not valid_cluster_ids:
        return {}

    # Step 2: Compute bounding boxes (as polygon points) for each cluster
    cluster_bboxes = {}
    for cid in valid_cluster_ids:
        lines_in_cluster = cluster_dict[cid]
        # Collect absolute coords for all corners in the cluster
        pts_abs = []
        for ln in lines_in_cluster:
            for corner in [
                ln.top_left,
                ln.top_right,
                ln.bottom_left,
                ln.bottom_right,
            ]:
                x_abs = corner["x"] * image_width
                y_abs = (1.0 - corner["y"]) * image_height
                pts_abs.append((x_abs, y_abs))

        if not pts_abs:
            continue

        (cx, cy), (rw, rh), angle_deg = min_area_rect(pts_abs)
        box_4 = box_points((cx, cy), (rw, rh), angle_deg)
        box_4_ordered = reorder_box_points(box_4)
        cluster_bboxes[cid] = {"box_points": box_4_ordered}

    # Step 3: Union-Find helper functions
    def find(x: int) -> int:
        """Find the root parent of cluster x"""
        if parent[x] != x:
            parent[x] = find(parent[x])  # Path compression
        return parent[x]

    def union(x: int, y: int):
        """Union clusters x and y by rank"""
        px, py = find(x), find(y)
        if px != py:
            if rank[px] < rank[py]:
                px, py = py, px
            parent[py] = px
            if rank[px] == rank[py]:
                rank[px] += 1

    # Initialize Union-Find data structures
    parent = {cid: cid for cid in valid_cluster_ids}
    rank = {cid: 0 for cid in valid_cluster_ids}

    def cross(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        """Return the 2D cross product of vectors a and b."""
        return a[0] * b[1] - a[1] * b[0]

    def subtract(
        a: Tuple[float, float], b: Tuple[float, float]
    ) -> Tuple[float, float]:
        """Subtract vector b from vector a."""
        return (a[0] - b[0], a[1] - b[1])

    def polygon_area(polygon: List[Tuple[float, float]]) -> float:
        """Compute the area of a polygon using the Shoelace formula."""
        area = 0.0
        n = len(polygon)
        for i in range(n):
            j = (i + 1) % n
            area += (
                polygon[i][0] * polygon[j][1] - polygon[j][0] * polygon[i][1]
            )
        return abs(area) / 2.0

    def compute_intersection(
        s: Tuple[float, float],
        e: Tuple[float, float],
        cp1: Tuple[float, float],
        cp2: Tuple[float, float],
    ) -> Optional[Tuple[float, float]]:
        """Compute intersection of line segment s->e with line cp1->cp2."""
        r = subtract(e, s)
        d = subtract(cp2, cp1)
        denominator = cross(r, d)
        if denominator == 0:
            return None
        t = cross(subtract(cp1, s), d) / denominator
        return (s[0] + t * r[0], s[1] + t * r[1])

    def is_inside(
        p: Tuple[float, float],
        cp1: Tuple[float, float],
        cp2: Tuple[float, float],
    ) -> bool:
        """Check if point p is inside half-plane defined by edge cp1->cp2."""
        return cross(subtract(cp2, cp1), subtract(p, cp1)) >= 0

    def polygon_clip(
        subject_polygon: List[Tuple[float, float]],
        clip_polygon: List[Tuple[float, float]],
    ) -> List[Tuple[float, float]]:
        """Clip a polygon with another using Sutherland-Hodgman algorithm."""
        output_list = subject_polygon[:]
        cp1 = clip_polygon[-1]
        for cp2 in clip_polygon:
            input_list = output_list
            output_list = []
            if not input_list:
                break
            s = input_list[-1]
            for e in input_list:
                if is_inside(e, cp1, cp2):
                    if not is_inside(s, cp1, cp2):
                        intersection_pt = compute_intersection(s, e, cp1, cp2)
                        if intersection_pt:
                            output_list.append(intersection_pt)
                    output_list.append(e)
                elif is_inside(s, cp1, cp2):
                    intersection_pt = compute_intersection(s, e, cp1, cp2)
                    if intersection_pt:
                        output_list.append(intersection_pt)
                s = e
            cp1 = cp2
        return output_list

    def compute_iou(box_a: Dict, box_b: Dict) -> float:
        """Compute Intersection over Union (IoU) for two bounding boxes."""
        poly_a = box_a["box_points"]
        poly_b = box_b["box_points"]

        area_a = polygon_area(poly_a)
        area_b = polygon_area(poly_b)

        intersection_poly = polygon_clip(poly_a, poly_b)
        if not intersection_poly:
            intersection_area = 0.0
        else:
            intersection_area = polygon_area(intersection_poly)

        union_area = area_a + area_b - intersection_area
        if union_area == 0:
            return 0.0
        return intersection_area / union_area

    def boxes_overlap(
        box_a: Dict, box_b: Dict, threshold: float = 0.1
    ) -> bool:
        """Check if boxes overlap significantly based on IoU threshold."""
        iou = compute_iou(box_a, box_b)
        return iou > threshold

    # Step 4: Compare every pair of clusters and merge if they overlap
    all_ids = list(cluster_bboxes.keys())
    for i, cid_a in enumerate(all_ids):
        for j in range(i + 1, len(all_ids)):
            cid_b = all_ids[j]
            if boxes_overlap(
                cluster_bboxes[cid_a], cluster_bboxes[cid_b], iou_threshold
            ):
                union(cid_a, cid_b)

    # Step 5: Create new merged clusters dictionary
    merged_clusters: Dict[int, List[Line]] = {}
    for cid in valid_cluster_ids:
        root = find(cid)
        if root not in merged_clusters:
            merged_clusters[root] = []
        merged_clusters[root].extend(cluster_dict[cid])

    return merged_clusters


def reorder_box_points(
    pts: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """
    Given four points in any order, return them in a consistent order:
    top-left, top-right, bottom-right, bottom-left.
    """
    pts_sorted = sorted(pts, key=lambda p: (p[1], p[0]))
    top1, top2 = pts_sorted[0], pts_sorted[1]
    bottom1, bottom2 = pts_sorted[2], pts_sorted[3]
    if top1[0] < top2[0]:
        tl, tr = top1, top2
    else:
        tl, tr = top2, top1
    if bottom1[0] < bottom2[0]:
        bl, br = bottom1, bottom2
    else:
        bl, br = bottom2, bottom1
    return [tl, tr, br, bl]
