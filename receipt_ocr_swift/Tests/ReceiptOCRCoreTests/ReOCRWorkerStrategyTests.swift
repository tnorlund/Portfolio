import Foundation
import Testing

@testable import ReceiptOCRCore

/// Contract + plumbing tests for the SMART RE-OCR strategy fields:
/// the DynamoDB `reocr_strategy` / `reocr_mechanism` fields written by the
/// Python side must parse per the contract (absent → plain), and the worker
/// must route a REGIONAL_REOCR job's strategy to the matching preprocess
/// transform and record `reocr_strategy_applied` in the uploaded result.
/// Uses swift-testing (not XCTest) so the suite also runs on machines with
/// only CommandLineTools.
@Suite struct ReOCRStrategyContractTests {

    private func baseItem(strategy: Any?, mechanism: Any?) -> [String: Any] {
        var item: [String: Any] = [
            "PK": ["S": "IMAGE#img-1"],
            "SK": ["S": "OCR_JOB#job-1"],
            "TYPE": ["S": "OCR_JOB"],
            "s3_bucket": ["S": "b"],
            "s3_key": ["S": "raw/img.png"],
            "created_at": ["S": "2026-08-03T00:00:00.000+00:00"],
            "updated_at": ["S": "2026-08-03T00:00:00.000+00:00"],
            "status": ["S": "PENDING"],
            "job_type": ["S": "REGIONAL_REOCR"],
        ]
        if let strategy = strategy { item["reocr_strategy"] = strategy }
        if let mechanism = mechanism { item["reocr_mechanism"] = mechanism }
        return item
    }

    @Test func absentStrategyDefaultsToPlain() throws {
        let job = try OCRJob.fromItem(baseItem(strategy: nil, mechanism: nil))
        #expect(job.reocrStrategy == .plain)
        #expect(job.reocrMechanism == nil)
    }

    @Test func strategyStringsParsePerContract() throws {
        let cases: [(String, ReOCRStrategy)] = [
            ("plain", .plain),
            ("invert", .invert),
            ("deskew", .deskew),
            ("upscale2x", .upscale2x),
        ]
        for (raw, expected) in cases {
            let job = try OCRJob.fromItem(baseItem(strategy: ["S": raw], mechanism: nil))
            #expect(job.reocrStrategy == expected, "raw=\(raw)")
        }
    }

    @Test func unrecognizedStrategyFallsBackToPlain() throws {
        let job = try OCRJob.fromItem(baseItem(strategy: ["S": "sharpen9000"], mechanism: nil))
        #expect(job.reocrStrategy == .plain)
    }

    @Test func nullStrategyFallsBackToPlain() throws {
        let job = try OCRJob.fromItem(baseItem(strategy: ["NULL": true], mechanism: ["NULL": true]))
        #expect(job.reocrStrategy == .plain)
        #expect(job.reocrMechanism == nil)
    }

    @Test func mechanismIsPassedThrough() throws {
        let job = try OCRJob.fromItem(
            baseItem(strategy: ["S": "invert"], mechanism: ["S": "low_contrast_histogram"])
        )
        #expect(job.reocrMechanism == "low_contrast_histogram")
    }

    @Test func toItemRoundTripsStrategyAndMechanism() throws {
        let original = try OCRJob.fromItem(
            baseItem(strategy: ["S": "upscale2x"], mechanism: ["S": "tiny_glyph_height"])
        )
        let roundTripped = try OCRJob.fromItem(original.toItem())
        #expect(roundTripped.reocrStrategy == .upscale2x)
        #expect(roundTripped.reocrMechanism == "tiny_glyph_height")
    }
}

#if os(macOS)
import CoreGraphics

/// End-to-end worker plumbing on synthetic images: the strategy stored on
/// the OCRJob must reach the right transform before Vision OCR, and the
/// uploaded result JSON must carry `reocr_strategy_applied`.
@Suite struct ReOCRWorkerRoutingTests {

    final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var sentMessages: [String] = []
        var deleted: [SQSDeleteEntry] = []
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws { deleted.append(contentsOf: entries) }
        func sendMessage(queueURL: String, body: String) async throws { sentMessages.append(body) }
    }

    final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:] // "bucket:key" -> Data
        var uploads: [(bucket: String, key: String, data: Data)] = []
        func getObject(bucket: String, key: String) async throws -> Data {
            objects["\(bucket):\(key)"] ?? Data()
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {
            let data = try Data(contentsOf: url)
            uploads.append((bucket, key, data))
        }
    }

    final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:] // "imageId:jobId"
        var stages: [String: String] = [:]
        var routing: [OCRRoutingDecision] = []
        var wordLabels: [ReceiptWordLabel] = []
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            jobs["\(imageId):\(jobId)"]!
        }
        func updateOCRJob(_ job: OCRJob) async throws { jobs["\(job.imageId):\(job.jobId)"] = job }
        func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {
            stages["\(imageId):\(jobId)"] = stage
        }
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws { routing.append(decision) }
        func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws { wordLabels.append(contentsOf: labels) }
        func addReceiptSections(
            imageId: String, receiptId: Int,
            sections: [ReceiptSectionPayload], createdAt: Date
        ) async throws {}
        func addReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws {}
        func addReceiptLineItemsIfWorkerOwned(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws -> Int { 0 }
    }

    /// Captures the image URLs the worker hands to Vision OCR so tests can
    /// inspect the (cropped + preprocessed) pixels.
    final class CapturingBox: @unchecked Sendable {
        var receivedImages: [URL] = []
    }

    struct CapturingOCREngine: OCREngineProtocol {
        let box: CapturingBox
        func process(images: [URL], outputDirectory: URL, includeClassification: Bool) throws -> [URL] {
            box.receivedImages.append(contentsOf: images)
            return try images.map { url in
                let out = outputDirectory.appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".json")
                try Data("{\"lines\": []}".utf8).write(to: out)
                return out
            }
        }
    }

    private func makeConfig() -> Config {
        Config(
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "tbl",
            region: "us-west-2",
            localstackEndpoint: nil,
            logLevel: "error",
            rawBucketName: "test-bucket"
        )
    }

    /// Run one REGIONAL_REOCR job through the worker with the given
    /// strategy over a dark 100x100 source image, full-image region.
    private func runRegionalJob(
        strategy: ReOCRStrategy
    ) async throws -> (engineImages: [URL], s3: S3Mock, dynamo: DynamoMock) {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        let box = CapturingBox()

        let imageId = "img-1"
        let jobId = "job-1"
        sqs.messages = [
            SQSMessage(
                messageId: "m1",
                receiptHandle: "rh1",
                body: "{\"image_id\":\"\(imageId)\",\"job_id\":\"\(jobId)\"}"
            )
        ]

        // Dark source image (reverse-video style) so `invert` is observable.
        let source = ReOCRTestImages.makeImage(width: 100, height: 100) { ctx in
            ctx.setFillColor(CGColor(red: 20.0 / 255, green: 20.0 / 255, blue: 20.0 / 255, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        }
        s3.objects["b:raw/img.png"] = ReOCRTestImages.pngData(from: source)

        let now = Date()
        dynamo.jobs["\(imageId):\(jobId)"] = OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: "b",
            s3Key: "raw/img.png",
            createdAt: now,
            updatedAt: now,
            status: .pending,
            jobType: .regionalReocr,
            receiptId: 1,
            reocrRegion: ReOCRRegion(x: 0, y: 0, width: 1, height: 1),
            reocrReason: "test",
            reocrStrategy: strategy,
            reocrMechanism: "unit_test"
        )

        let worker = OCRWorker(
            config: makeConfig(),
            ocr: CapturingOCREngine(box: box),
            sqs: sqs,
            s3: s3,
            dynamo: dynamo
        )
        let hadMessages = try await worker.processBatch()
        #expect(hadMessages)
        return (box.receivedImages, s3, dynamo)
    }

    private func loadImage(_ url: URL) throws -> CGImage {
        let data = try Data(contentsOf: url)
        let provider = CGDataProvider(data: data as CFData)!
        return CGImage(
            pngDataProviderSource: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        )!
    }

    private func uploadedResultJSON(_ s3: S3Mock) throws -> [String: Any] {
        let upload = try #require(s3.uploads.first { $0.key.hasPrefix("ocr_results/") })
        return try #require(
            JSONSerialization.jsonObject(with: upload.data) as? [String: Any]
        )
    }

    @Test func invertStrategyInvertsPixelsBeforeOCR() async throws {
        let (engineImages, s3, dynamo) = try await runRegionalJob(strategy: .invert)

        // The image handed to OCR must be the INVERTED crop: dark (20)
        // source pixels become light (~235).
        let ocrInput = try loadImage(try #require(engineImages.first))
        let pixel = ReOCRTestImages.pixel(in: ocrInput, x: 50, y: 50)
        #expect(pixel.r > 200 && pixel.g > 200 && pixel.b > 200)

        // The uploaded result JSON records what was applied, additively.
        let json = try uploadedResultJSON(s3)
        #expect(json["reocr_strategy_applied"] as? String == "invert")
        #expect(json["lines"] != nil)
        #expect(dynamo.jobs["img-1:job-1"]?.status == .completed)
    }

    @Test func upscaleStrategyDoublesOCRInputDimensions() async throws {
        let (engineImages, s3, _) = try await runRegionalJob(strategy: .upscale2x)
        let ocrInput = try loadImage(try #require(engineImages.first))
        #expect(ocrInput.width == 200)
        #expect(ocrInput.height == 200)
        let json = try uploadedResultJSON(s3)
        #expect(json["reocr_strategy_applied"] as? String == "upscale2x")
    }

    @Test func plainStrategyLeavesPixelsUntouchedButIsStillRecorded() async throws {
        let (engineImages, s3, _) = try await runRegionalJob(strategy: .plain)
        let ocrInput = try loadImage(try #require(engineImages.first))
        #expect(ocrInput.width == 100)
        #expect(ocrInput.height == 100)
        let pixel = ReOCRTestImages.pixel(in: ocrInput, x: 50, y: 50)
        #expect(pixel.r < 60 && pixel.g < 60 && pixel.b < 60)
        let json = try uploadedResultJSON(s3)
        #expect(json["reocr_strategy_applied"] as? String == "plain")
    }

    @Test func firstPassJobDoesNotRecordStrategy() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        let box = CapturingBox()

        sqs.messages = [
            SQSMessage(
                messageId: "m1",
                receiptHandle: "rh1",
                body: "{\"image_id\":\"img-2\",\"job_id\":\"job-2\"}"
            )
        ]
        s3.objects["b:raw/img2.png"] = ReOCRTestImages.pngData(
            from: ReOCRTestImages.makeImage(width: 10, height: 10) { ctx in
                ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
                ctx.fill(CGRect(x: 0, y: 0, width: 10, height: 10))
            }
        )
        let now = Date()
        dynamo.jobs["img-2:job-2"] = OCRJob(
            imageId: "img-2",
            jobId: "job-2",
            s3Bucket: "b",
            s3Key: "raw/img2.png",
            createdAt: now,
            updatedAt: now,
            status: .pending
        )

        let worker = OCRWorker(
            config: makeConfig(),
            ocr: CapturingOCREngine(box: box),
            sqs: sqs,
            s3: s3,
            dynamo: dynamo
        )
        _ = try await worker.processBatch()

        let upload = try #require(s3.uploads.first { $0.key.hasPrefix("ocr_results/") })
        let json = try #require(
            JSONSerialization.jsonObject(with: upload.data) as? [String: Any]
        )
        #expect(json["reocr_strategy_applied"] == nil)
    }
}
#endif
