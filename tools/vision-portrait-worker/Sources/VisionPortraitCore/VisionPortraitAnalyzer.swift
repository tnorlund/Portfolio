import CoreGraphics
import CoreVideo
import CryptoKit
import Foundation
import ImageIO
import Vision

public struct VisionPortraitAnalysis: Sendable {
    public let manifest: VisionPortraitManifest
    public let personMask: PersonMaskArtifact?

    public init(manifest: VisionPortraitManifest, personMask: PersonMaskArtifact?) {
        self.manifest = manifest
        self.personMask = personMask
    }
}

public final class VisionPortraitAnalyzer {
    public static let primaryFaceAnchor = NormalizedPoint(x: 0.4, y: 0.56)
    public static let minimumPointConfidence = 0.08
    public static let maskWidth = 240
    public static let maskHeight = 180
    public static let maximumContourFeatures = 256

    public init() {}

    public func analyze(
        imageURL: URL,
        maskPublicPath: String
    ) throws -> VisionPortraitAnalysis {
        let sourceData = try Data(contentsOf: imageURL)
        let image = try imageProperties(for: imageURL)
        let orientation = image.orientation
        var diagnostics: [String] = []
        let anchor = Self.primaryFaceAnchor

        let faceRequest = VNDetectFaceRectanglesRequest()
        faceRequest.revision = VNDetectFaceRectanglesRequestRevision3
        perform(faceRequest, imageURL: imageURL, orientation: orientation, name: "face-rectangles", diagnostics: &diagnostics)
        let faces = faceRequest.results ?? []
        let primaryFaceIndex = PrimarySubject.faceIndex(
            in: faces.map(\.boundingBox),
            anchor: anchor
        )
        let selectedFace = primaryFaceIndex.map { faces[$0] }

        let landmarksRequest = VNDetectFaceLandmarksRequest()
        landmarksRequest.revision = VNDetectFaceLandmarksRequestRevision3
        landmarksRequest.constellation = .constellation76Points
        landmarksRequest.inputFaceObservations = selectedFace.map { [$0] }
        perform(landmarksRequest, imageURL: imageURL, orientation: orientation, name: "face-landmarks", diagnostics: &diagnostics)

        let qualityRequest = VNDetectFaceCaptureQualityRequest()
        qualityRequest.revision = VNDetectFaceCaptureQualityRequestRevision3
        qualityRequest.inputFaceObservations = selectedFace.map { [$0] }
        perform(qualityRequest, imageURL: imageURL, orientation: orientation, name: "face-capture-quality", diagnostics: &diagnostics)

        let humanRequest = VNDetectHumanRectanglesRequest()
        humanRequest.revision = VNDetectHumanRectanglesRequestRevision2
        humanRequest.upperBodyOnly = false
        perform(humanRequest, imageURL: imageURL, orientation: orientation, name: "human-rectangles", diagnostics: &diagnostics)

        let bodyRequest = VNDetectHumanBodyPoseRequest()
        bodyRequest.revision = VNDetectHumanBodyPoseRequestRevision1
        perform(bodyRequest, imageURL: imageURL, orientation: orientation, name: "body-pose", diagnostics: &diagnostics)

        let handRequest = VNDetectHumanHandPoseRequest()
        handRequest.revision = VNDetectHumanHandPoseRequestRevision1
        handRequest.maximumHandCount = 2
        perform(handRequest, imageURL: imageURL, orientation: orientation, name: "hand-pose", diagnostics: &diagnostics)

        let attentionRequest = VNGenerateAttentionBasedSaliencyImageRequest()
        attentionRequest.revision = VNGenerateAttentionBasedSaliencyImageRequestRevision2
        perform(attentionRequest, imageURL: imageURL, orientation: orientation, name: "attention-saliency", diagnostics: &diagnostics)

        let objectnessRequest = VNGenerateObjectnessBasedSaliencyImageRequest()
        objectnessRequest.revision = VNGenerateObjectnessBasedSaliencyImageRequestRevision2
        perform(objectnessRequest, imageURL: imageURL, orientation: orientation, name: "objectness-saliency", diagnostics: &diagnostics)

        let contourRequest = VNDetectContoursRequest()
        contourRequest.revision = VNDetectContourRequestRevision1
        contourRequest.maximumImageDimension = 256
        contourRequest.contrastAdjustment = 1.5
        contourRequest.detectsDarkOnLight = false
        perform(contourRequest, imageURL: imageURL, orientation: orientation, name: "contours", diagnostics: &diagnostics)

        let faceObservation = landmarksRequest.results?.first ?? selectedFace
        let quality = qualityRequest.results?.first?.faceCaptureQuality.map(Double.init)
        let primaryFace = faceObservation.map { faceAnnotation(from: $0, quality: quality) }

        let humans = (humanRequest.results ?? [])
            .map {
                HumanAnnotation(
                    boundingBox: Geometry.topLeftRect(fromVision: $0.boundingBox),
                    confidence: Geometry.quantize(Double($0.confidence)),
                    upperBodyOnly: $0.upperBodyOnly
                )
            }
            .sorted { rectSortKey($0.boundingBox) < rectSortKey($1.boundingBox) }

        let bodyPoses = poseAnnotations(
            observations: bodyRequest.results ?? [],
            kind: .bodyJoint,
            group: "body"
        )
        let handPoses = poseAnnotations(
            observations: handRequest.results ?? [],
            kind: .handJoint,
            group: "hand"
        )

        let saliency = saliencyAnnotations(
            attention: attentionRequest.results ?? [],
            objectness: objectnessRequest.results ?? []
        )

        var features: [VisionFeature] = []
        if let face = faceObservation {
            features.append(contentsOf: faceFeatures(from: face))
        }
        features.append(contentsOf: bodyPoses.flatMap(\.joints))
        features.append(contentsOf: handPoses.flatMap(\.joints))
        features.append(contentsOf: saliency.enumerated().map { index, item in
            VisionFeature(
                id: "saliency.\(index)",
                label: item.kind == "attention" ? "Attention" : "Object",
                kind: .saliency,
                group: "background",
                point: item.boundingBox.center,
                confidence: item.confidence
            )
        })
        features.append(contentsOf: contourFeatures(from: contourRequest.results?.first))
        features = deduplicatedAndSorted(features)

        let maskResult = personMask(
            imageURL: imageURL,
            orientation: orientation,
            primaryFaceCenter: primaryFace?.boundingBox.center,
            diagnostics: &diagnostics
        )
        let maskData = try maskResult.map { try StableJSON.encode($0.artifact) }
        let maskReference = maskResult.flatMap { result in
            maskData.map { data in
                PersonMaskReference(
                    path: maskPublicPath,
                    sha256: StableJSON.sha256(data),
                    width: result.artifact.width,
                    height: result.artifact.height,
                    coverage: result.coverage,
                    boundingBox: result.boundingBox,
                    containsPrimaryFaceCenter: result.containsPrimaryFaceCenter
                )
            }
        }

        let manifest = VisionPortraitManifest(
            schemaVersion: 1,
            coordinateSpace: "normalized-top-left",
            source: SourceAnnotation(
                fileName: imageURL.lastPathComponent,
                sha256: StableJSON.sha256(sourceData),
                width: image.width,
                height: image.height
            ),
            generator: GeneratorAnnotation(
                name: "vision-portrait-worker",
                version: 1,
                swiftVersion: "6.3",
                operatingSystem: ProcessInfo.processInfo.operatingSystemVersionString,
                sdk: "macOS 26.5",
                requestRevisions: [
                    "attentionSaliency": VNGenerateAttentionBasedSaliencyImageRequestRevision2,
                    "bodyPose": VNDetectHumanBodyPoseRequestRevision1,
                    "contours": VNDetectContourRequestRevision1,
                    "faceCaptureQuality": VNDetectFaceCaptureQualityRequestRevision3,
                    "faceLandmarks": VNDetectFaceLandmarksRequestRevision3,
                    "faceRectangles": VNDetectFaceRectanglesRequestRevision3,
                    "handPose": VNDetectHumanHandPoseRequestRevision1,
                    "humanRectangles": VNDetectHumanRectanglesRequestRevision2,
                    "objectnessSaliency": VNGenerateObjectnessBasedSaliencyImageRequestRevision2,
                    "personInstanceMask": VNGeneratePersonInstanceMaskRequestRevision1,
                    "personSegmentationFallback": VNGeneratePersonSegmentationRequestRevision1,
                ]
            ),
            primaryFaceAnchor: anchor,
            primaryFace: primaryFace,
            humans: humans,
            bodyPoses: bodyPoses,
            handPoses: handPoses,
            saliency: saliency,
            classifications: [],
            features: features,
            personMask: maskReference,
            diagnostics: diagnostics.sorted()
        )
        return VisionPortraitAnalysis(manifest: manifest, personMask: maskResult?.artifact)
    }

    private func perform(
        _ request: VNRequest,
        imageURL: URL,
        orientation: CGImagePropertyOrientation,
        name: String,
        diagnostics: inout [String]
    ) {
        do {
            let handler = VNImageRequestHandler(url: imageURL, orientation: orientation)
            try handler.perform([request])
        } catch {
            diagnostics.append("\(name): \(error.localizedDescription)")
        }
    }

    private func faceAnnotation(from face: VNFaceObservation, quality: Double?) -> FaceAnnotation {
        FaceAnnotation(
            boundingBox: Geometry.topLeftRect(fromVision: face.boundingBox),
            confidence: Geometry.quantize(Double(face.confidence)),
            captureQuality: quality.map(Geometry.quantize),
            rollRadians: face.roll.map { Geometry.quantize($0.doubleValue) },
            yawRadians: face.yaw.map { Geometry.quantize($0.doubleValue) },
            pitchRadians: face.pitch.map { Geometry.quantize($0.doubleValue) },
            landmarkRegions: landmarkRegions(from: face)
        )
    }

    private func landmarkRegions(from face: VNFaceObservation) -> [LandmarkRegion] {
        guard let landmarks = face.landmarks else { return [] }
        let regions: [(String, VNFaceLandmarkRegion2D?)] = [
            ("faceContour", landmarks.faceContour),
            ("leftEye", landmarks.leftEye),
            ("rightEye", landmarks.rightEye),
            ("leftEyebrow", landmarks.leftEyebrow),
            ("rightEyebrow", landmarks.rightEyebrow),
            ("leftPupil", landmarks.leftPupil),
            ("rightPupil", landmarks.rightPupil),
            ("nose", landmarks.nose),
            ("noseCrest", landmarks.noseCrest),
            ("medianLine", landmarks.medianLine),
            ("outerLips", landmarks.outerLips),
            ("innerLips", landmarks.innerLips),
            ("allPoints", landmarks.allPoints),
        ]
        return regions.compactMap { name, region in
            guard let region else { return nil }
            let points = (0..<region.pointCount).map { index in
                Geometry.faceLocalPoint(
                    region.normalizedPoints[index],
                    faceBoundingBox: face.boundingBox
                )
            }
            return LandmarkRegion(name: name, points: points)
        }
    }

    private func faceFeatures(from face: VNFaceObservation) -> [VisionFeature] {
        var output: [VisionFeature] = [
            VisionFeature(
                id: "face.center",
                label: "Face",
                kind: .faceCenter,
                group: "face",
                point: Geometry.topLeftRect(fromVision: face.boundingBox).center,
                confidence: Double(face.confidence)
            )
        ]
        var positions = Set<NormalizedPoint>()
        for region in landmarkRegions(from: face) where region.name != "allPoints" {
            for (index, point) in region.points.enumerated() where positions.insert(point).inserted {
                output.append(
                    VisionFeature(
                        id: "face.\(region.name).\(index)",
                        label: humanLabel(region.name),
                        kind: .faceLandmark,
                        group: region.name,
                        point: point,
                        confidence: Double(face.confidence)
                    )
                )
            }
        }
        if let allPoints = landmarkRegions(from: face).first(where: { $0.name == "allPoints" }) {
            for (index, point) in allPoints.points.enumerated() where positions.insert(point).inserted {
                output.append(
                    VisionFeature(
                        id: "face.allPoints.\(index)",
                        label: "Face landmark",
                        kind: .faceLandmark,
                        group: "allPoints",
                        point: point,
                        confidence: Double(face.confidence)
                    )
                )
            }
        }
        return output
    }

    private func poseAnnotations<Observation: VNRecognizedPointsObservation>(
        observations: [Observation],
        kind: VisionFeatureKind,
        group: String
    ) -> [PoseAnnotation] {
        observations.enumerated().map { observationIndex, observation in
            let points = (try? observation.recognizedPoints(forGroupKey: .all)) ?? [:]
            let joints = points.compactMap { key, point -> VisionFeature? in
                guard point.confidence >= Float(Self.minimumPointConfidence) else { return nil }
                let rawName = key.rawValue
                return VisionFeature(
                    id: "\(group).\(observationIndex).\(rawName)",
                    label: humanLabel(rawName),
                    kind: kind,
                    group: group,
                    point: Geometry.topLeftPoint(fromVision: point.location),
                    confidence: Double(point.confidence)
                )
            }.sorted { $0.id < $1.id }
            return PoseAnnotation(index: observationIndex, joints: joints)
        }
    }

    private func saliencyAnnotations(
        attention: [VNSaliencyImageObservation],
        objectness: [VNSaliencyImageObservation]
    ) -> [SaliencyAnnotation] {
        let items = [
            ("attention", attention.flatMap { $0.salientObjects ?? [] }),
            ("objectness", objectness.flatMap { $0.salientObjects ?? [] }),
        ]
        return items.flatMap { kind, observations in
            observations.map {
                SaliencyAnnotation(
                    kind: kind,
                    boundingBox: Geometry.topLeftRect(fromVision: $0.boundingBox),
                    confidence: Geometry.quantize(Double($0.confidence))
                )
            }
        }.sorted {
            ($0.kind, rectSortKey($0.boundingBox)) < ($1.kind, rectSortKey($1.boundingBox))
        }
    }

    private func contourFeatures(from observation: VNContoursObservation?) -> [VisionFeature] {
        guard let observation else { return [] }
        let contours = observation.topLevelContours
            .sorted { $0.pointCount > $1.pointCount }
            .prefix(48)
        var features: [VisionFeature] = []
        for (contourIndex, contour) in contours.enumerated() {
            let sampleCount = min(8, contour.pointCount)
            guard sampleCount > 0 else { continue }
            for sample in 0..<sampleCount {
                let pointIndex = min(
                    contour.pointCount - 1,
                    sample * contour.pointCount / sampleCount
                )
                let point = contour.normalizedPoints[pointIndex]
                features.append(
                    VisionFeature(
                        id: "contour.\(contourIndex).\(sample)",
                        label: "Contour",
                        kind: .contour,
                        group: "background",
                        point: Geometry.topLeftPoint(
                            fromVision: CGPoint(x: Double(point.x), y: Double(point.y))
                        ),
                        confidence: 0.35
                    )
                )
                if features.count >= Self.maximumContourFeatures { return features }
            }
        }
        return features
    }

    private func deduplicatedAndSorted(_ input: [VisionFeature]) -> [VisionFeature] {
        let priority: [VisionFeatureKind: Int] = [
            .faceLandmark: 0,
            .faceCenter: 1,
            .bodyJoint: 2,
            .handJoint: 3,
            .humanCenter: 4,
            .saliency: 5,
            .contour: 6,
        ]
        var positions = Set<NormalizedPoint>()
        return input.sorted {
            (priority[$0.kind] ?? 99, $0.id) < (priority[$1.kind] ?? 99, $1.id)
        }.filter { positions.insert($0.point).inserted }
    }

    private struct MaskResult {
        let artifact: PersonMaskArtifact
        let coverage: Double
        let boundingBox: NormalizedRect
        let containsPrimaryFaceCenter: Bool
    }

    private func personMask(
        imageURL: URL,
        orientation: CGImagePropertyOrientation,
        primaryFaceCenter: NormalizedPoint?,
        diagnostics: inout [String]
    ) -> MaskResult? {
        let request = VNGeneratePersonInstanceMaskRequest()
        request.revision = VNGeneratePersonInstanceMaskRequestRevision1
        do {
            let handler = VNImageRequestHandler(url: imageURL, orientation: orientation)
            try handler.perform([request])
            guard let observation = request.results?.first else {
                diagnostics.append("person-instance-mask: no observations")
                return fallbackPersonMask(
                    imageURL: imageURL,
                    orientation: orientation,
                    primaryFaceCenter: primaryFaceCenter,
                    diagnostics: &diagnostics
                )
            }
            let instances = observation.allInstances.map { $0 }
            var best: (score: Float, index: Int, buffer: CVPixelBuffer)?
            for instance in instances {
                let selected = IndexSet(integer: instance)
                let buffer = try observation.generateScaledMaskForImage(
                    forInstances: selected,
                    from: handler
                )
                let centerValue = primaryFaceCenter.map { maskValue(buffer, point: $0) } ?? 0
                let score = centerValue + Float(maskCoverage(buffer)) * 0.01
                if best == nil || score > best!.score {
                    best = (score, instance, buffer)
                }
            }
            guard let best else {
                diagnostics.append("person-instance-mask: no instances")
                return nil
            }
            return maskResult(buffer: best.buffer, primaryFaceCenter: primaryFaceCenter)
        } catch {
            diagnostics.append("person-instance-mask: \(error.localizedDescription)")
            return fallbackPersonMask(
                imageURL: imageURL,
                orientation: orientation,
                primaryFaceCenter: primaryFaceCenter,
                diagnostics: &diagnostics
            )
        }
    }

    private func fallbackPersonMask(
        imageURL: URL,
        orientation: CGImagePropertyOrientation,
        primaryFaceCenter: NormalizedPoint?,
        diagnostics: inout [String]
    ) -> MaskResult? {
        let request = VNGeneratePersonSegmentationRequest()
        request.revision = VNGeneratePersonSegmentationRequestRevision1
        request.qualityLevel = .accurate
        request.outputPixelFormat = kCVPixelFormatType_OneComponent32Float
        do {
            let handler = VNImageRequestHandler(url: imageURL, orientation: orientation)
            try handler.perform([request])
            guard let buffer = request.results?.first?.pixelBuffer else {
                diagnostics.append("person-segmentation-fallback: no observations")
                return nil
            }
            diagnostics.append("person-segmentation-fallback: used combined person mask")
            return maskResult(buffer: buffer, primaryFaceCenter: primaryFaceCenter)
        } catch {
            diagnostics.append("person-segmentation-fallback: \(error.localizedDescription)")
            return nil
        }
    }

    private func maskResult(
        buffer: CVPixelBuffer,
        primaryFaceCenter: NormalizedPoint?
    ) -> MaskResult {
        let pixels = downsampleMask(buffer, width: Self.maskWidth, height: Self.maskHeight)
        let artifact = PersonMaskArtifact(width: Self.maskWidth, height: Self.maskHeight, pixels: pixels)
        let onCount = pixels.reduce(0) { $0 + ($1 > 0 ? 1 : 0) }
        var minX = Self.maskWidth
        var minY = Self.maskHeight
        var maxX = -1
        var maxY = -1
        for y in 0..<Self.maskHeight {
            for x in 0..<Self.maskWidth where pixels[y * Self.maskWidth + x] > 0 {
                minX = min(minX, x)
                minY = min(minY, y)
                maxX = max(maxX, x)
                maxY = max(maxY, y)
            }
        }
        let bounds = maxX >= minX && maxY >= minY
            ? NormalizedRect(
                x: Double(minX) / Double(Self.maskWidth),
                y: Double(minY) / Double(Self.maskHeight),
                width: Double(maxX - minX + 1) / Double(Self.maskWidth),
                height: Double(maxY - minY + 1) / Double(Self.maskHeight)
            )
            : NormalizedRect(x: 0, y: 0, width: 0, height: 0)
        let containsCenter = primaryFaceCenter.map { point in
            let x = min(Self.maskWidth - 1, max(0, Int(point.x * Double(Self.maskWidth))))
            let y = min(Self.maskHeight - 1, max(0, Int(point.y * Double(Self.maskHeight))))
            return pixels[y * Self.maskWidth + x] > 0
        } ?? false
        return MaskResult(
            artifact: artifact,
            coverage: Geometry.quantize(Double(onCount) / Double(pixels.count)),
            boundingBox: bounds,
            containsPrimaryFaceCenter: containsCenter
        )
    }

    private func downsampleMask(_ buffer: CVPixelBuffer, width: Int, height: Int) -> [UInt8] {
        return (0..<height).flatMap { y in
            (0..<width).map { x in
                let sourcePoint = NormalizedPoint(
                    x: (Double(x) + 0.5) / Double(width),
                    y: (Double(y) + 0.5) / Double(height)
                )
                return maskValue(buffer, point: sourcePoint) >= 0.5 ? 1 : 0
            }
        }
    }

    private func maskCoverage(_ buffer: CVPixelBuffer) -> Double {
        let sampleWidth = 48
        let sampleHeight = 36
        var onCount = 0
        for y in 0..<sampleHeight {
            for x in 0..<sampleWidth {
                let point = NormalizedPoint(
                    x: (Double(x) + 0.5) / Double(sampleWidth),
                    y: (Double(y) + 0.5) / Double(sampleHeight)
                )
                if maskValue(buffer, point: point) >= 0.5 { onCount += 1 }
            }
        }
        return Double(onCount) / Double(sampleWidth * sampleHeight)
    }

    private func maskValue(_ buffer: CVPixelBuffer, point: NormalizedPoint) -> Float {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return 0 }
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let x = min(width - 1, max(0, Int(point.x * Double(width))))
        let y = min(height - 1, max(0, Int(point.y * Double(height))))
        let row = base.advanced(by: y * CVPixelBufferGetBytesPerRow(buffer))
        switch CVPixelBufferGetPixelFormatType(buffer) {
        case kCVPixelFormatType_OneComponent32Float:
            return row.assumingMemoryBound(to: Float.self)[x]
        case kCVPixelFormatType_OneComponent8:
            return Float(row.assumingMemoryBound(to: UInt8.self)[x]) / 255
        default:
            return 0
        }
    }

    private func imageProperties(
        for url: URL
    ) throws -> (width: Int, height: Int, orientation: CGImagePropertyOrientation) {
        guard
            let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
            let width = properties[kCGImagePropertyPixelWidth] as? Int,
            let height = properties[kCGImagePropertyPixelHeight] as? Int,
            width > 0,
            height > 0
        else {
            throw VisionPortraitError.invalidImage("Unable to read image dimensions")
        }
        let rawOrientation = (properties[kCGImagePropertyOrientation] as? UInt32) ?? 1
        let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up
        let swapsDimensions: Bool = [.left, .leftMirrored, .right, .rightMirrored].contains(orientation)
        return swapsDimensions ? (height, width, orientation) : (width, height, orientation)
    }

    private func humanLabel(_ raw: String) -> String {
        let stripped = raw
            .replacingOccurrences(of: "VNHumanBodyPoseObservationJointName", with: "")
            .replacingOccurrences(of: "VNHumanHandPoseObservationJointName", with: "")
        var output = ""
        for character in stripped {
            if character.isUppercase && !output.isEmpty { output.append(" ") }
            output.append(character)
        }
        return output.isEmpty ? raw : output.prefix(1).uppercased() + output.dropFirst()
    }

    private func rectSortKey(_ rect: NormalizedRect) -> String {
        String(format: "%0.6f:%0.6f:%0.6f:%0.6f", rect.y, rect.x, rect.height, rect.width)
    }
}
