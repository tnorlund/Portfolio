import Foundation

/// EVALUATION ONLY. A ground-truth proxy for "is each held object in the
/// mask this frame?" on the benchmark clip. Unlike the mask pipeline, this
/// is allowed to know what the props look like: the band is orange, the
/// plates are dark discs beyond the hands along the bar line. Nothing here
/// feeds the mask; it only grades it, so the metrics can name the frames
/// where a prop is missing instead of a human scrubbing the video.
public enum Presence {
    public struct Result {
        public var bandTruth = 0
        public var bandRecall: Double? = nil
        public var plateTruthLeft = 0
        public var plateTruthRight = 0
        public var plateRecallLeft: Double? = nil
        public var plateRecallRight: Double? = nil
    }

    /// Orange pixels anywhere in the frame: the band's own colour. Deliberately
    /// NOT restricted to outside the person mask — Vision's person segmentation
    /// often swallows the band, and the question is whether the band is in the
    /// final mask, not who put it there. Thresholds sit clear of skin
    /// (skin ≈ 200/150/120: r−g 50, r−b 80) and of the red cap in the
    /// background (g ≈ b).
    public static func bandTruth(rgba: [UInt8], person: [UInt8], width: Int, height: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: width * height)
        for i in 0..<out.count {
            let r = Int(rgba[i * 4]), g = Int(rgba[i * 4 + 1]), b = Int(rgba[i * 4 + 2])
            if r >= 150 && r - g >= 80 && r - b >= 120 && g - b >= 15 { out[i] = 1 }
        }
        return out
    }

    /// Dark pixels that differ from the background plate, outside the person,
    /// within `radius` px of the bar line and beyond its ends (t < 0 left,
    /// t > 1 right). Returns nil when there is no pose bar line.
    public static func plateTruth(
        rgba: [UInt8], difference: [UInt8], person: [UInt8], bar: BodyPose.Segment?, width: Int, height: Int,
        threshold: Int, radius: Double = 100, maxBrightness: Int = 70
    ) -> (left: [UInt8], right: [UInt8])? {
        guard let bar, bar.length > 40 else { return nil }
        var left = [UInt8](repeating: 0, count: width * height)
        var right = left
        for y in 0..<height {
            for x in 0..<width {
                let i = y * width + x
                if person[i] != 0 || Int(difference[i]) < threshold { continue }
                let r = Int(rgba[i * 4]), g = Int(rgba[i * 4 + 1]), b = Int(rgba[i * 4 + 2])
                if max(r, max(g, b)) >= maxBrightness { continue }
                let d = bar.distance(x: Double(x), y: Double(y))
                if d.perpendicular > radius { continue }
                if d.along < 0 { left[i] = 1 } else if d.along > 1 { right[i] = 1 }
            }
        }
        // The bar's left end in image space is whichever end has the smaller x.
        if bar.x0 > bar.x1 { swap(&left, &right) }
        return (left, right)
    }

    /// Fraction of truth pixels the mask covers; nil when the truth is too
    /// small to judge.
    public static func recall(truth: [UInt8], mask: [UInt8], minimum: Int) -> (count: Int, recall: Double?) {
        var n = 0, hit = 0
        for i in 0..<truth.count where truth[i] != 0 {
            n += 1
            if mask[i] != 0 { hit += 1 }
        }
        return (n, n >= minimum ? Double(hit) / Double(n) : nil)
    }

    public static func evaluate(
        rgba: [UInt8], difference: [UInt8]?, person: [UInt8], mask: [UInt8], pose: BodyPose?, width: Int, height: Int,
        threshold: Int
    ) -> Result {
        var result = Result()
        let band = bandTruth(rgba: rgba, person: person, width: width, height: height)
        let b = recall(truth: band, mask: mask, minimum: 300)
        result.bandTruth = b.count
        result.bandRecall = b.recall
        if let difference,
            let plates = plateTruth(
                rgba: rgba, difference: difference, person: person, bar: pose?.barSegment, width: width, height: height,
                threshold: threshold)
        {
            let l = recall(truth: plates.left, mask: mask, minimum: 800)
            let r = recall(truth: plates.right, mask: mask, minimum: 800)
            result.plateTruthLeft = l.count
            result.plateTruthRight = r.count
            result.plateRecallLeft = l.recall
            result.plateRecallRight = r.recall
        }
        return result
    }
}
