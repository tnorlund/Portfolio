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
///   - saturationMargin: How far above the image's own median line saturation a
///     line must sit to be dropped as non-document (floors the MAD-based, spread-
///     adaptive threshold). Keeps uniform warm/cream paper (~0.22) while still
///     removing a colored menu background (~0.35-0.47).
/// - Returns: The subset of `lines` on a document background.
public func filterDocumentLines(
    _ lines: [Line],
    image: CGImage,
    saturationMargin: CGFloat = 0.15
) -> [Line] {
    let w = image.width
    let h = image.height
    guard w > 0, h > 0, !lines.isEmpty else { return lines }

    // Downscale so the long side is <= maxSide. We sample sparsely, so a small
    // buffer is plenty and cuts memory ~10-15x (a full 4032x3024 RGBA buffer is
    // ~48MB).
    let maxSide = 1024
    let scale = min(1.0, CGFloat(maxSide) / CGFloat(max(w, h)))
    let bw = max(1, Int((CGFloat(w) * scale).rounded()))
    let bh = max(1, Int((CGFloat(h) * scale).rounded()))

    let bytesPerRow = bw * 4
    var data = [UInt8](repeating: 0, count: bw * bh * 4)
    guard
        let ctx = CGContext(
            data: &data,
            width: bw,
            height: bh,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
    else {
        return lines
    }
    ctx.interpolationQuality = .low
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: bw, height: bh))

    func backgroundSaturation(_ line: Line) -> CGFloat {
        let xsN = [
            line.topLeft.x, line.topRight.x,
            line.bottomLeft.x, line.bottomRight.x,
        ]
        // Vision is bottom-left origin; the bitmap is top-left origin.
        let ysN = [
            line.topLeft.y, line.topRight.y,
            line.bottomLeft.y, line.bottomRight.y,
        ]
        let xs = xsN.map { $0 * CGFloat(bw) }
        let ys = ysN.map { (1 - $0) * CGFloat(bh) }
        let x0 = max(0, Int(xs.min() ?? 0))
        let x1 = min(bw, Int(xs.max() ?? 0))
        let y0 = max(0, Int(ys.min() ?? 0))
        let y1 = min(bh, Int(ys.max() ?? 0))
        if x1 <= x0 || y1 <= y0 { return 0 }

        // Measure the PAPER, not the ink: sample only non-dark pixels
        // (lum >= 0.4) and take the MEDIAN. White paper and black ink are both
        // low-saturation; a colored menu background is high. Excluding ink also
        // protects receipt lines printed in colored ink (red VOID, colored
        // logos) — the surrounding white paper dominates the median, so the line
        // is kept.
        var sats: [CGFloat] = []
        let stepX = max(1, (x1 - x0) / 16)
        let stepY = max(1, (y1 - y0) / 16)
        var y = y0
        while y < y1 {
            var x = x0
            while x < x1 {
                let i = (y * bw + x) * 4
                let r = CGFloat(data[i]) / 255.0
                let g = CGFloat(data[i + 1]) / 255.0
                let b = CGFloat(data[i + 2]) / 255.0
                let lum = 0.299 * r + 0.587 * g + 0.114 * b
                if lum >= 0.4 {
                    let mx = max(r, max(g, b))
                    let mn = min(r, min(g, b))
                    sats.append(mx > 0 ? (mx - mn) / mx : 0)
                }
                x += stepX
            }
            y += stepY
        }
        // No paper sampled (essentially all ink) -> don't flag; keep the line.
        guard !sats.isEmpty else { return 0 }
        return median(sats)
    }

    // Adaptive threshold: compare each line's background saturation to the
    // image's OWN median line saturation, not a fixed constant. A receipt on
    // warm/cream paper under warm light has an elevated but UNIFORM background
    // (~0.22), so it must not be stripped; only lines that are clear saturation
    // outliers above the paper baseline (a colored menu ~0.35-0.47) are dropped.
    // threshold = median + max(margin, 4·MAD): the MAD term adapts to spread,
    // the margin floor keeps uniform warm paper safe.
    let sats = lines.map { backgroundSaturation($0) }
    let med = median(sats)
    let mad = median(sats.map { abs($0 - med) })
    let threshold = med + max(saturationMargin, 4.0 * 1.4826 * mad)
    return zip(lines, sats).compactMap { $0.1 < threshold ? $0.0 : nil }
}
#endif
