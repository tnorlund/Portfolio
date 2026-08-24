import Foundation
import simd

/// Bilinear sampling helpers for 8-bit and Int16 planes.
public enum Bilinear {
    @inline(__always)
    public static func sample(_ field: [UInt8], width: Int, height: Int, x: Float, y: Float) -> Float {
        let x0 = Int(x.rounded(.down)), y0 = Int(y.rounded(.down))
        let fx = x - Float(x0), fy = y - Float(y0)
        let x1 = min(width - 1, x0 + 1), y1 = min(height - 1, y0 + 1)
        let cx0 = max(0, min(width - 1, x0)), cy0 = max(0, min(height - 1, y0))
        let a = Float(field[cy0 * width + cx0]), b = Float(field[cy0 * width + x1])
        let c = Float(field[y1 * width + cx0]), d = Float(field[y1 * width + x1])
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
    }

    @inline(__always)
    public static func sample(_ field: [Int16], width: Int, height: Int, x: Float, y: Float) -> Float {
        let x0 = Int(x.rounded(.down)), y0 = Int(y.rounded(.down))
        let fx = x - Float(x0), fy = y - Float(y0)
        let x1 = min(width - 1, x0 + 1), y1 = min(height - 1, y0 + 1)
        let cx0 = max(0, min(width - 1, x0)), cy0 = max(0, min(height - 1, y0))
        let a = Float(field[cy0 * width + cx0]), b = Float(field[cy0 * width + x1])
        let c = Float(field[y1 * width + cx0]), d = Float(field[y1 * width + x1])
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
    }
}

public struct LKResult {
    public var position: SIMD2<Float>
    public var converged: Bool
    /// Mean absolute template residual at the final position (gray levels).
    public var ssd: Float
    /// Smaller eigenvalue of the structure tensor over the window (texture).
    public var minEigen: Float
    public var iterations: Int
}

/// Single-level forward-additive Lucas–Kanade refinement of a point whose
/// seed is already within a few pixels (dense flow, an object-transform
/// prediction, or zero motion). Templates are bilinear gray patches; image
/// gradients come from `Engine.sobel` (÷8 to be a unit-ramp derivative).
public enum LucasKanade {
    /// Patch of `(2r+1)²` bilinear gray samples centered at `p`, or nil when
    /// the window leaves the frame.
    public static func patch(_ gray: [UInt8], width: Int, height: Int, at p: SIMD2<Float>, radius: Int) -> [Float]? {
        let r = Float(radius)
        if p.x - r < 0 || p.y - r < 0 || p.x + r > Float(width - 1) || p.y + r > Float(height - 1) { return nil }
        let side = 2 * radius + 1
        var out = [Float](repeating: 0, count: side * side)
        var k = 0
        for dy in -radius...radius {
            for dx in -radius...radius {
                out[k] = Bilinear.sample(gray, width: width, height: height, x: p.x + Float(dx), y: p.y + Float(dy))
                k += 1
            }
        }
        return out
    }

    public static func refine(
        template: [Float], radius: Int, current: [UInt8], gradient: Engine.Gradient, width: Int, height: Int,
        start: SIMD2<Float>, iterations: Int, minEigen: Float
    ) -> LKResult {
        var p = start
        let side = 2 * radius + 1
        precondition(template.count == side * side, "template size mismatch")
        var converged = false
        var iterationsUsed = 0
        var lastEigen: Float = 0
        var lastSSD: Float = 255
        let r = Float(radius)
        for iteration in 0..<max(1, iterations) {
            iterationsUsed = iteration + 1
            if p.x - r < 1 || p.y - r < 1 || p.x + r > Float(width - 2) || p.y + r > Float(height - 2) {
                return LKResult(position: p, converged: false, ssd: 255, minEigen: 0, iterations: iterationsUsed)
            }
            var hxx: Float = 0, hxy: Float = 0, hyy: Float = 0
            var bx: Float = 0, by: Float = 0
            var ssd: Float = 0
            var k = 0
            for dy in -radius...radius {
                for dx in -radius...radius {
                    let x = p.x + Float(dx), y = p.y + Float(dy)
                    let i = Bilinear.sample(current, width: width, height: height, x: x, y: y)
                    let ix = Bilinear.sample(gradient.x, width: width, height: height, x: x, y: y) / 8
                    let iy = Bilinear.sample(gradient.y, width: width, height: height, x: x, y: y) / 8
                    let e = template[k] - i
                    hxx += ix * ix; hxy += ix * iy; hyy += iy * iy
                    bx += ix * e; by += iy * e
                    ssd += abs(e)
                    k += 1
                }
            }
            lastSSD = ssd / Float(side * side)
            let trace = hxx + hyy
            let det = hxx * hyy - hxy * hxy
            let disc = max(0, trace * trace / 4 - det)
            lastEigen = trace / 2 - disc.squareRoot()
            if lastEigen < minEigen || det <= 1e-6 {
                return LKResult(position: p, converged: false, ssd: lastSSD, minEigen: lastEigen, iterations: iterationsUsed)
            }
            let ddx = (hyy * bx - hxy * by) / det
            let ddy = (hxx * by - hxy * bx) / det
            p.x += ddx
            p.y += ddy
            if abs(ddx) < 0.02 && abs(ddy) < 0.02 {
                converged = true
                break
            }
            if abs(ddx) > 12 || abs(ddy) > 12 {
                return LKResult(position: p, converged: false, ssd: lastSSD, minEigen: lastEigen, iterations: iterationsUsed)
            }
        }
        // Final residual at the refined position.
        var ssd: Float = 0
        var k = 0
        for dy in -radius...radius {
            for dx in -radius...radius {
                ssd += abs(template[k] - Bilinear.sample(current, width: width, height: height, x: p.x + Float(dx), y: p.y + Float(dy)))
                k += 1
            }
        }
        return LKResult(position: p, converged: converged, ssd: ssd / Float(side * side), minEigen: lastEigen, iterations: iterationsUsed)
    }
}
