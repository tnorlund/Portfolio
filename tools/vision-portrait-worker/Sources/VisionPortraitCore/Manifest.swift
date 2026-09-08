import CoreGraphics
import Foundation

public enum VisionFeatureKind: String, Codable, Sendable {
    case faceLandmark = "face-landmark"
    case bodyJoint = "body-joint"
    case handJoint = "hand-joint"
    case faceCenter = "face-center"
    case humanCenter = "human-center"
    case saliency = "saliency"
    case contour = "contour"
}

public struct NormalizedPoint: Codable, Equatable, Hashable, Sendable {
    public let x: Double
    public let y: Double

    public init(x: Double, y: Double) {
        self.x = Geometry.quantize(x)
        self.y = Geometry.quantize(y)
    }
}

public struct NormalizedRect: Codable, Equatable, Sendable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = Geometry.quantize(x)
        self.y = Geometry.quantize(y)
        self.width = Geometry.quantize(width)
        self.height = Geometry.quantize(height)
    }

    public var center: NormalizedPoint {
        NormalizedPoint(x: x + width / 2, y: y + height / 2)
    }
}

public enum Geometry {
    public static let precision = 1_000_000.0

    public static func quantize(_ value: Double) -> Double {
        guard value.isFinite else { return 0 }
        return (value * precision).rounded() / precision
    }

    public static func topLeftPoint(fromVision point: CGPoint) -> NormalizedPoint {
        NormalizedPoint(
            x: min(1, max(0, point.x)),
            y: min(1, max(0, 1 - point.y))
        )
    }

    public static func topLeftRect(fromVision rect: CGRect) -> NormalizedRect {
        NormalizedRect(
            x: min(1, max(0, rect.minX)),
            y: min(1, max(0, 1 - rect.maxY)),
            width: min(1, max(0, rect.width)),
            height: min(1, max(0, rect.height))
        )
    }

    public static func faceLocalPoint(
        _ point: CGPoint,
        faceBoundingBox: CGRect
    ) -> NormalizedPoint {
        let imagePoint = CGPoint(
            x: faceBoundingBox.minX + point.x * faceBoundingBox.width,
            y: faceBoundingBox.minY + point.y * faceBoundingBox.height
        )
        return topLeftPoint(fromVision: imagePoint)
    }
}

public struct VisionFeature: Codable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let kind: VisionFeatureKind
    public let group: String
    public let point: NormalizedPoint
    public let confidence: Double

    public init(
        id: String,
        label: String,
        kind: VisionFeatureKind,
        group: String,
        point: NormalizedPoint,
        confidence: Double
    ) {
        self.id = id
        self.label = label
        self.kind = kind
        self.group = group
        self.point = point
        self.confidence = Geometry.quantize(min(1, max(0, confidence)))
    }
}

public struct LandmarkRegion: Codable, Equatable, Sendable {
    public let name: String
    public let points: [NormalizedPoint]
}

public struct FaceAnnotation: Codable, Equatable, Sendable {
    public let boundingBox: NormalizedRect
    public let confidence: Double
    public let captureQuality: Double?
    public let rollRadians: Double?
    public let yawRadians: Double?
    public let pitchRadians: Double?
    public let landmarkRegions: [LandmarkRegion]
}

public struct HumanAnnotation: Codable, Equatable, Sendable {
    public let boundingBox: NormalizedRect
    public let confidence: Double
    public let upperBodyOnly: Bool
}

public struct PoseAnnotation: Codable, Equatable, Sendable {
    public let index: Int
    public let joints: [VisionFeature]
}

public struct SaliencyAnnotation: Codable, Equatable, Sendable {
    public let kind: String
    public let boundingBox: NormalizedRect
    public let confidence: Double
}

public struct ClassificationAnnotation: Codable, Equatable, Sendable {
    public let identifier: String
    public let confidence: Double
}

public struct SourceAnnotation: Codable, Equatable, Sendable {
    public let fileName: String
    public let sha256: String
    public let width: Int
    public let height: Int
}

public struct GeneratorAnnotation: Codable, Equatable, Sendable {
    public let name: String
    public let version: Int
    public let swiftVersion: String
    public let operatingSystem: String
    public let sdk: String
    public let requestRevisions: [String: Int]
}

public struct PersonMaskReference: Codable, Equatable, Sendable {
    public let path: String
    public let sha256: String
    public let width: Int
    public let height: Int
    public let coverage: Double
    public let boundingBox: NormalizedRect
    public let containsPrimaryFaceCenter: Bool
}

public struct VisionPortraitManifest: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let coordinateSpace: String
    public let source: SourceAnnotation
    public let generator: GeneratorAnnotation
    public let primaryFaceAnchor: NormalizedPoint
    public let primaryFace: FaceAnnotation?
    public let humans: [HumanAnnotation]
    public let bodyPoses: [PoseAnnotation]
    public let handPoses: [PoseAnnotation]
    public let saliency: [SaliencyAnnotation]
    public let classifications: [ClassificationAnnotation]
    public let features: [VisionFeature]
    public let personMask: PersonMaskReference?
    public let diagnostics: [String]
}

public struct PersonMaskArtifact: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let coordinateSpace: String
    public let width: Int
    public let height: Int
    public let runs: [Int]

    public init(width: Int, height: Int, pixels: [UInt8]) {
        precondition(width > 0 && height > 0 && pixels.count == width * height)
        self.schemaVersion = 1
        self.coordinateSpace = "normalized-top-left"
        self.width = width
        self.height = height
        var encoded: [Int] = []
        encoded.reserveCapacity(max(2, pixels.count / 8))
        var current = Int(pixels[0])
        var count = 1
        for pixel in pixels.dropFirst() {
            let value = Int(pixel)
            if value == current {
                count += 1
            } else {
                encoded.append(current)
                encoded.append(count)
                current = value
                count = 1
            }
        }
        encoded.append(current)
        encoded.append(count)
        self.runs = encoded
    }

    public func decodedPixels() -> [UInt8] {
        var pixels: [UInt8] = []
        pixels.reserveCapacity(width * height)
        var index = 0
        while index + 1 < runs.count {
            pixels.append(contentsOf: repeatElement(UInt8(runs[index]), count: runs[index + 1]))
            index += 2
        }
        return pixels
    }
}

public enum VisionPortraitError: LocalizedError {
    case invalidImage(String)
    case invalidArguments(String)
    case artifactMismatch(String)

    public var errorDescription: String? {
        switch self {
        case .invalidImage(let message), .invalidArguments(let message), .artifactMismatch(let message):
            message
        }
    }
}
