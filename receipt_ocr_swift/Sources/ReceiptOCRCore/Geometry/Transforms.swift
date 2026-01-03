import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Geometry Errors

/// Errors that can occur during geometric transform calculations
public enum GeometryError: Error, LocalizedError {
    case singularMatrix(String)
    case insufficientPoints(String)
    case collinearPoints(String)
    case degenerateBounds(String)

    public var errorDescription: String? {
        switch self {
        case .singularMatrix(let msg): return "Singular matrix: \(msg)"
        case .insufficientPoints(let msg): return "Insufficient points: \(msg)"
        case .collinearPoints(let msg): return "Collinear points: \(msg)"
        case .degenerateBounds(let msg): return "Degenerate bounds: \(msg)"
        }
    }
}

// MARK: - Perspective Transform Coefficients

/// Compute 8 perspective transform coefficients.
///
/// These coefficients define a perspective (projective) transformation that maps
/// destination points to source points. The transformation formula is:
///
/// ```
/// x_src = (a*x_dst + b*y_dst + c) / (g*x_dst + h*y_dst + 1)
/// y_src = (d*x_dst + e*y_dst + f) / (g*x_dst + h*y_dst + 1)
/// ```
///
/// - Parameters:
///   - srcPoints: 4 source corner points in matching order with dstPoints
///   - dstPoints: 4 destination corner points
/// - Returns: Array of 8 coefficients [a, b, c, d, e, f, g, h]
/// - Throws: GeometryError if points are collinear, duplicated, or matrix is singular
public func findPerspectiveCoeffs(
    srcPoints: [(CGFloat, CGFloat)],
    dstPoints: [(CGFloat, CGFloat)]
) throws -> [CGFloat] {
    guard srcPoints.count == 4, dstPoints.count == 4 else {
        throw GeometryError.insufficientPoints("Exactly 4 source and 4 destination points required")
    }

    // Check for duplicate points
    for i in 0..<4 {
        for j in (i + 1)..<4 {
            let srcDist = sqrt(pow(srcPoints[i].0 - srcPoints[j].0, 2) + pow(srcPoints[i].1 - srcPoints[j].1, 2))
            let dstDist = sqrt(pow(dstPoints[i].0 - dstPoints[j].0, 2) + pow(dstPoints[i].1 - dstPoints[j].1, 2))
            if srcDist < 1e-9 || dstDist < 1e-9 {
                throw GeometryError.collinearPoints("Duplicate corner points detected")
            }
        }
    }

    // Check for collinear source points (using cross product)
    func crossProduct(_ p1: (CGFloat, CGFloat), _ p2: (CGFloat, CGFloat), _ p3: (CGFloat, CGFloat)) -> CGFloat {
        return (p2.0 - p1.0) * (p3.1 - p1.1) - (p2.1 - p1.1) * (p3.0 - p1.0)
    }

    let cross1 = crossProduct(srcPoints[0], srcPoints[1], srcPoints[2])
    let cross2 = crossProduct(srcPoints[0], srcPoints[1], srcPoints[3])
    let cross3 = crossProduct(srcPoints[0], srcPoints[2], srcPoints[3])
    if abs(cross1) < 1e-9 && abs(cross2) < 1e-9 && abs(cross3) < 1e-9 {
        throw GeometryError.collinearPoints("Source points are collinear")
    }

    // Build 8x8 linear system
    // Each point pair contributes 2 equations:
    // sx = a*dx + b*dy + c - g*dx*sx - h*dy*sx
    // sy = d*dx + e*dy + f - g*dx*sy - h*dy*sy
    var A: [[CGFloat]] = Array(repeating: Array(repeating: 0, count: 8), count: 8)
    var b: [CGFloat] = Array(repeating: 0, count: 8)

    for i in 0..<4 {
        let sx = srcPoints[i].0
        let sy = srcPoints[i].1
        let dx = dstPoints[i].0
        let dy = dstPoints[i].1

        // Equation for x: a*dx + b*dy + c + 0*d + 0*e + 0*f - g*dx*sx - h*dy*sx = sx
        A[i * 2][0] = dx
        A[i * 2][1] = dy
        A[i * 2][2] = 1
        A[i * 2][3] = 0
        A[i * 2][4] = 0
        A[i * 2][5] = 0
        A[i * 2][6] = -dx * sx
        A[i * 2][7] = -dy * sx
        b[i * 2] = sx

        // Equation for y: 0*a + 0*b + 0*c + d*dx + e*dy + f - g*dx*sy - h*dy*sy = sy
        A[i * 2 + 1][0] = 0
        A[i * 2 + 1][1] = 0
        A[i * 2 + 1][2] = 0
        A[i * 2 + 1][3] = dx
        A[i * 2 + 1][4] = dy
        A[i * 2 + 1][5] = 1
        A[i * 2 + 1][6] = -dx * sy
        A[i * 2 + 1][7] = -dy * sy
        b[i * 2 + 1] = sy
    }

    return try solve8x8System(A: A, b: b)
}

// MARK: - Solve 8x8 Linear System

/// Solve an 8x8 linear system using Gaussian elimination with partial pivoting.
///
/// - Parameters:
///   - A: 8x8 coefficient matrix
///   - b: 8-element right-hand side vector
/// - Returns: 8-element solution vector
/// - Throws: GeometryError.singularMatrix if the matrix is singular
internal func solve8x8System(A: [[CGFloat]], b: [CGFloat]) throws -> [CGFloat] {
    var matrix = A
    var rhs = b
    let n = 8

    // Forward elimination with partial pivoting
    for col in 0..<n {
        // Find pivot (row with largest absolute value in current column)
        var maxRow = col
        var maxVal = abs(matrix[col][col])
        for row in (col + 1)..<n {
            if abs(matrix[row][col]) > maxVal {
                maxVal = abs(matrix[row][col])
                maxRow = row
            }
        }

        // Swap rows if needed
        if maxRow != col {
            matrix.swapAt(col, maxRow)
            rhs.swapAt(col, maxRow)
        }

        // Check for singular matrix
        if abs(matrix[col][col]) < 1e-14 {
            throw GeometryError.singularMatrix("Matrix is singular at column \(col)")
        }

        // Eliminate column below pivot
        for row in (col + 1)..<n {
            let factor = matrix[row][col] / matrix[col][col]
            for k in col..<n {
                matrix[row][k] -= factor * matrix[col][k]
            }
            rhs[row] -= factor * rhs[col]
        }
    }

    // Back substitution
    var solution = Array(repeating: CGFloat(0), count: n)
    for i in stride(from: n - 1, through: 0, by: -1) {
        var sum = rhs[i]
        for j in (i + 1)..<n {
            sum -= matrix[i][j] * solution[j]
        }
        solution[i] = sum / matrix[i][i]
    }

    return solution
}

// MARK: - Invert Affine Transform

/// Invert a 2x3 affine transformation matrix.
///
/// The affine transform is represented as:
/// ```
/// [a  b  c]     [x']   [a*x + b*y + c]
/// [d  e  f]  *  [y'] = [d*x + e*y + f]
/// [0  0  1]     [1 ]   [      1      ]
/// ```
///
/// - Parameters:
///   - a, b, c, d, e, f: The 6 affine coefficients
/// - Returns: Tuple of 6 inverted coefficients (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
/// - Throws: GeometryError.singularMatrix if the transform is singular
public func invertAffine(
    a: CGFloat, b: CGFloat, c: CGFloat,
    d: CGFloat, e: CGFloat, f: CGFloat
) throws -> (CGFloat, CGFloat, CGFloat, CGFloat, CGFloat, CGFloat) {
    // Determinant of the 2x2 portion
    let det = a * e - b * d

    if abs(det) < 1e-14 {
        throw GeometryError.singularMatrix("Affine transform is singular (det ≈ 0)")
    }

    // Inverse of 2x2 matrix
    let a_inv = e / det
    let b_inv = -b / det
    let d_inv = -d / det
    let e_inv = a / det

    // Translation component of inverse
    let c_inv = -(a_inv * c + b_inv * f)
    let f_inv = -(d_inv * c + e_inv * f)

    return (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
}

// MARK: - Invert Perspective Transform

/// Invert an 8-coefficient perspective transformation.
///
/// The perspective transform can be represented as a 3x3 matrix:
/// ```
/// [a  b  c]
/// [d  e  f]
/// [g  h  1]
/// ```
///
/// - Parameters:
///   - coeffs: Array of 8 perspective coefficients [a, b, c, d, e, f, g, h]
/// - Returns: Array of 8 inverted coefficients
/// - Throws: GeometryError.singularMatrix if the transform is singular
public func invertPerspective(coeffs: [CGFloat]) throws -> [CGFloat] {
    guard coeffs.count == 8 else {
        throw GeometryError.insufficientPoints("Exactly 8 coefficients required")
    }

    let a = coeffs[0], b = coeffs[1], c = coeffs[2]
    let d = coeffs[3], e = coeffs[4], f = coeffs[5]
    let g = coeffs[6], h = coeffs[7]

    // Build 3x3 matrix
    // M = [[a, b, c], [d, e, f], [g, h, 1]]

    // Compute determinant using cofactor expansion along first row
    let det = a * (e * 1 - f * h) - b * (d * 1 - f * g) + c * (d * h - e * g)

    if abs(det) < 1e-14 {
        throw GeometryError.singularMatrix("Perspective transform is singular (det ≈ 0)")
    }

    // Compute adjugate matrix (transpose of cofactor matrix)
    let a2 = (e * 1 - f * h) / det
    let b2 = -(b * 1 - c * h) / det
    let c2 = (b * f - c * e) / det
    let d2 = -(d * 1 - f * g) / det
    let e2 = (a * 1 - c * g) / det
    let f2 = -(a * f - c * d) / det
    let g2 = (d * h - e * g) / det
    let h2 = -(a * h - b * g) / det
    // i2 = (a * e - b * d) / det  // This would be the [2][2] element, but we don't need it

    return [a2, b2, c2, d2, e2, f2, g2, h2]
}

#endif
