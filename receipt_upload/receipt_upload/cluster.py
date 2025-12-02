# infra/lambda_layer/python/dynamo/data/_cluster.py
import math
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities import Line
from receipt_upload.geometry import box_points, min_area_rect


def calculate_angle_from_corners(line: Line) -> float:
    """
    Calculate angle in degrees from top-left and top-right corners.

    Returns angle in degrees, where:
    - 0° = perfectly horizontal (left to right)
    - Positive = rotated counter-clockwise
    - Negative = rotated clockwise
    """
    tl = line.top_left
    tr = line.top_right

    dx = tr["x"] - tl["x"]
    dy = tr["y"] - tl["y"]

    # Handle axis-aligned bounding boxes (dx might be very small)
    if abs(dx) < 1e-6:
        # Vertical line
        return 90.0 if dy > 0 else -90.0

    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    return angle_deg


def circular_mean(angles: List[float]) -> float:
    """Calculate mean of angles handling wraparound."""
    if not angles:
        return 0.0
    angles_rad = [math.radians(a) for a in angles]
    mean_sin = sum(math.sin(a) for a in angles_rad) / len(angles_rad)
    mean_cos = sum(math.cos(a) for a in angles_rad) / len(angles_rad)
    return math.degrees(math.atan2(mean_sin, mean_cos))


def angle_difference(a1: float, a2: float) -> float:
    """Calculate smallest angle difference between two angles."""
    diff = abs(a1 - a2)
    return min(diff, 360 - diff)


def should_apply_smart_merging(
    cluster_dict: Dict[int, List[Line]],
    total_lines: int,
) -> bool:
    """
    Fast heuristic to determine if smart merging is needed.

    This avoids expensive merging operations when clustering looks good.

    Criteria for skipping smart merging:
    1. Only 1-2 clusters (likely correct)
    2. Clusters are well-separated spatially (X-coordinates)
    3. Each cluster has reasonable line count (not too many small clusters)
    4. No suspicious patterns (e.g., many tiny clusters)

    Returns:
        True if smart merging should be applied, False to skip it
    """
    if not cluster_dict:
        return False

    num_clusters = len(cluster_dict)

    # If only 1-2 clusters, likely correct (skip expensive merging)
    if num_clusters <= 2:
        return False

    # If too many clusters relative to total lines, might need merging
    # e.g., 100 lines split into 20 clusters is suspicious
    avg_lines_per_cluster = total_lines / num_clusters if num_clusters > 0 else 0
    if avg_lines_per_cluster < 3:
        # Many tiny clusters - likely over-clustered
        return True

    # Check for spatial separation (X-coordinates)
    # If clusters are well-separated horizontally, they're likely different receipts
    cluster_x_centers = []
    for cluster_lines in cluster_dict.values():
        x_coords = [line.calculate_centroid()[0] for line in cluster_lines]
        if x_coords:
            cluster_x_centers.append(sum(x_coords) / len(x_coords))

    if len(cluster_x_centers) >= 2:
        cluster_x_centers.sort()
        # Check if clusters are clearly separated (gaps > 0.2 = 20% of image width)
        min_gap = min(
            cluster_x_centers[i + 1] - cluster_x_centers[i]
            for i in range(len(cluster_x_centers) - 1)
        )

        # If clusters are well-separated, they're likely different receipts
        # Only merge if clusters are close together (might be over-split)
        if min_gap > 0.2:
            return False

    # Check for angle consistency within clusters
    # If clusters have very different angles, they might be different receipts
    cluster_angles = []
    for cluster_lines in cluster_dict.values():
        if cluster_lines:
            angles = [calculate_angle_from_corners(line) for line in cluster_lines]
            mean_angle = circular_mean(angles)
            cluster_angles.append(mean_angle)

    if len(cluster_angles) >= 2:
        # Check if angles are very different (might be different receipts)
        angle_diffs = [
            angle_difference(cluster_angles[i], cluster_angles[i + 1])
            for i in range(len(cluster_angles) - 1)
        ]
        max_angle_diff = max(angle_diffs) if angle_diffs else 0

        # If angles are very different (>5°), likely different receipts (skip merging)
        if max_angle_diff > 5.0:
            return False

    # Default: apply smart merging if we have 3+ clusters
    # This is conservative - better to merge when unsure
    return num_clusters >= 3


def split_clusters_by_angle_consistency(
    cluster_dict: Dict[int, List[Line]],
    angle_tolerance: float = 3.0,
    min_samples: int = 2,
) -> Dict[int, List[Line]]:
    """
    Phase 1: Split clusters by angle consistency.

    This function takes clusters from X-axis DBSCAN and splits them if they
    contain lines with significantly different angles. This prevents merging
    side-by-side receipts that have different rotations.

    Args:
        cluster_dict: Dictionary mapping cluster_id -> List[Line]
        angle_tolerance: Maximum angle difference within a cluster (degrees)
        min_samples: Minimum lines per cluster after splitting

    Returns:
        New dictionary with potentially split clusters
    """
    if not cluster_dict:
        return {}

    new_clusters: Dict[int, List[Line]] = {}
    next_cluster_id = 1

    for cluster_id, cluster_lines in cluster_dict.items():
        if len(cluster_lines) < min_samples:
            # Keep small clusters as-is
            new_clusters[next_cluster_id] = cluster_lines
            next_cluster_id += 1
            continue

        # Calculate angles for all lines in this cluster
        line_data = []
        for line in cluster_lines:
            angle = calculate_angle_from_corners(line)
            x, y = line.calculate_centroid()
            line_data.append({
                'line': line,
                'angle': angle,
                'x': x,
            })

        # Sort by X coordinate (maintain reading order)
        line_data.sort(key=lambda ld: ld['x'])

        # Calculate mean angle for the cluster
        angles = [ld['angle'] for ld in line_data]
        mean_angle = circular_mean(angles)

        # Check if all angles are consistent
        max_angle_diff = max(angle_difference(a, mean_angle) for a in angles)

        if max_angle_diff <= angle_tolerance:
            # All angles consistent - keep as single cluster
            new_clusters[next_cluster_id] = cluster_lines
            next_cluster_id += 1
        else:
            # Angles inconsistent - split by angle
            # Group by angle similarity
            angle_groups = []
            current_angle_group = [line_data[0]]

            for i in range(1, len(line_data)):
                current_angle = line_data[i]['angle']

                # Check if angle is similar to current group's mean
                group_angles = [ld['angle'] for ld in current_angle_group]
                group_mean = circular_mean(group_angles)

                if angle_difference(current_angle, group_mean) <= angle_tolerance:
                    current_angle_group.append(line_data[i])
                else:
                    # Start new angle group
                    if len(current_angle_group) >= min_samples:
                        angle_groups.append(current_angle_group)
                    current_angle_group = [line_data[i]]

            # Add last group
            if len(current_angle_group) >= min_samples:
                angle_groups.append(current_angle_group)

            # Assign each angle group to a cluster
            for angle_group in angle_groups:
                new_clusters[next_cluster_id] = [ld['line'] for ld in angle_group]
                next_cluster_id += 1

    return new_clusters


def evaluate_receipt_completeness(lines: List[Line]) -> Dict[str, float]:
    """
    Evaluate if a group of lines forms a complete receipt.

    Uses the same logic as the combine agent's evaluation tools.
    Returns scores for spatial coherence, completeness, and overall quality.
    """
    if not lines:
        return {
            'spatial_score': 0.0,
            'completeness_score': 0.0,
            'line_count_score': 0.0,
            'overall_score': 0.0,
            'y_range': 0.0,
        }

    # Build text
    receipt_text = " ".join(line.text for line in lines)
    text_lower = receipt_text.lower()

    # Check for receipt elements (same logic as combine agent)
    has_merchant = any(
        word in text_lower for word in ["market", "store", "restaurant", "cafe", "shop", "inc", "llc", "corp"]
    ) or (lines and len([w for w in lines[0].text.split() if len(w) > 3]) > 0)

    has_address = any(
        word in text_lower for word in ["street", "st", "avenue", "ave", "road", "rd", "blvd", "boulevard", "drive", "dr", "way", "lane", "ln"]
    ) or any(char.isdigit() for char in receipt_text)

    has_phone = any(
        char in receipt_text for char in ["(", ")", "-"]
    ) or any(len(part) == 10 and part.isdigit() for part in receipt_text.replace("(", "").replace(")", "").replace("-", "").split())

    has_total = any(
        word in text_lower for word in ["total", "amount", "sum", "$", "subtotal"]
    ) or any(char == "$" for char in receipt_text)

    completeness_score = sum([has_merchant, has_address, has_phone, has_total]) / 4.0

    # Spatial analysis
    y_positions = [line.calculate_centroid()[1] for line in lines]
    y_range = max(y_positions) - min(y_positions) if y_positions else 0
    spatial_score = 1.0 / (1.0 + y_range * 0.1)  # Closer lines = higher score

    # Line count score
    line_count_score = min(len(lines) / 10.0, 1.0) if len(lines) >= 3 else len(lines) / 3.0

    # Overall score (same weights as evaluate_grouping)
    overall_score = (spatial_score * 0.3 + completeness_score * 0.5 + line_count_score * 0.2)

    return {
        'spatial_score': spatial_score,
        'completeness_score': completeness_score,
        'line_count_score': line_count_score,
        'overall_score': overall_score,
        'y_range': y_range,
    }


def merge_clusters_with_agent_logic(
    cluster_dict: Dict[int, List[Line]],
    min_score: float = 0.5,
    x_proximity_threshold: float = 0.4,
) -> Dict[int, List[Line]]:
    """
    Phase 2: Greedily merge clusters using combine agent evaluation logic.

    This function evaluates cluster pairs and merges them if they form
    coherent receipts. It prevents merging side-by-side receipts by
    checking X-coordinate proximity.

    NOTE: This does NOT use LLM calls - it uses pure Python evaluation
    logic based on the combine agent's scoring functions.

    Args:
        cluster_dict: Dictionary mapping cluster_id -> List[Line]
        min_score: Minimum coherence score to merge clusters
        x_proximity_threshold: Maximum X-distance to allow merge (normalized)

    Returns:
        New dictionary with merged clusters
    """
    if not cluster_dict:
        return {}

    # Start with original clusters
    merged_clusters = {cid: lines[:] for cid, lines in cluster_dict.items()}
    next_cluster_id = max(merged_clusters.keys()) + 1 if merged_clusters else 1

    iteration = 0
    max_iterations = len(cluster_dict)  # Prevent infinite loops

    while iteration < max_iterations:
        iteration += 1

        # Evaluate all pairs
        cluster_ids = sorted(merged_clusters.keys())
        best_merge = None
        best_score = min_score

        for i, cid1 in enumerate(cluster_ids):
            for cid2 in cluster_ids[i+1:]:
                cluster1_lines = merged_clusters[cid1]
                cluster2_lines = merged_clusters[cid2]

                # Check X-proximity first (quick check)
                x_coords_1 = [line.calculate_centroid()[0] for line in cluster1_lines]
                x_coords_2 = [line.calculate_centroid()[0] for line in cluster2_lines]
                mean_x_1 = sum(x_coords_1) / len(x_coords_1) if x_coords_1 else 0
                mean_x_2 = sum(x_coords_2) / len(x_coords_2) if x_coords_2 else 0
                x_proximity = abs(mean_x_1 - mean_x_2)

                # Skip if too far apart horizontally (likely different receipts)
                if x_proximity > x_proximity_threshold:
                    continue

                # Try merging
                merged_lines = cluster1_lines + cluster2_lines
                merged_eval = evaluate_receipt_completeness(merged_lines)

                # Check for large spatial gaps
                all_y_positions = [line.calculate_centroid()[1] for line in merged_lines]
                all_y_positions.sort()

                large_gaps = []
                for j in range(len(all_y_positions) - 1):
                    gap = all_y_positions[j + 1] - all_y_positions[j]
                    if gap > 0.1:  # Large gap might indicate separate receipts
                        large_gaps.append(gap)

                # Check for different merchants (simplified)
                text1 = " ".join(line.text for line in cluster1_lines).lower()
                text2 = " ".join(line.text for line in cluster2_lines).lower()

                merchant_indicators = ["market", "store", "restaurant", "cafe", "shop", "inc", "llc", "corp"]
                merchant_words_1 = [word for word in text1.split() if any(ind in word for ind in merchant_indicators)]
                merchant_words_2 = [word for word in text2.split() if any(ind in word for ind in merchant_indicators)]

                has_different_merchants = (
                    len(merchant_words_1) > 0 and len(merchant_words_2) > 0 and
                    len(set(merchant_words_1) & set(merchant_words_2)) == 0 and
                    len(merchant_words_1) >= 2 and len(merchant_words_2) >= 2
                )

                # Calculate coherence
                coherence = merged_eval['overall_score']

                # Add X-proximity bonus
                x_proximity_bonus = 0.0
                if x_proximity < 0.2:
                    x_proximity_bonus = 0.1
                coherence += x_proximity_bonus
                coherence = min(coherence, 1.0)

                # Penalize for issues
                if has_different_merchants:
                    coherence *= 0.5
                if large_gaps:
                    coherence *= 0.8
                if merged_eval['y_range'] > 0.8:
                    coherence *= 0.95

                # Check if merge makes sense
                makes_sense = (
                    coherence > min_score and
                    not has_different_merchants and
                    merged_eval['line_count_score'] > 0 and
                    (len(large_gaps) == 0 or x_proximity < 0.15)
                )

                if makes_sense and coherence > best_score:
                    best_score = coherence
                    best_merge = (cid1, cid2, merged_lines, coherence)

        if best_merge is None:
            # No more good merges
            break

        # Perform the merge
        cid1, cid2, merged_lines, _ = best_merge

        # Remove old clusters
        del merged_clusters[cid1]
        del merged_clusters[cid2]

        # Add merged cluster
        merged_clusters[next_cluster_id] = merged_lines
        next_cluster_id += 1

    return merged_clusters


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
