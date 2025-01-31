from typing import List, Dict
import numpy as np
import logging

logger = logging.getLogger(__name__)

class Line:
    def __init__(self, text: str):
        self.text = text
        self.cluster_id = None

    def calculate_centroid(self):
        """
        Returns (x_centroid, y_centroid) for this line.
        For simplicity, let's just say it has some logic
        to compute and return them.
        """
        # Dummy implementation; replace with your own logic
        return (np.random.rand(), np.random.rand())

def cluster_image(
    lines: List[Line],
    eps: float = 0.08,
    min_samples: int = 2
) -> Dict[int, List[Line]]:
    """
    Cluster lines of text based on their x-centroids using a simple threshold-based approach.
    
    - Sort lines by x-centroid.
    - Group consecutive lines if their x-centroids are within `eps` of each other.
    - Mark clusters with fewer than `min_samples` lines as noise (cluster_id = -1).
    
    Returns:
        A dictionary mapping cluster_id -> list_of_lines.
    """
    if not lines:
        return {}

    # Sort by x-centroid
    lines_with_x = [(line, line.calculate_centroid()[0]) for line in lines]
    lines_with_x.sort(key=lambda pair: pair[1])  # sort by x value

    current_cluster_id = 0
    clusters = [[]]  # list of clusters; each cluster is a list of (Line, x)
    clusters[0].append(lines_with_x[0])  # start with the first line

    # Walk through lines, group if within eps
    for i in range(1, len(lines_with_x)):
        current_line, current_x = lines_with_x[i]
        prev_line, prev_x = lines_with_x[i - 1]

        if abs(current_x - prev_x) <= eps:
            # same cluster
            clusters[current_cluster_id].append((current_line, current_x))
        else:
            # start a new cluster
            current_cluster_id += 1
            clusters.append([(current_line, current_x)])

    # Mark cluster IDs; small clusters become noise (cluster_id = -1)
    cluster_id_counter = 1
    for cluster in clusters:
        if len(cluster) < min_samples:
            # mark noise
            for (line_obj, _) in cluster:
                line_obj.cluster_id = -1
        else:
            # assign a valid cluster ID
            for (line_obj, _) in cluster:
                line_obj.cluster_id = cluster_id_counter
            cluster_id_counter += 1

    # Build the final dictionary of cluster_id -> lines
    cluster_dict: Dict[int, List[Line]] = {}
    for line_obj, _ in lines_with_x:
        if line_obj.cluster_id == -1:
            continue  # skip noise
        cluster_dict.setdefault(line_obj.cluster_id, []).append(line_obj)

    logger.info("Found %d receipts (clusters).", len(cluster_dict))
    return cluster_dict