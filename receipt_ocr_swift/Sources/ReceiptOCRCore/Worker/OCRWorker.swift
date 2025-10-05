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
                receiptCount: 0,
                status: .pending
            )
            logger.debug("routing_decision_add image_id=\(ctx.imageId) job_id=\(ctx.jobId) key=\(resultKey)")
            try await Retry.withBackoff { try await self.dynamo.addOCRRoutingDecision(decision) }

            let body: [String: Any] = [
                "image_id": ctx.imageId,
                "job_id": ctx.jobId,
                "s3_key": resultKey,
                "s3_bucket": ctx.s3Bucket
            ]
            let bodyData = try JSONSerialization.data(withJSONObject: body)
            let bodyString = String(data: bodyData, encoding: .utf8)!
            logger.debug("sqs_send_result queue=\(config.ocrResultsQueueURL) size=\(bodyString.count)")
            try await Retry.withBackoff { try await self.sqs.sendMessage(queueURL: self.config.ocrResultsQueueURL, body: bodyString) }

            var updatedJob = try await Retry.withBackoff { try await self.dynamo.getOCRJob(imageId: ctx.imageId, jobId: ctx.jobId) }
            updatedJob.updatedAt = now
            updatedJob.status = .completed
            try await Retry.withBackoff { try await self.dynamo.updateOCRJob(updatedJob) }
            logger.info("job_complete image_id=\(ctx.imageId) job_id=\(ctx.jobId)")
        }

        // Delete processed messages
        let entries = contexts.map { SQSDeleteEntry(id: $0.message.messageId, receiptHandle: $0.message.receiptHandle) }
        logger.info("sqs_delete_batch count=\(entries.count)")
        try await Retry.withBackoff { try await self.sqs.deleteMessages(queueURL: self.config.ocrJobQueueURL, entries: entries) }
        
        return true
    }
}


