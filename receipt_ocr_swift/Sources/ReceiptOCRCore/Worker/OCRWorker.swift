import Foundation
import Logging

public protocol OCREngineProtocol {
    func process(images: [URL], outputDirectory: URL) throws -> [URL]
}

public struct StubOCREngine: OCREngineProtocol {
    public init() {}
    public func process(images: [URL], outputDirectory: URL) throws -> [URL] {
        return try images.map { url in
            let out = outputDirectory.appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".json")
            try Data("{\"lines\": []}".utf8).write(to: out)
            return out
        }
    }
}

// MARK: - OCR Result Parsing

/// Parsed receipt info from OCR JSON output
private struct ParsedReceiptInfo {
    let clusterId: Int
    let localFileName: String
    let bounds: ReceiptBoundsInfo
    let warpedWidth: Int
    let warpedHeight: Int
    let lineIndices: [Int]
}

/// Receipt bounds from JSON
private struct ReceiptBoundsInfo {
    let topLeft: (x: Double, y: Double)
    let topRight: (x: Double, y: Double)
    let bottomRight: (x: Double, y: Double)
    let bottomLeft: (x: Double, y: Double)
}

/// Parse receipts from OCR JSON output
private func parseReceiptsFromJSON(_ jsonData: Data) -> [ParsedReceiptInfo] {
    guard let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
          let receiptsArray = json["receipts"] as? [[String: Any]] else {
        return []
    }

    return receiptsArray.compactMap { receiptDict -> ParsedReceiptInfo? in
        guard let clusterId = receiptDict["cluster_id"] as? Int,
              let s3Key = receiptDict["s3_key"] as? String,
              let warpedWidth = receiptDict["warped_width"] as? Int,
              let warpedHeight = receiptDict["warped_height"] as? Int,
              let lineIndices = receiptDict["line_indices"] as? [Int],
              let boundsDict = receiptDict["bounds"] as? [String: Any],
              let topLeft = boundsDict["top_left"] as? [String: Double],
              let topRight = boundsDict["top_right"] as? [String: Double],
              let bottomRight = boundsDict["bottom_right"] as? [String: Double],
              let bottomLeft = boundsDict["bottom_left"] as? [String: Double] else {
            return nil
        }

        // Validate all coordinate values exist - skip malformed receipts
        guard let topLeftX = topLeft["x"], let topLeftY = topLeft["y"],
              let topRightX = topRight["x"], let topRightY = topRight["y"],
              let bottomRightX = bottomRight["x"], let bottomRightY = bottomRight["y"],
              let bottomLeftX = bottomLeft["x"], let bottomLeftY = bottomLeft["y"] else {
            return nil
        }

        let bounds = ReceiptBoundsInfo(
            topLeft: (x: topLeftX, y: topLeftY),
            topRight: (x: topRightX, y: topRightY),
            bottomRight: (x: bottomRightX, y: bottomRightY),
            bottomLeft: (x: bottomLeftX, y: bottomLeftY)
        )

        return ParsedReceiptInfo(
            clusterId: clusterId,
            localFileName: s3Key,  // s3Key initially contains local filename
            bounds: bounds,
            warpedWidth: warpedWidth,
            warpedHeight: warpedHeight,
            lineIndices: lineIndices
        )
    }
}

public final class OCRWorker {
    private let config: Config
    private let ocr: OCREngineProtocol
    private let logger: Logger
    private let sqs: SQSClientProtocol
    private let s3: S3ClientProtocol
    private let dynamo: DynamoClientProtocol
    // Hold factory to manage AWSClient lifecycle when using Soto-backed clients
    private let sotoFactory: SotoAWSFactory?

    public init(
        config: Config,
        ocr: OCREngineProtocol,
        sqs: SQSClientProtocol,
        s3: S3ClientProtocol,
        dynamo: DynamoClientProtocol,
        sotoFactory: SotoAWSFactory? = nil
    ) {
        self.config = config
        self.ocr = ocr
        self.sqs = sqs
        self.s3 = s3
        self.dynamo = dynamo
        self.sotoFactory = sotoFactory
        var logger = Logger(label: "receipt.ocr.worker")
        switch config.logLevel.lowercased() {
        case "trace": logger.logLevel = .trace
        case "debug": logger.logLevel = .debug
        case "warn": logger.logLevel = .warning
        case "error": logger.logLevel = .error
        default: logger.logLevel = .info
        }
        self.logger = logger
    }

    public static func make(config: Config, stubOCR: Bool) throws -> OCRWorker {
        #if os(macOS)
        let engine: OCREngineProtocol = stubOCR ? StubOCREngine() : VisionOCREngine()
        #else
        let engine: OCREngineProtocol = StubOCREngine()
        #endif
        let factory = SotoAWSFactory(config: config)
        let worker = OCRWorker(
            config: config,
            ocr: engine,
            sqs: SotoSQSClient(sqs: factory.makeSQS()),
            s3: SotoS3Client(s3: factory.makeS3()),
            dynamo: SotoDynamoClient(dynamo: factory.makeDynamo(), tableName: config.dynamoTableName),
            sotoFactory: factory
        )
        return worker
    }

    public func processBatch() async throws -> Bool {
        logger.info("sqs_receive_start max=10 visibility=60 queue=\(config.ocrJobQueueURL)")
        let messages = try await Retry.withBackoff {
            try await self.sqs.receiveMessages(
                queueURL: self.config.ocrJobQueueURL,
                maxNumber: 10,
                visibilityTimeout: 60
            )
        }
        logger.info("sqs_receive_complete count=\(messages.count)")
        if messages.isEmpty { return false }

        let fileManager = FileManager.default
        let tempDir = fileManager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: tempDir, withIntermediateDirectories: true)

        struct Context { let message: SQSMessage; let imageId: String; let jobId: String; let s3Bucket: String }
        var imageURLs: [URL] = []
        var contexts: [Context] = []

        for msg in messages {
            guard let data = msg.body.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let imageId = obj["image_id"] as? String,
                  let jobId = obj["job_id"] as? String
            else { continue }
            logger.info("job_start image_id=\(imageId) job_id=\(jobId)")
            let job = try await Retry.withBackoff { try await self.dynamo.getOCRJob(imageId: imageId, jobId: jobId) }
            logger.debug("download_image bucket=\(job.s3Bucket) key=\(job.s3Key)")
            // Download image
            let imageData = try await Retry.withBackoff { try await self.s3.getObject(bucket: job.s3Bucket, key: job.s3Key) }
            let baseName = (job.s3Key as NSString).lastPathComponent
            let name = baseName.isEmpty ? "\(imageId)" : (baseName as NSString).deletingPathExtension
            let ext = (baseName as NSString).pathExtension
            let localName = ext.isEmpty ? "\(name)-\(jobId)" : "\(name)-\(jobId).\(ext)"
            let localURL = tempDir.appendingPathComponent(localName)
            try imageData.write(to: localURL)

            imageURLs.append(localURL)
            contexts.append(Context(message: msg, imageId: imageId, jobId: jobId, s3Bucket: job.s3Bucket))
        }

        // Run OCR engine
        logger.info("ocr_run count=\(imageURLs.count) out_dir=\(tempDir.path)")
        let ocrResults = try ocr.process(images: imageURLs, outputDirectory: tempDir)

        // Upload results, write routing decision, send result message, update job
        let now = Date()
        for (resultURL, ctx) in zip(ocrResults, contexts) {
            // Parse the OCR result JSON to get receipt info
            let jsonData = try Data(contentsOf: resultURL)
            let receipts = parseReceiptsFromJSON(jsonData)

            // Upload receipt images to S3
            var uploadedReceiptKeys: [String] = []
            for receipt in receipts {
                let receiptLocalURL = tempDir.appendingPathComponent(receipt.localFileName)
                let receiptS3Key = "receipts/\(ctx.imageId)/\(receipt.localFileName)"

                if FileManager.default.fileExists(atPath: receiptLocalURL.path) {
                    logger.debug("upload_receipt bucket=\(ctx.s3Bucket) key=\(receiptS3Key)")
                    do {
                        try await Retry.withBackoff { try await self.s3.uploadFile(url: receiptLocalURL, bucket: ctx.s3Bucket, key: receiptS3Key) }
                        uploadedReceiptKeys.append(receiptS3Key)
                    } catch {
                        logger.warning("failed_upload_receipt key=\(receiptS3Key) error=\(error)")
                    }
                } else {
                    logger.warning("receipt_file_missing path=\(receiptLocalURL.path)")
                }
            }
            logger.info("receipts_uploaded count=\(uploadedReceiptKeys.count) image_id=\(ctx.imageId)")

            // Upload OCR result JSON
            let resultKey = "ocr_results/\(resultURL.lastPathComponent)"
            logger.debug("upload_result bucket=\(ctx.s3Bucket) key=\(resultKey)")
            try await Retry.withBackoff { try await self.s3.uploadFile(url: resultURL, bucket: ctx.s3Bucket, key: resultKey) }

            let decision = OCRRoutingDecision(
                imageId: ctx.imageId,
                jobId: ctx.jobId,
                s3Bucket: ctx.s3Bucket,
                s3Key: resultKey,
                createdAt: now,
                updatedAt: now,
                receiptCount: receipts.count,
                status: .pending
            )
            logger.debug("routing_decision_add image_id=\(ctx.imageId) job_id=\(ctx.jobId) key=\(resultKey) receiptCount=\(receipts.count)")
            try await Retry.withBackoff { try await self.dynamo.addOCRRoutingDecision(decision) }

            let body: [String: Any] = [
                "image_id": ctx.imageId,
                "job_id": ctx.jobId,
                "s3_key": resultKey,
                "s3_bucket": ctx.s3Bucket,
                "receipt_count": receipts.count
            ]
            let bodyData = try JSONSerialization.data(withJSONObject: body)
            let bodyString = String(data: bodyData, encoding: .utf8)!
            logger.debug("sqs_send_result queue=\(config.ocrResultsQueueURL) size=\(bodyString.count)")
            try await Retry.withBackoff { try await self.sqs.sendMessage(queueURL: self.config.ocrResultsQueueURL, body: bodyString) }

            var updatedJob = try await Retry.withBackoff { try await self.dynamo.getOCRJob(imageId: ctx.imageId, jobId: ctx.jobId) }
            updatedJob.updatedAt = now
            updatedJob.status = .completed
            try await Retry.withBackoff { try await self.dynamo.updateOCRJob(updatedJob) }
            logger.info("job_complete image_id=\(ctx.imageId) job_id=\(ctx.jobId) receipts=\(receipts.count)")
        }

        // Delete processed messages
        let entries = contexts.map { SQSDeleteEntry(id: $0.message.messageId, receiptHandle: $0.message.receiptHandle) }
        logger.info("sqs_delete_batch count=\(entries.count)")
        try await Retry.withBackoff { try await self.sqs.deleteMessages(queueURL: self.config.ocrJobQueueURL, entries: entries) }
        
        return true
    }
}


