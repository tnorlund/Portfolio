import Foundation

#if os(macOS)
import CoreGraphics

/// Bounding box preprocessing for LayoutLM inference.
///
/// This normalizer matches the Python implementation in data_loader.py:
/// - `_box_from_word()`: Extracts axis-aligned bbox from 4 corners
/// - `_normalize_box_from_extents()`: Normalizes to [0, 1000] range
public struct BboxNormalizer {

    /// Extract axis-aligned bounding box from 4 corner points.
    ///
    /// This matches Python's `_box_from_word()` which computes:
    /// ```python
    /// xs = [word.top_left["x"], word.top_right["x"], ...]
    /// ys = [word.top_left["y"], word.top_right["y"], ...]
    /// return min(xs), min(ys), max(xs), max(ys)
    /// ```
    ///
    /// - Parameter word: Word with 4-corner bounding box
    /// - Returns: Tuple of (x0, y0, x1, y1) axis-aligned coordinates
    public static func boxFromWord(_ word: Word) -> (x0: CGFloat, y0: CGFloat, x1: CGFloat, y1: CGFloat) {
        let xs = [word.topLeft.x, word.topRight.x, word.bottomLeft.x, word.bottomRight.x]
        let ys = [word.topLeft.y, word.topRight.y, word.bottomLeft.y, word.bottomRight.y]
        return (xs.min()!, ys.min()!, xs.max()!, ys.max()!)
    }

    /// Normalize bounding box to [0, 1000] range.
    ///
    /// This matches Python's `_normalize_box_from_extents()`:
    /// - If max values > 1.0, scales by (v / max_val) * 1000
    /// - If max values <= 1.0 (already normalized), scales by v * 1000
    /// - Clamps result to [0, 1000]
    /// - Ensures proper ordering (x0 <= x1, y0 <= y1)
    ///
    /// - Parameters:
    ///   - x0: Left x coordinate
    ///   - y0: Top y coordinate
    ///   - x1: Right x coordinate
    ///   - y1: Bottom y coordinate
    ///   - maxX: Maximum x value across all words (for normalization)
    ///   - maxY: Maximum y value across all words (for normalization)
    /// - Returns: Array [x0, y0, x1, y1] as Int32 in range [0, 1000]
    public static func normalizeBox(
        x0: CGFloat,
        y0: CGFloat,
        x1: CGFloat,
        y1: CGFloat,
        maxX: CGFloat,
        maxY: CGFloat
    ) -> [Int32] {
        func scale(_ v: CGFloat, _ denom: CGFloat) -> Int32 {
            let val: Int32
            if denom > 1.0 {
                // Pixel coordinates - scale to 0-1000
                val = Int32(round((v / denom) * 1000))
            } else {
                // Already normalized (0-1) - just multiply by 1000
                val = Int32(round(v * 1000))
            }
            // Clamp to [0, 1000]
            return max(0, min(1000, val))
        }

        var nx0 = scale(x0, maxX)
        var ny0 = scale(y0, maxY)
        var nx1 = scale(x1, maxX)
        var ny1 = scale(y1, maxY)

        // Ensure proper ordering after rounding
        if nx0 > nx1 { swap(&nx0, &nx1) }
        if ny0 > ny1 { swap(&ny0, &ny1) }

        return [nx0, ny0, nx1, ny1]
    }

    /// Compute maximum extents (maxX, maxY) from a list of words.
    ///
    /// Used for per-receipt normalization as done in Python's inference code.
    ///
    /// - Parameter words: Array of words with bounding boxes
    /// - Returns: Tuple of (maxX, maxY)
    public static func computeExtents(words: [Word]) -> (maxX: CGFloat, maxY: CGFloat) {
        var maxX: CGFloat = 0
        var maxY: CGFloat = 0

        for word in words {
            let (_, _, x1, y1) = boxFromWord(word)
            maxX = max(maxX, x1)
            maxY = max(maxY, y1)
        }

        // Ensure we don't divide by zero
        if maxX == 0 { maxX = 1 }
        if maxY == 0 { maxY = 1 }

        return (maxX, maxY)
    }

    /// Compute maximum extents from a list of lines (containing words).
    ///
    /// - Parameter lines: Array of OCR lines
    /// - Returns: Tuple of (maxX, maxY)
    public static func computeExtents(lines: [Line]) -> (maxX: CGFloat, maxY: CGFloat) {
        var maxX: CGFloat = 0
        var maxY: CGFloat = 0

        for line in lines {
            for word in line.words {
                let (_, _, x1, y1) = boxFromWord(word)
                maxX = max(maxX, x1)
                maxY = max(maxY, y1)
            }
        }

        // Ensure we don't divide by zero
        if maxX == 0 { maxX = 1 }
        if maxY == 0 { maxY = 1 }

        return (maxX, maxY)
    }

    /// Normalize all words in a list, returning parallel arrays for LayoutLM input.
    ///
    /// - Parameters:
    ///   - words: Array of words to normalize
    ///   - maxX: Maximum x extent (compute with `computeExtents` first)
    ///   - maxY: Maximum y extent
    /// - Returns: Tuple of (texts, normalizedBboxes) parallel arrays
    public static func normalizeWords(
        _ words: [Word],
        maxX: CGFloat,
        maxY: CGFloat
    ) -> (texts: [String], bboxes: [[Int32]]) {
        var texts: [String] = []
        var bboxes: [[Int32]] = []

        for word in words {
            let (x0, y0, x1, y1) = boxFromWord(word)
            let normalizedBox = normalizeBox(x0: x0, y0: y0, x1: x1, y1: y1, maxX: maxX, maxY: maxY)
            texts.append(word.text)
            bboxes.append(normalizedBox)
        }

        return (texts, bboxes)
    }
}

#endif
