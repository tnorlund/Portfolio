import Foundation

#if os(macOS)
import CoreGraphics

/// Filters OCR lines to those sitting on a document (white-paper) background,
/// dropping lines that sit on a colored / photo background — e.g. a menu or
/// flyer behind the receipt.
///
/// Rationale: white thermal paper and black ink are BOTH near-zero saturation,
/// so a receipt line's bounding box is low-saturation regardless of its text.
/// A line printed on a colored menu/photo background is high-saturation. This
/// is a cheap, mostly-deterministic separator that removes background bleed
/// (the Twin Peaks receipt absorbing the burger-menu behind it) before
/// clustering/segmentation.
///
/// Note: this does NOT separate two overlapping *white* receipts (both are
/// low-saturation) — that remains a geometry problem handled elsewhere.
///
/// - Parameters:
///   - lines: OCR lines with normalized, bottom-left-origin coordinates.
///   - image: Source pixels (full image).
///   - saturationThreshold: Mean background saturation above which a line is
///     treated as non-document. Calibrated: receipt lines ~0.09 median, menu
///     lines ~0.35-0.47; 0.18 leaves a wide margin.
/// - Returns: The subset of `lines` on a document background.
public func filterDocumentLines(
    _ lines: [Line],
    image: CGImage,
    saturationThreshold: CGFloat = 0.18
) -> [Line] {
    let w = image.width
    let h = image.height
    guard w > 0, h > 0, !lines.isEmpty else { return lines }

    let bytesPerRow = w * 4
    var data = [UInt8](repeating: 0, count: w * h * 4)
    guard
        let ctx = CGContext(
            data: &data,
            width: w,
            height: h,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
    else {
        return lines
    }
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))

    func meanBackgroundSaturation(_ line: Line) -> CGFloat {
        let xsN = [
            line.topLeft.x, line.topRight.x,
            line.bottomLeft.x, line.bottomRight.x,
        ]
        // Vision is bottom-left origin; the bitmap is top-left origin.
        let ysN = [
            line.topLeft.y, line.topRight.y,
            line.bottomLeft.y, line.bottomRight.y,
        ]
        let xs = xsN.map { $0 * CGFloat(w) }
        let ys = ysN.map { (1 - $0) * CGFloat(h) }
        let x0 = max(0, Int(xs.min() ?? 0))
        let x1 = min(w, Int(xs.max() ?? 0))
        let y0 = max(0, Int(ys.min() ?? 0))
        let y1 = min(h, Int(ys.max() ?? 0))
        if x1 <= x0 || y1 <= y0 { return 0 }

        // Subsample to at most ~16x16 samples per line for speed.
        let stepX = max(1, (x1 - x0) / 16)
        let stepY = max(1, (y1 - y0) / 16)
        var sum: CGFloat = 0
        var count = 0
        var y = y0
        while y < y1 {
            var x = x0
            while x < x1 {
                let i = (y * w + x) * 4
                let r = CGFloat(data[i]) / 255.0
                let g = CGFloat(data[i + 1]) / 255.0
                let b = CGFloat(data[i + 2]) / 255.0
                let mx = max(r, max(g, b))
                let mn = min(r, min(g, b))
                sum += mx > 0 ? (mx - mn) / mx : 0
                count += 1
                x += stepX
            }
            y += stepY
        }
        return count > 0 ? sum / CGFloat(count) : 0
    }

    return lines.filter { meanBackgroundSaturation($0) < saturationThreshold }
}
#endif
