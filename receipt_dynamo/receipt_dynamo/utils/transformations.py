"""
Affine and perspective transformation utilities.

This module provides functions for computing and inverting
2D transformations used in coordinate space conversions.

Copied from receipt_upload/receipt_upload/geometry/transformations.py
to avoid dependency on receipt_upload package.
"""

from typing import List, Tuple


def invert_affine(
    a: float, b: float, c: float, d: float, e: float, f: float
) -> Tuple[float, float, float, float, float, float]:
    """
    Inverts the 2x3 affine transform:

        [a  b  c]
        [d  e  f]
        [0  0  1]

    Returns the 6-tuple (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
    for the inverse transform, provided the determinant is not zero.
    """
    det = a * e - b * d
    if abs(det) < 1e-14:
        raise ValueError("Singular transform cannot be inverted.")
    a_inv = e / det
    b_inv = -b / det
    c_inv = (b * f - c * e) / det
    d_inv = -d / det
    e_inv = a / det
    f_inv = (c * d - a * f) / det
    return (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)


def invert_warp(
    a: float,
    b: float,
    c: float,
    d: float,
    e: float,
    f: float,
    g: float,
    h: float,
) -> List[float]:
    """
    Given the 8 perspective coefficients (a, b, c, d, e, f, g, h) for the mapping
      x_new = (a*x + b*y + c) / (1 + g*x + h*y)
      y_new = (d*x + e*y + f) / (1 + g*x + h*y)
    returns a new list of 8 coefficients [a2, b2, c2, d2, e2, f2, g2, h2]
    that perform the inverse mapping (x_new, y_new) -> (x, y).
    """
    # Form the 3x3 matrix
    # pylint: disable=invalid-name
    M = [
        [a, b, c],
        [d, e, f],
        [g, h, 1],
    ]
    # Invert it
    M_inv = _invert_3x3(M)
    # Extract the top-left 8 elements
    # M_inv = [[A, B, C],
    #          [D, E, F],
    #          [G, H, I]]
    A = M_inv[0][0]
    B = M_inv[0][1]
    C = M_inv[0][2]
    D = M_inv[1][0]
    E = M_inv[1][1]
    F = M_inv[1][2]
    G = M_inv[2][0]
    H = M_inv[2][1]
    # pylint: enable=invalid-name
    # The last element M_inv[2][2] would be 1 if not degenerate
    return [A, B, C, D, E, F, G, H]


def _invert_3x3(
    M: List[List[float]],
) -> List[List[float]]:  # pylint: disable=invalid-name
    """Inverts a 3x3 matrix M using standard formula (or your own method)."""
    determinant = (
        M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
        - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
        + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])
    )
    if abs(determinant) < 1e-12:
        raise ValueError("Cannot invert perspective matrix (det=0).")

    inverse_determinant = 1.0 / determinant
    # Adjugate / cofactor method
    return [
        [
            inverse_determinant * ((M[1][1] * M[2][2] - M[1][2] * M[2][1])),
            inverse_determinant * (-(M[0][1] * M[2][2] - M[0][2] * M[2][1])),
            inverse_determinant * ((M[0][1] * M[1][2] - M[0][2] * M[1][1])),
        ],
        [
            inverse_determinant * (-(M[1][0] * M[2][2] - M[1][2] * M[2][0])),
            inverse_determinant * ((M[0][0] * M[2][2] - M[0][2] * M[2][0])),
            inverse_determinant * (-(M[0][0] * M[1][2] - M[0][2] * M[1][0])),
        ],
        [
            inverse_determinant * ((M[1][0] * M[2][1] - M[1][1] * M[2][0])),
            inverse_determinant * (-(M[0][0] * M[2][1] - M[0][1] * M[2][0])),
            inverse_determinant * ((M[0][0] * M[1][1] - M[0][1] * M[1][0])),
        ],
    ]


def solve_8x8_system(
    A: List[List[float]], b: List[float]
) -> List[float]:  # pylint: disable=invalid-name
    """
    Solve an 8x8 system A * x = b for x, where:
      - A is a list of lists (8 rows, each row has 8 floats).
      - b is a list of length 8.
    Returns x as a list of length 8.
    Uses Gaussian elimination with partial pivoting.
    """
    n = 8

    # Forward elimination
    for i in range(n):
        # 1) Find pivot row (partial pivot)
        pivot = i
        for r in range(i + 1, n):
            if abs(A[r][i]) > abs(A[pivot][i]):
                pivot = r
        # 2) Swap pivot row into position
        if pivot != i:
            A[i], A[pivot] = A[pivot], A[i]
            b[i], b[pivot] = b[pivot], b[i]

        # 3) Normalize pivot row (so A[i][i] = 1)
        pivot_val = A[i][i]
        if abs(pivot_val) < 1e-12:
            raise ValueError(
                "Matrix is singular or poorly conditioned for pivoting."
            )
        inv_pivot = 1.0 / pivot_val
        A[i] = [val * inv_pivot for val in A[i]]
        b[i] = b[i] * inv_pivot

        # 4) Eliminate below pivot
        for r in range(i + 1, n):
            factor = A[r][i]
            A[r] = [A[r][c] - factor * A[i][c] for c in range(n)]
            b[r] = b[r] - factor * b[i]

    # Back-substitution
    for i in reversed(range(n)):
        # b[i] is the value after subtracting known terms from the row
        for j in range(i + 1, n):
            b[i] -= A[i][j] * b[j]
        # A[i][i] should be 1.0 here from the normalization step
    return b


def find_perspective_coeffs(
    src_points: List[Tuple[float, float]],
    dst_points: List[Tuple[float, float]],
) -> List[float]:
    """
    src_points: list of 4 (x, y) source corners
    dst_points: list of 4 (x, y) destination corners

    Returns a list of 8 coefficients [a, b, c, d, e, f, g, h] for PIL's
    Image.transform(..., PERSPECTIVE, coeffs).

    The transform maps (x_dst, y_dst) back to (x_src, y_src) as:
        x_src = a*x_dst + b*y_dst + c
        y_src = d*x_dst + e*y_dst + f
    normalized by (1 + g*x_dst + h*y_dst).
    """
    # Validate inputs
    if len(src_points) != 4 or len(dst_points) != 4:
        raise ValueError(
            "find_perspective_coeffs requires exactly 4 source and 4 "
            "destination points"
        )

    # Check for degenerate cases: collinear points
    def are_collinear(
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        p3: Tuple[float, float],
    ) -> bool:
        """Check if three points are collinear using cross product"""
        return (
            abs(
                (p2[0] - p1[0]) * (p3[1] - p1[1])
                - (p3[0] - p1[0]) * (p2[1] - p1[1])
            )
            < 1e-10
        )

    # Check if any 3 points in src_points are collinear
    for i in range(4):
        for j in range(i + 1, 4):
            for k in range(j + 1, 4):
                if are_collinear(src_points[i], src_points[j], src_points[k]):
                    raise ValueError(
                        f"Source points {i}, {j}, {k} are collinear - "
                        "cannot compute perspective transform"
                    )

    # Check if any 3 points in dst_points are collinear
    for i in range(4):
        for j in range(i + 1, 4):
            for k in range(j + 1, 4):
                if are_collinear(dst_points[i], dst_points[j], dst_points[k]):
                    raise ValueError(
                        f"Destination points {i}, {j}, {k} are collinear - "
                        "cannot compute perspective transform"
                    )

    # Check for duplicate points
    for i in range(4):
        for j in range(i + 1, 4):
            if (
                abs(src_points[i][0] - src_points[j][0]) < 1e-10
                and abs(src_points[i][1] - src_points[j][1]) < 1e-10
            ):
                raise ValueError(f"Source points {i} and {j} are identical")
            if (
                abs(dst_points[i][0] - dst_points[j][0]) < 1e-10
                and abs(dst_points[i][1] - dst_points[j][1]) < 1e-10
            ):
                raise ValueError(
                    f"Destination points {i} and {j} are identical"
                )

    # Each source→destination pair gives 2 linear equations:
    #   sx = a*dx + b*dy + c - g*dx*sx - h*dy*sx  (written in standard form)
    #   sy = d*dx + e*dy + f - g*dx*sy - h*dy*sy
    #
    # The matrix A is 8x8, vector b is length 8.
    # pylint: disable=invalid-name
    A = []
    B = []
    for (sx, sy), (dx, dy) in zip(src_points, dst_points):
        A.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        B.append(sx)
        A.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        B.append(sy)

    # Solve the system for [a, b, c, d, e, f, g, h]
    # Make a *copy* of A if you don't want to mutate the original:
    A_copy = [row[:] for row in A]
    B_copy = B[:]
    # pylint: enable=invalid-name

    try:
        solution = solve_8x8_system(A_copy, B_copy)
        return solution
    except ValueError as e:
        raise ValueError(
            f"Failed to compute perspective transform: {e}. "
            f"Source points may be too close to collinear."
        ) from e
