import Foundation

public enum VisionPortraitValidator {
    private static let requiredRegions = [
        "faceContour",
        "leftEye",
        "rightEye",
        "leftEyebrow",
        "rightEyebrow",
        "nose",
        "outerLips",
        "innerLips",
        "allPoints",
    ]

    public static func validate(_ analysis: VisionPortraitAnalysis) throws {
        let manifest = analysis.manifest
        guard manifest.schemaVersion == 1,
              manifest.coordinateSpace == "normalized-top-left",
              manifest.source.width > 0,
              manifest.source.height > 0,
              manifest.source.sha256.count == 64
        else {
            throw VisionPortraitError.invalidImage("Invalid Vision manifest source")
        }
        guard let face = manifest.primaryFace else {
            throw VisionPortraitError.invalidImage("Vision did not find the primary face")
        }
        for name in requiredRegions {
            guard face.landmarkRegions.contains(where: { $0.name == name && !$0.points.isEmpty }) else {
                throw VisionPortraitError.invalidImage("Missing face landmark region: \(name)")
            }
        }
        let allPoints = face.landmarkRegions.first { $0.name == "allPoints" }?.points ?? []
        guard Set(allPoints).count >= 60 else {
            throw VisionPortraitError.invalidImage("Vision returned fewer than 60 unique face points")
        }
        guard manifest.features.count >= 60, manifest.features.count <= 512 else {
            throw VisionPortraitError.invalidImage("Vision feature count is outside the supported range")
        }
        for feature in manifest.features {
            guard (0...1).contains(feature.point.x),
                  (0...1).contains(feature.point.y),
                  (0...1).contains(feature.confidence)
            else {
                throw VisionPortraitError.invalidImage("Vision emitted an out-of-bounds feature")
            }
        }
        guard let maskReference = manifest.personMask,
              maskReference.containsPrimaryFaceCenter,
              let mask = analysis.personMask,
              let face = manifest.primaryFace,
              maskReference.width == mask.width,
              maskReference.height == mask.height
        else {
            throw VisionPortraitError.invalidImage("Invalid primary-person mask")
        }
        let pixels = mask.decodedPixels()
        guard pixels.count == mask.width * mask.height else {
            throw VisionPortraitError.invalidImage("Invalid primary-person mask")
        }
        let center = face.boundingBox.center
        let centerX = min(mask.width - 1, max(0, Int(center.x * Double(mask.width))))
        let centerY = min(mask.height - 1, max(0, Int(center.y * Double(mask.height))))
        guard pixels[centerY * mask.width + centerX] > 0 else {
            throw VisionPortraitError.invalidImage(
                "Primary-person mask does not contain the primary face"
            )
        }
    }
}
