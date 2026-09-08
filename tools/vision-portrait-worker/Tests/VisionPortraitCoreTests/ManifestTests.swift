import CoreGraphics
import XCTest
@testable import VisionPortraitCore

final class ManifestTests: XCTestCase {
    private func analysis(maskPixels: [UInt8] = [1, 1, 1, 1]) -> VisionPortraitAnalysis {
        let points = (0..<60).map { index in
            NormalizedPoint(
                x: 0.2 + Double(index % 10) * 0.01,
                y: 0.3 + Double(index / 10) * 0.01
            )
        }
        let regions = [
            "faceContour", "leftEye", "rightEye", "leftEyebrow",
            "rightEyebrow", "nose", "outerLips", "innerLips",
        ].map { LandmarkRegion(name: $0, points: [points[0]]) } + [
            LandmarkRegion(name: "allPoints", points: points),
        ]
        let features = points.enumerated().map { index, point in
            VisionFeature(
                id: "face.\(index)",
                label: "Face landmark",
                kind: .faceLandmark,
                group: "allPoints",
                point: point,
                confidence: 0.9
            )
        }
        let mask = PersonMaskArtifact(width: 2, height: 2, pixels: maskPixels)
        let face = FaceAnnotation(
            boundingBox: NormalizedRect(x: 0.25, y: 0.25, width: 0.5, height: 0.5),
            confidence: 0.9,
            captureQuality: 0.8,
            rollRadians: 0,
            yawRadians: 0,
            pitchRadians: 0,
            landmarkRegions: regions
        )
        let manifest = VisionPortraitManifest(
            schemaVersion: 1,
            coordinateSpace: "normalized-top-left",
            source: SourceAnnotation(
                fileName: "portrait.jpg",
                sha256: String(repeating: "a", count: 64),
                width: 960,
                height: 720
            ),
            generator: GeneratorAnnotation(
                name: "fixture",
                version: 1,
                swiftVersion: "6",
                operatingSystem: "test",
                sdk: "test",
                requestRevisions: [:]
            ),
            primaryFaceAnchor: NormalizedPoint(x: 0.5, y: 0.5),
            primaryFace: face,
            humans: [],
            bodyPoses: [],
            handPoses: [],
            saliency: [],
            classifications: [],
            features: features,
            personMask: PersonMaskReference(
                path: "/rotoscope/mask.json",
                sha256: String(repeating: "b", count: 64),
                width: 2,
                height: 2,
                coverage: 1,
                boundingBox: NormalizedRect(x: 0, y: 0, width: 1, height: 1),
                containsPrimaryFaceCenter: true
            ),
            diagnostics: []
        )
        return VisionPortraitAnalysis(manifest: manifest, personMask: mask)
    }

    func testVisionCoordinatesBecomeTopLeftCoordinates() {
        XCTAssertEqual(
            Geometry.topLeftPoint(fromVision: CGPoint(x: 0.25, y: 0.75)),
            NormalizedPoint(x: 0.25, y: 0.25)
        )
        XCTAssertEqual(
            Geometry.topLeftRect(fromVision: CGRect(x: 0.2, y: 0.3, width: 0.4, height: 0.5)),
            NormalizedRect(x: 0.2, y: 0.2, width: 0.4, height: 0.5)
        )
    }

    func testFaceLocalCoordinatesBecomeFullImageCoordinates() {
        let face = CGRect(x: 0.2, y: 0.3, width: 0.4, height: 0.5)
        XCTAssertEqual(
            Geometry.faceLocalPoint(CGPoint(x: 0.25, y: 0.8), faceBoundingBox: face),
            NormalizedPoint(x: 0.3, y: 0.3)
        )
    }

    func testPersonMaskRunLengthEncodingRoundTrips() {
        let pixels: [UInt8] = [0, 0, 1, 1, 1, 0, 1, 1]
        let artifact = PersonMaskArtifact(width: 4, height: 2, pixels: pixels)
        XCTAssertEqual(artifact.runs, [0, 2, 1, 3, 0, 1, 1, 2])
        XCTAssertEqual(artifact.decodedPixels(), pixels)
    }

    func testNumbersAreQuantizedAndBounded() {
        XCTAssertEqual(NormalizedPoint(x: 0.12345678, y: 0.87654321).x, 0.123457)
        let feature = VisionFeature(
            id: "face.0",
            label: "Face",
            kind: .faceCenter,
            group: "face",
            point: NormalizedPoint(x: 0.5, y: 0.5),
            confidence: 2
        )
        XCTAssertEqual(feature.confidence, 1)
    }

    func testPrimaryFaceUsesAnchorAndAreaRatherThanObservationOrder() {
        let backgroundFace = CGRect(x: 0.02, y: 0.72, width: 0.08, height: 0.1)
        let primaryFace = CGRect(x: 0.28, y: 0.28, width: 0.24, height: 0.34)
        XCTAssertEqual(
            PrimarySubject.faceIndex(
                in: [backgroundFace, primaryFace],
                anchor: NormalizedPoint(x: 0.4, y: 0.56)
            ),
            1
        )
    }

    func testStableJSONIsSortedAndNewlineTerminated() throws {
        struct Fixture: Codable {
            let z: Int
            let a: Int
        }
        let fixture = Fixture(z: 1, a: 2)
        let first = try StableJSON.encode(fixture)
        let second = try StableJSON.encode(fixture)
        XCTAssertEqual(first, second)
        XCTAssertEqual(first.last, 0x0A)
        XCTAssertLessThan(
            String(decoding: first, as: UTF8.self).range(of: "\"a\"")!.lowerBound,
            String(decoding: first, as: UTF8.self).range(of: "\"z\"")!.lowerBound
        )
    }

    func testValidatorAcceptsBoundedCanonicalAnalysis() throws {
        XCTAssertNoThrow(try VisionPortraitValidator.validate(analysis()))
    }

    func testValidatorRejectsMaskThatMissesPrimaryFace() {
        XCTAssertThrowsError(
            try VisionPortraitValidator.validate(analysis(maskPixels: [1, 1, 1, 0]))
        )
    }
}
