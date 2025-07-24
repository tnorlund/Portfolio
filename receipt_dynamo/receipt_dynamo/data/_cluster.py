# infra/lambda_layer/python/dynamo/data/_cluster.py
import math
from typing import Dict, List, Optional

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
        min_samples (int, optional): Minimum number of points required to form
            a dense region. Defaults to 2.

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
    current_cluster = 0

    def region_query(idx: int) -> List[int]:
        """Returns indices for points within eps distance of points[idx]."""
        neighbors = []
        x1, y1 = points[idx]
        for j in range(n):
            x2, y2 = points[j]
            if math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2) <= eps:
                neighbors.append(j)
        return neighbors

    # DBSCAN algorithm
    for i in range(n):
        if not visited[i]:
            visited[i] = True
            neighbors = region_query(i)
            if len(neighbors) < min_samples:
                # Label as noise if not enough neighbors.
                cluster_labels[i] = -1
            else:
                # Start a new cluster and expand it.
                cluster_labels[i] = current_cluster
                seeds = neighbors.copy()
                while seeds:
                    current_point = seeds.pop(0)
                    if not visited[current_point]:
                        visited[current_point] = True
                        new_neighbors = region_query(current_point)
                        if len(new_neighbors) >= min_samples:
                            # Add new neighbors to the seeds list if not
                            # already present.
                            for neighbor in new_neighbors:
                                if neighbor not in seeds:
                                    seeds.append(neighbor)
                    # Assign to the cluster if not already assigned.
                    if (
                        cluster_labels[current_point] is None
                        or cluster_labels[current_point] == -1
                    ):
                        cluster_labels[current_point] = current_cluster
                current_cluster += 1

    # Group Line objects by cluster label.
    clusters: Dict[int, List[Line]] = {}
    for idx, label in enumerate(cluster_labels):
        # Handle None labels (unassigned points) by treating them as noise (-1)
        if label is None:
            label = -1
        clusters.setdefault(label, []).append(lines[idx])

    return clusters
