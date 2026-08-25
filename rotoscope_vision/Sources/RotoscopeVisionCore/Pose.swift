import CoreGraphics
import CoreVideo
import Foundation
import Vision

/// Body-pose keypoints (image coordinates, y down) for the subject, and the
/// structural lines derived from them: the bar through the elbow crooks
/// (falling back to the wrists) and the band between the ankles.
public struct BodyPose {
    public struct Point {
        public var x: Double
        public var y: Double
        public var confidence: Double
    }
    public var leftElbow: Point?
    public var rightElbow: Point?
    public var leftWrist: Point?
    public var rightWrist: Point?
    public var leftAnkle: Point?
    public var rightAnkle: Point?
    public var leftShoulder: Point?
    public var rightShoulder: Point?

    public struct Segment {
        public var x0: Double, y0: Double, x1: Double, y1: Double
        public init(x0: Double, y0: Double, x1: Double, y1: Double) {
            self.x0 = x0; self.y0 = y0; self.x1 = x1; self.y1 = y1
        }
        public var length: Double { hypot(x1 - x0, y1 - y0) }
        /// Signed distance of a point to the infinite line (px) and its
        /// parameter along the segment (0…1 inside).
        public func distance(x: Double, y: Double) -> (perpendicular: Double, along: Double) {
            let vx = x1 - x0, vy = y1 - y0
            let len2 = max(1e-6, vx * vx + vy * vy)
            let t = ((x - x0) * vx + (y - y0) * vy) / len2
            let px = x0 + t * vx, py = y0 + t * vy
            return (hypot(x - px, y - py), t)
        }
    }

    /// The bar line: elbows when both are confident (Zercher / front rack),
    /// otherwise wrists.
    public var barSegment: Segment? {
        if let l = leftElbow, let r = rightElbow, l.confidence > 0.3, r.confidence > 0.3 {
            return Segment(x0: l.x, y0: l.y, x1: r.x, y1: r.y)
        }
        if let l = leftWrist, let r = rightWrist, l.confidence > 0.3, r.confidence > 0.3 {
            return Segment(x0: l.x, y0: l.y, x1: r.x, y1: r.y)
        }
        return nil
    }

    public var bandSegment: Segment? {
        if let l = leftAnkle, let r = rightAnkle, l.confidence > 0.3, r.confidence > 0.3 {
            return Segment(x0: l.x, y0: l.y, x1: r.x, y1: r.y)
        }
        return nil
    }

    /// Lowest confident ankle, the floor contact height.
    public var floorY: Double? {
        let ys = [leftAnkle, rightAnkle].compactMap { $0 }.filter { $0.confidence > 0.3 }.map { $0.y }
        return ys.max()
    }
}

public final class PoseDetector {
    public let width: Int
    public let height: Int

    public init(width: Int, height: Int) {
        self.width = width
        self.height = height
    }

    /// Detects poses and returns the one whose shoulder midpoint lies on the
    /// subject mask (largest such pose; otherwise nil).
    public func detect(_ pixelBuffer: CVPixelBuffer, subjectMask: [UInt8]) throws -> BodyPose? {
        let request = VNDetectHumanBodyPoseRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up, options: [:])
        try handler.perform([request])
        var best: (pose: BodyPose, extent: Double)?
        for observation in request.results ?? [] {
            guard let points = try? observation.recognizedPoints(.all) else { continue }
            func point(_ name: VNHumanBodyPoseObservation.JointName) -> BodyPose.Point? {
                guard let p = points[name], p.confidence > 0 else { return nil }
                return BodyPose.Point(
                    x: Double(p.location.x) * Double(width),
                    y: (1 - Double(p.location.y)) * Double(height),
                    confidence: Double(p.confidence))
            }
            let pose = BodyPose(
                leftElbow: point(.leftElbow), rightElbow: point(.rightElbow),
                leftWrist: point(.leftWrist), rightWrist: point(.rightWrist),
                leftAnkle: point(.leftAnkle), rightAnkle: point(.rightAnkle),
                leftShoulder: point(.leftShoulder), rightShoulder: point(.rightShoulder))
            guard let ls = pose.leftShoulder, let rs = pose.rightShoulder else { continue }
            let cx = Int((ls.x + rs.x) / 2), cy = Int((ls.y + rs.y) / 2)
            guard cx >= 0, cx < width, cy >= 0, cy < height, subjectMask[cy * width + cx] > 127 else { continue }
            let extent = abs(ls.x - rs.x)
            if best == nil || extent > best!.extent { best = (pose, extent) }
        }
        return best?.pose
    }
}
