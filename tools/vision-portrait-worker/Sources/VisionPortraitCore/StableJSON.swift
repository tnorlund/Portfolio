import CryptoKit
import Foundation

public enum StableJSON {
    public static func encode<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        var data = try encoder.encode(value)
        data.append(0x0A)
        return data
    }

    public static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public enum PrimarySubject {
    public static func faceIndex(
        in visionBoundingBoxes: [CGRect],
        anchor: NormalizedPoint
    ) -> Int? {
        visionBoundingBoxes.enumerated().min { left, right in
            score(left.element, anchor: anchor) < score(right.element, anchor: anchor)
        }?.offset
    }

    private static func score(_ visionBoundingBox: CGRect, anchor: NormalizedPoint) -> Double {
        let rect = Geometry.topLeftRect(fromVision: visionBoundingBox)
        let dx = rect.center.x - anchor.x
        let dy = rect.center.y - anchor.y
        let area = rect.width * rect.height
        return dx * dx + dy * dy - area * 0.2
    }
}
