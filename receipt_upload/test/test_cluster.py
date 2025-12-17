"""Comprehensive unit tests for the cluster module."""

import math
from unittest.mock import Mock, patch

import pytest
from receipt_upload.cluster import (
    dbscan_lines,
    dbscan_lines_x_axis,
    join_overlapping_clusters,
    reorder_box_points,
)

from receipt_dynamo.entities import Line


class TestDBSCANLines:
    """Test cases for the dbscan_lines function."""

    @pytest.fixture
    def mock_lines(self):
        """Create mock Line objects for testing."""
        lines = []
        for i in range(5):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (i * 5, i * 5)
            lines.append(line)
        return lines

    @pytest.fixture
    def close_lines(self):
        """Create lines that should cluster together."""
        lines = []
        # Create 3 lines close together
        for i in range(3):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (10 + i, 20 + i)
            lines.append(line)
        # Create 2 lines far apart (noise)
        for i in range(2):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (100 + i * 50, 200 + i * 50)
            lines.append(line)
        return lines

    @pytest.mark.unit
    def test_dbscan_lines_basic_clustering(self, close_lines):
        """Test basic clustering functionality."""
        result = dbscan_lines(close_lines, eps=5.0, min_samples=2)

        # Should have clusters and potentially noise points
        assert len(result) == 2  # 1 cluster + noise
        assert -1 in result  # Noise cluster
        assert 1 in result  # First cluster

        # Check cluster sizes
        assert len(result[1]) == 3  # Main cluster has 3 lines
        assert len(result[-1]) == 2  # Noise has 2 lines

    @pytest.mark.unit
    def test_dbscan_lines_empty_input(self):
        """Test with empty line list."""
        result = dbscan_lines([], eps=10.0, min_samples=2)
        assert result == {}

    @pytest.mark.unit
    def test_dbscan_lines_single_point(self):
        """Test with single line (should be noise)."""
        line = Mock(spec=Line)
        line.calculate_centroid.return_value = (10, 20)
        result = dbscan_lines([line], eps=10.0, min_samples=2)

        assert len(result) == 1
        assert -1 in result
        assert len(result[-1]) == 1

    @pytest.mark.unit
    def test_dbscan_lines_all_noise(self, mock_lines):
        """Test when all points are too far apart."""
        result = dbscan_lines(mock_lines, eps=1.0, min_samples=2)

        # All points should be noise
        assert len(result) == 1
        assert -1 in result
        assert len(result[-1]) == 5

    @pytest.mark.unit
    def test_dbscan_lines_different_eps(self, close_lines):
        """Test clustering with different epsilon values."""
        # Small eps - more noise
        result_small = dbscan_lines(close_lines, eps=1.0, min_samples=2)
        # Large eps - might cluster more
        result_large = dbscan_lines(close_lines, eps=50.0, min_samples=2)

        # With small eps, we should have more noise
        assert len(result_small.get(-1, [])) > len(result_large.get(-1, []))

    @pytest.mark.unit
    def test_dbscan_lines_min_samples_effect(self, close_lines):
        """Test effect of min_samples parameter."""
        # With min_samples=3, the 3 close lines should still form a cluster
        result = dbscan_lines(close_lines, eps=5.0, min_samples=3)
        assert 1 in result
        assert len(result[1]) == 3

        # With min_samples=4, they shouldn't form a cluster
        result = dbscan_lines(close_lines, eps=5.0, min_samples=4)
        assert 1 not in result or len(result.get(1, [])) == 0


class TestDBSCANLinesXAxis:
    """Test cases for the dbscan_lines_x_axis function."""

    @pytest.fixture
    def x_aligned_lines(self):
        """Create lines aligned along x-axis."""
        lines = []
        # Group 1: x around 10
        for i in range(3):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (10 + i * 0.01, 20 + i * 10)
            line.cluster_id = None
            lines.append(line)
        # Group 2: x around 20
        for i in range(3):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (20 + i * 0.01, 30 + i * 10)
            line.cluster_id = None
            lines.append(line)
        return lines

    @pytest.mark.unit
    def test_dbscan_lines_x_axis_basic(self, x_aligned_lines):
        """Test basic x-axis clustering."""
        result = dbscan_lines_x_axis(x_aligned_lines, eps=0.05, min_samples=2)

        # Should have 2 clusters
        assert len(result) == 2
        assert 1 in result
        assert 2 in result
        assert len(result[1]) == 3
        assert len(result[2]) == 3

    @pytest.mark.unit
    def test_dbscan_lines_x_axis_empty(self):
        """Test with empty input."""
        result = dbscan_lines_x_axis([], eps=0.08, min_samples=2)
        assert result == {}

    @pytest.mark.unit
    def test_dbscan_lines_x_axis_noise_handling(self):
        """Test noise point handling."""
        lines = []
        # Single isolated point
        line1 = Mock(spec=Line)
        line1.calculate_centroid.return_value = (10, 20)
        line1.cluster_id = None
        lines.append(line1)

        # Two points close together
        for i in range(2):
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (50 + i * 0.01, 30)
            line.cluster_id = None
            lines.append(line)

        result = dbscan_lines_x_axis(lines, eps=0.05, min_samples=2)

        # Should have one cluster, noise not included
        assert len(result) == 1
        assert line1.cluster_id == -1  # Marked as noise
        assert 1 in result
        assert len(result[1]) == 2

    @pytest.mark.unit
    def test_dbscan_lines_x_axis_sorting(self):
        """Test that lines are properly sorted by x coordinate."""
        lines = []
        x_coords = [50, 10, 30, 20, 40]  # Unsorted
        for x in x_coords:
            line = Mock(spec=Line)
            line.calculate_centroid.return_value = (x, 20)
            line.cluster_id = None
            lines.append(line)

        result = dbscan_lines_x_axis(lines, eps=15, min_samples=2)

        # All should be in one cluster since eps is large
        assert len(result) == 1
        assert 1 in result
        assert len(result[1]) == 5


class TestJoinOverlappingClusters:
    """Test cases for join_overlapping_clusters function."""

    @pytest.fixture
    def mock_cluster_dict(self):
        """Create mock cluster dictionary."""
        clusters = {}

        # Cluster 1: Simple rectangle
        lines1 = []
        for i in range(2):
            line = Mock(spec=Line)
            line.top_left = {"x": 0.1, "y": 0.9}
            line.top_right = {"x": 0.2, "y": 0.9}
            line.bottom_left = {"x": 0.1, "y": 0.8}
            line.bottom_right = {"x": 0.2, "y": 0.8}
            lines1.append(line)
        clusters[1] = lines1

        # Cluster 2: Overlapping rectangle
        lines2 = []
        for i in range(2):
            line = Mock(spec=Line)
            line.top_left = {"x": 0.15, "y": 0.85}
            line.top_right = {"x": 0.25, "y": 0.85}
            line.bottom_left = {"x": 0.15, "y": 0.75}
            line.bottom_right = {"x": 0.25, "y": 0.75}
            lines2.append(line)
        clusters[2] = lines2

        # Cluster 3: Non-overlapping rectangle
        lines3 = []
        for i in range(2):
            line = Mock(spec=Line)
            line.top_left = {"x": 0.5, "y": 0.5}
            line.top_right = {"x": 0.6, "y": 0.5}
            line.bottom_left = {"x": 0.5, "y": 0.4}
            line.bottom_right = {"x": 0.6, "y": 0.4}
            lines3.append(line)
        clusters[3] = lines3

        return clusters

    @pytest.mark.unit
    def test_join_overlapping_clusters_basic(self, mock_cluster_dict):
        """Test basic cluster merging."""
        with patch("receipt_upload.cluster.min_area_rect") as mock_min_area:
            with patch("receipt_upload.cluster.box_points") as mock_box_points:
                # Mock the bounding box calculations
                mock_min_area.side_effect = [
                    ((15, 85), (10, 10), 0),  # Cluster 1
                    ((20, 80), (10, 10), 0),  # Cluster 2
                    ((55, 45), (10, 10), 0),  # Cluster 3
                ]
                mock_box_points.side_effect = [
                    [(10, 80), (20, 80), (20, 90), (10, 90)],  # Cluster 1
                    [(15, 75), (25, 75), (25, 85), (15, 85)],  # Cluster 2
                    [(50, 40), (60, 40), (60, 50), (50, 50)],  # Cluster 3
                ]

                result = join_overlapping_clusters(
                    mock_cluster_dict,
                    image_width=1000,
                    image_height=1000,
                    iou_threshold=0.01,
                )

                # Clusters 1 and 2 should merge, cluster 3 should remain separate
                assert len(result) == 2

    @pytest.mark.unit
    def test_join_overlapping_clusters_empty(self):
        """Test with empty cluster dictionary."""
        result = join_overlapping_clusters({}, 1000, 1000, 0.01)
        assert result == {}

    @pytest.mark.unit
    def test_join_overlapping_clusters_noise_excluded(self, mock_cluster_dict):
        """Test that noise cluster (-1) is excluded."""
        # Add noise cluster
        mock_cluster_dict[-1] = [Mock(spec=Line)]

        with patch("receipt_upload.cluster.min_area_rect") as mock_min_area:
            with patch("receipt_upload.cluster.box_points") as mock_box_points:
                mock_min_area.side_effect = [
                    ((15, 85), (10, 10), 0),
                    ((20, 80), (10, 10), 0),
                    ((55, 45), (10, 10), 0),
                ]
                mock_box_points.side_effect = [
                    [(10, 80), (20, 80), (20, 90), (10, 90)],
                    [(15, 75), (25, 75), (25, 85), (15, 85)],
                    [(50, 40), (60, 40), (60, 50), (50, 50)],
                ]

                result = join_overlapping_clusters(
                    mock_cluster_dict,
                    image_width=1000,
                    image_height=1000,
                    iou_threshold=0.01,
                )

                # Noise cluster should not appear in result
                assert -1 not in result

    @pytest.mark.unit
    def test_join_overlapping_clusters_no_overlap(self):
        """Test when no clusters overlap."""
        clusters = {}

        # Create well-separated clusters
        for i in range(3):
            lines = []
            line = Mock(spec=Line)
            x_base = i * 0.3
            line.top_left = {"x": x_base, "y": 0.9}
            line.top_right = {"x": x_base + 0.1, "y": 0.9}
            line.bottom_left = {"x": x_base, "y": 0.8}
            line.bottom_right = {"x": x_base + 0.1, "y": 0.8}
            lines.append(line)
            clusters[i + 1] = lines

        with patch("receipt_upload.cluster.min_area_rect") as mock_min_area:
            with patch("receipt_upload.cluster.box_points") as mock_box_points:
                mock_min_area.side_effect = [
                    ((50, 850), (100, 100), 0),
                    ((350, 850), (100, 100), 0),
                    ((650, 850), (100, 100), 0),
                ]
                mock_box_points.side_effect = [
                    [(0, 800), (100, 800), (100, 900), (0, 900)],
                    [(300, 800), (400, 800), (400, 900), (300, 900)],
                    [(600, 800), (700, 800), (700, 900), (600, 900)],
                ]

                result = join_overlapping_clusters(
                    clusters,
                    image_width=1000,
                    image_height=1000,
                    iou_threshold=0.01,
                )

                # All clusters should remain separate
                assert len(result) == 3


class TestReorderBoxPoints:
    """Test cases for reorder_box_points function."""

    @pytest.mark.unit
    def test_reorder_box_points_already_ordered(self):
        """Test with already correctly ordered points."""
        points = [(0, 0), (10, 0), (10, 10), (0, 10)]
        result = reorder_box_points(points)
        assert result == [(0, 0), (10, 0), (10, 10), (0, 10)]

    @pytest.mark.unit
    def test_reorder_box_points_random_order(self):
        """Test with randomly ordered points."""
        points = [(10, 10), (0, 0), (10, 0), (0, 10)]
        result = reorder_box_points(points)
        assert result == [(0, 0), (10, 0), (10, 10), (0, 10)]

    @pytest.mark.unit
    def test_reorder_box_points_rotated_box(self):
        """Test with rotated rectangle points."""
        # Rotated rectangle
        points = [(5, 0), (10, 5), (5, 10), (0, 5)]
        result = reorder_box_points(points)

        # Check that top-left is correct (leftmost of the two top points)
        assert result[0] == (0, 5)
        assert result[1] == (5, 0)
        assert result[2] == (10, 5)
        assert result[3] == (5, 10)

    @pytest.mark.unit
    def test_reorder_box_points_negative_coordinates(self):
        """Test with negative coordinates."""
        points = [(-10, -10), (10, -10), (10, 10), (-10, 10)]
        result = reorder_box_points(points)
        assert result == [(-10, -10), (10, -10), (10, 10), (-10, 10)]

    @pytest.mark.unit
    def test_reorder_box_points_edge_cases(self):
        """Test edge cases like very small differences."""
        # Points with very small y differences
        points = [(0, 0.0001), (10, 0), (10, 10), (0, 10.0001)]
        result = reorder_box_points(points)

        # Should still order correctly
        assert result[0][0] < result[1][0]  # Top points ordered by x
        assert result[3][0] < result[2][0]  # Bottom points ordered by x
