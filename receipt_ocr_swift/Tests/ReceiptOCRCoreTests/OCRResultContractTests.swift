import XCTest
import CoreGraphics
@testable import ReceiptOCRCore

/// Contract tests for the payload the Mac worker hands to AWS.
///
/// The Python side (infra/upload_images/container_ocr handler) pins the SAME
/// fixture in test_swift_payload_contract.py, so a schema change on either
/// side of the S3/SQS boundary fails a local test suite before it fails in
/// the Lambda.
final class OCRResultContractTests: XCTestCase {

    static var fixtureURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures/swift_single_pass_contract.json")
    }

    // MARK: - Encoding: VisionOCREngine's snake_case output matches the
    // keys the Python parser requires.

    func test_receipt_output_encodes_python_parser_contract_keys() throws {
        let letter = Letter(
            text: "S",
            boundingBox: NormalizedRect(x: 0.08, y: 0.9, width: 0.02, height: 0.03),
            topLeft: CodablePoint(x: 0.08, y: 0.93),
            topRight: CodablePoint(x: 0.10, y: 0.93),
            bottomLeft: CodablePoint(x: 0.08, y: 0.9),
            bottomRight: CodablePoint(x: 0.10, y: 0.9),
            angleDegrees: 0, angleRadians: 0, confidence: 0.98
        )
        let word = Word(
            text: "3.99",
            boundingBox: NormalizedRect(x: 0.7, y: 0.9, width: 0.1, height: 0.03),
            topLeft: CodablePoint(x: 0.7, y: 0.93),
            topRight: CodablePoint(x: 0.8, y: 0.93),
            bottomLeft: CodablePoint(x: 0.7, y: 0.9),
            bottomRight: CodablePoint(x: 0.8, y: 0.9),
            angleDegrees: 0, angleRadians: 0, confidence: 0.97,
            letters: [letter],
            extractedData: ExtractedData(type: "currency", value: "3.99")
        )
        let line = Line(
            text: "TOTAL 3.99",
            boundingBox: NormalizedRect(x: 0.1, y: 0.9, width: 0.7, height: 0.03),
            topLeft: CodablePoint(x: 0.1, y: 0.93),
            topRight: CodablePoint(x: 0.8, y: 0.93),
            bottomLeft: CodablePoint(x: 0.1, y: 0.9),
            bottomRight: CodablePoint(x: 0.8, y: 0.9),
            angleDegrees: 0, angleRadians: 0, confidence: 0.96,
            words: [word]
        )
        let bounds = ReceiptBounds(
            topLeft: CodablePoint(x: 0.2, y: 0.8),
            topRight: CodablePoint(x: 0.8, y: 0.8),
            bottomRight: CodablePoint(x: 0.8, y: 0.2),
            bottomLeft: CodablePoint(x: 0.2, y: 0.2)
        )
        let prediction = LinePrediction(
            tokens: ["TOTAL", "3.99"],
            labels: ["O", "B-GRAND_TOTAL"],
            confidences: [0.5, 0.9],
            allProbabilities: nil
        )
        let barcode = Barcode(
            payload: "0123456789012",
            symbology: "EAN13",
            boundingBox: NormalizedRect(x: 0.3, y: 0.1, width: 0.4, height: 0.08),
            topLeft: CodablePoint(x: 0.3, y: 0.18),
            topRight: CodablePoint(x: 0.7, y: 0.18),
            bottomLeft: CodablePoint(x: 0.3, y: 0.1),
            bottomRight: CodablePoint(x: 0.7, y: 0.1),
            confidence: 0.9
        )
        let output = ReceiptOutput(
            clusterId: 1,
            bounds: bounds,
            warpedWidth: 1000,
            warpedHeight: 2400,
            s3Key: "IMG_X_receipt_1.png",
            lineIndices: [0, 1],
            lines: [line],
            layoutlmPredictions: [prediction],
            barcodes: [barcode]
        )

        // Exactly the encoder configuration VisionOCREngine uses.
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let data = try encoder.encode(output)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )

        // Receipt-level keys OCRProcessor._process_swift_single_pass reads.
        XCTAssertEqual(json["cluster_id"] as? Int, 1)
        XCTAssertEqual(json["warped_width"] as? Int, 1000)
        XCTAssertEqual(json["warped_height"] as? Int, 2400)
        XCTAssertNotNil(json["s3_key"] as? String)
        let boundsDict = try XCTUnwrap(json["bounds"] as? [String: Any])
        for corner in ["top_left", "top_right", "bottom_left", "bottom_right"] {
            let point = try XCTUnwrap(
                boundsDict[corner] as? [String: Any], corner
            )
            XCTAssertNotNil(point["x"] as? Double)
            XCTAssertNotNil(point["y"] as? Double)
        }

        // Line/word/letter geometry keys _parse_receipt_ocr_from_swift needs.
        let lines = try XCTUnwrap(json["lines"] as? [[String: Any]])
        let lineDict = try XCTUnwrap(lines.first)
        XCTAssertEqual(lineDict["text"] as? String, "TOTAL 3.99")
        for entity in [lineDict,
                       try XCTUnwrap((lineDict["words"] as? [[String: Any]])?.first),
                       try XCTUnwrap((((lineDict["words"] as? [[String: Any]])?
                            .first)?["letters"] as? [[String: Any]])?.first)] {
            let bbox = try XCTUnwrap(entity["bounding_box"] as? [String: Any])
            for key in ["x", "y", "width", "height"] {
                XCTAssertNotNil(bbox[key] as? Double, "bounding_box.\(key)")
            }
            for corner in ["top_left", "top_right", "bottom_left", "bottom_right"] {
                XCTAssertNotNil(entity[corner] as? [String: Any], corner)
            }
            XCTAssertNotNil(entity["angle_degrees"] as? Double)
            XCTAssertNotNil(entity["angle_radians"] as? Double)
            XCTAssertNotNil(entity["confidence"] as? Double)
        }
        let wordDict = try XCTUnwrap((lineDict["words"] as? [[String: Any]])?.first)
        let extracted = try XCTUnwrap(wordDict["extracted_data"] as? [String: Any])
        XCTAssertEqual(extracted["type"] as? String, "currency")

        // LayoutLM predictions: parallel arrays under snake_case key.
        let predictions = try XCTUnwrap(
            json["layoutlm_predictions"] as? [[String: Any]]
        )
        let predDict = try XCTUnwrap(predictions.first)
        XCTAssertEqual((predDict["tokens"] as? [String])?.count, 2)
        XCTAssertEqual((predDict["labels"] as? [String])?.count, 2)
        XCTAssertEqual((predDict["confidences"] as? [Double])?.count, 2)

        // Barcodes: payload + symbology + Vision-space geometry.
        let barcodes = try XCTUnwrap(json["barcodes"] as? [[String: Any]])
        let barcodeDict = try XCTUnwrap(barcodes.first)
        XCTAssertEqual(barcodeDict["symbology"] as? String, "EAN13")
        XCTAssertEqual(barcodeDict["payload"] as? String, "0123456789012")
        XCTAssertNotNil(barcodeDict["bounding_box"] as? [String: Any])
    }

    func test_classification_encodes_python_parser_contract_keys() throws {
        let classification = ClassificationResult(
            imageType: .photo,
            margins: ImageMargins(left: 0.18, right: 0.2, top: 0.12, bottom: 0.15),
            scanDistance: 0.61,
            photoDistance: 0.04,
            imageWidth: 3024,
            imageHeight: 4032
        )
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let data = try encoder.encode(classification)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        // The Lambda routes on image_type and sizes the Image entity from
        // image_width/image_height.
        XCTAssertEqual(json["image_type"] as? String, "PHOTO")
        XCTAssertEqual(json["image_width"] as? Int, 3024)
        XCTAssertEqual(json["image_height"] as? Int, 4032)
    }

    // MARK: - Worker: the shared fixture drives the full upload/handoff path.

    private final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var sentMessages: [String] = []
        var deleted: [SQSDeleteEntry] = []
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws { deleted.append(contentsOf: entries) }
        func sendMessage(queueURL: String, body: String) async throws { sentMessages.append(body) }
    }

    private final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:]
        var uploads: [(bucket: String, key: String)] = []
        func getObject(bucket: String, key: String) async throws -> Data {
            objects["\(bucket):\(key)"] ?? Data()
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {
            uploads.append((bucket, key))
        }
    }

    private final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:]
        var stages: [String: String] = [:]
        var routing: [OCRRoutingDecision] = []
        var wordLabels: [ReceiptWordLabel] = []
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            jobs["\(imageId):\(jobId)"]!
        }
        func updateOCRJob(_ job: OCRJob) async throws {
            jobs["\(job.imageId):\(job.jobId)"] = job
        }
        func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {
            stages["\(imageId):\(jobId)"] = stage
        }
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws {
            routing.append(decision)
        }
        func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws {
            wordLabels.append(contentsOf: labels)
        }
    }

    /// Engine that "produces" the shared contract fixture plus the warped
    /// receipt crop it references.
    private struct FixtureOCREngine: OCREngineProtocol {
        func process(images: [URL], outputDirectory: URL, includeClassification: Bool) throws -> [URL] {
            let fixture = try Data(contentsOf: OCRResultContractTests.fixtureURL)
            return try images.map { url in
                let out = outputDirectory
                    .appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".json")
                try fixture.write(to: out)
                // The warped crop the fixture's s3_key points at.
                try Data([0x89, 0x50, 0x4E, 0x47]).write(
                    to: outputDirectory.appendingPathComponent("IMG_CONTRACT-job-1_receipt_1.png")
                )
                return out
            }
        }
    }

    func test_worker_uploads_fixture_and_writes_pending_labels() async throws {
        let cfg = Config(
            ocrJobQueueURL: "q-jobs",
            ocrResultsQueueURL: "q-results",
            dynamoTableName: "tbl",
            region: "us-west-2",
            localstackEndpoint: nil,
            logLevel: "debug",
            rawBucketName: "raw-bucket"
        )
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()

        let imageId = "img-contract"
        let jobId = "job-contract"
        sqs.messages = [SQSMessage(
            messageId: "m1", receiptHandle: "rh1",
            body: "{\"image_id\":\"\(imageId)\",\"job_id\":\"\(jobId)\"}"
        )]
        let now = Date()
        dynamo.jobs["\(imageId):\(jobId)"] = OCRJob(
            imageId: imageId, jobId: jobId,
            s3Bucket: "raw-bucket", s3Key: "images/IMG_CONTRACT.png",
            createdAt: now, updatedAt: now, status: .pending
        )
        s3.objects["raw-bucket:images/IMG_CONTRACT.png"] = Data([0xFF, 0xD8, 0xFF])

        let worker = OCRWorker(
            config: cfg, ocr: FixtureOCREngine(), sqs: sqs, s3: s3, dynamo: dynamo
        )
        XCTAssertTrue(try await worker.processBatch())

        // Warped receipt crop and OCR JSON both land in the raw bucket.
        let uploadKeys = s3.uploads.map { $0.key }
        XCTAssertTrue(uploadKeys.contains(
            "receipts/\(imageId)/IMG_CONTRACT-job-1_receipt_1.png"
        ))
        let resultKey = try XCTUnwrap(
            uploadKeys.first { $0.hasPrefix("ocr_results/") }
        )

        // Routing decision points the Lambda at the uploaded JSON.
        XCTAssertEqual(dynamo.routing.count, 1)
        XCTAssertEqual(dynamo.routing[0].s3Key, resultKey)
        XCTAssertEqual(dynamo.routing[0].receiptCount, 1)

        // LayoutLM predictions become PENDING ReceiptWordLabels keyed by
        // 1-based ARRAY POSITIONS — the same positions the Python parser
        // assigns to ReceiptWord ids (skipped entries still consume an id).
        let keyed = Dictionary(
            uniqueKeysWithValues: dynamo.wordLabels.map {
                ("\($0.lineId):\($0.wordId)", $0)
            }
        )
        XCTAssertEqual(dynamo.wordLabels.count, 6)
        XCTAssertEqual(keyed["1:1"]?.label, "MERCHANT_NAME")
        XCTAssertEqual(keyed["1:2"]?.label, "MERCHANT_NAME")
        XCTAssertEqual(keyed["1:3"]?.label, "MERCHANT_NAME")
        XCTAssertEqual(keyed["3:1"]?.label, "PRODUCT_NAME")
        XCTAssertNil(keyed["3:2"])  // "O" token: skipped, position preserved
        XCTAssertEqual(keyed["3:3"]?.label, "LINE_TOTAL")
        XCTAssertEqual(keyed["4:2"]?.label, "GRAND_TOTAL")
        for label in dynamo.wordLabels {
            XCTAssertEqual(label.validationStatus, .pending)
            XCTAssertEqual(label.labelProposedBy, "auto-inference")
        }

        // The OCR-results message carries the pointer contract the Lambda
        // parses: job_id + image_id (routing), s3 location, receipt count.
        let sent = try XCTUnwrap(sqs.sentMessages.first)
        let message = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any]
        )
        XCTAssertEqual(message["image_id"] as? String, imageId)
        XCTAssertEqual(message["job_id"] as? String, jobId)
        XCTAssertEqual(message["s3_key"] as? String, resultKey)
        XCTAssertEqual(message["s3_bucket"] as? String, "raw-bucket")
        XCTAssertEqual(message["receipt_count"] as? Int, 1)
    }

    // MARK: - Fixture satisfies the worker's own parse guards.

    func test_fixture_receipts_satisfy_worker_parse_guards() throws {
        let data = try Data(contentsOf: Self.fixtureURL)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertNotNil(json["classification"] as? [String: Any])
        let receipts = try XCTUnwrap(json["receipts"] as? [[String: Any]])
        for receipt in receipts {
            // Mirror of parseReceiptsFromJSON's guard: a receipt missing any
            // of these is silently dropped (never uploaded, never persisted).
            XCTAssertNotNil(receipt["cluster_id"] as? Int)
            XCTAssertNotNil(receipt["s3_key"] as? String)
            XCTAssertNotNil(receipt["warped_width"] as? Int)
            XCTAssertNotNil(receipt["warped_height"] as? Int)
            XCTAssertNotNil(receipt["line_indices"] as? [Int])
            let bounds = try XCTUnwrap(receipt["bounds"] as? [String: Any])
            for corner in ["top_left", "top_right", "bottom_right", "bottom_left"] {
                let point = try XCTUnwrap(bounds[corner] as? [String: Double])
                XCTAssertNotNil(point["x"])
                XCTAssertNotNil(point["y"])
            }
            let predictions = try XCTUnwrap(
                receipt["layoutlm_predictions"] as? [[String: Any]]
            )
            for prediction in predictions {
                let tokens = try XCTUnwrap(prediction["tokens"] as? [String])
                let labels = try XCTUnwrap(prediction["labels"] as? [String])
                let confidences = try XCTUnwrap(
                    prediction["confidences"] as? [Double]
                )
                XCTAssertEqual(tokens.count, labels.count)
                XCTAssertEqual(tokens.count, confidences.count)
            }
        }
    }
}
