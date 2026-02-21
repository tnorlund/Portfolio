import Foundation
import Logging
#if os(macOS)
import AppKit
import CoreGraphics
#endif

public protocol OCREngineProtocol {
    func process(images: [URL], outputDirectory: URL) throws -> [URL]
    func processParallel(images: [URL], outputDirectory: URL, maxConcurrency: Int) async throws -> [URL]
}

// Default implementation for processParallel that falls back to sequential
extension OCREngineProtocol {
    public func processParallel(images: [URL], outputDirectory: URL, maxConcurrency: Int) async throws -> [URL] {
        // Default: fall back to sequential processing
        return try process(images: images, outputDirectory: outputDirectory)
    }
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
    let layoutLMPredictions: [ParsedLinePrediction]?
}

/// Parsed LayoutLM prediction for a line
private struct ParsedLinePrediction {
    let tokens: [String]
    let labels: [String]
    let confidences: [Float]
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

        // Parse LayoutLM predictions if present
        var layoutLMPredictions: [ParsedLinePrediction]? = nil
        if let predictionsArray = receiptDict["layoutlm_predictions"] as? [[String: Any]] {
            layoutLMPredictions = predictionsArray.compactMap { predDict -> ParsedLinePrediction? in
                guard let tokens = predDict["tokens"] as? [String],
                      let labels = predDict["labels"] as? [String],
                      let confidences = predDict["confidences"] as? [Double] else {
                    return nil
                }
                return ParsedLinePrediction(
                    tokens: tokens,
                    labels: labels,
                    confidences: confidences.map { Float($0) }
                )
            }
        }

        return ParsedReceiptInfo(
            clusterId: clusterId,
            localFileName: s3Key,  // s3Key initially contains local filename
            bounds: bounds,
            warpedWidth: warpedWidth,
            warpedHeight: warpedHeight,
            lineIndices: lineIndices,
            layoutLMPredictions: layoutLMPredictions
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
        logger.logLevel = .from(string: config.logLevel)
        self.logger = logger
    }

    public static func make(config: Config, stubOCR: Bool) async throws -> OCRWorker {
        let factory = SotoAWSFactory(config: config)
        let s3Client = SotoS3Client(s3: factory.makeS3())

        // Set up logger for model operations
        var modelLogger = Logger(label: "receipt.ocr.model")
        modelLogger.logLevel = .from(string: config.logLevel)

        // Download LayoutLM model if configured
        var layoutLMBundlePath: URL? = nil
        #if os(macOS)
        if let bucket = config.layoutLMModelS3Bucket,
           let key = config.layoutLMModelS3Key,
           !bucket.isEmpty, !key.isEmpty {
            modelLogger.info("layoutlm_download_start bucket=\(bucket) key=\(key)")
            let downloader = ModelDownloader(s3: s3Client, logger: modelLogger)
            layoutLMBundlePath = try await downloader.ensureModelDownloaded(
                bucket: bucket,
                key: key,
                localCachePath: config.layoutLMLocalCachePath
            )
            modelLogger.info("layoutlm_download_complete path=\(layoutLMBundlePath?.path ?? "nil")")
        } else {
            // Log why LayoutLM is disabled
            let bucketStatus = config.layoutLMModelS3Bucket.map { $0.isEmpty ? "empty" : "set" } ?? "nil"
            let keyStatus = config.layoutLMModelS3Key.map { $0.isEmpty ? "empty" : "set" } ?? "nil"
            modelLogger.info("layoutlm_skipped bucket=\(bucketStatus) key=\(keyStatus) reason=missing_config")
        }
        #else
        modelLogger.info("layoutlm_skipped reason=not_macos")
        #endif

        #if os(macOS)
        let engine: OCREngineProtocol = stubOCR ? StubOCREngine() : VisionOCREngine(layoutLMBundlePath: layoutLMBundlePath)
        #else
        let engine: OCREngineProtocol = StubOCREngine()
        #endif

        let worker = OCRWorker(
            config: config,
            ocr: engine,
            sqs: SotoSQSClient(sqs: factory.makeSQS()),
            s3: s3Client,
            dynamo: SotoDynamoClient(dynamo: factory.makeDynamo(), tableName: config.dynamoTableName),
            sotoFactory: factory
        )
        return worker
    }

    #if os(macOS)
    private func cropImageData(_ imageData: Data, region: ReOCRRegion) throws -> Data {
        // The region already includes horizontal padding (applied in
        // _compute_reocr_region on the Python side). Use it directly so
        // crop and overlay coordinate mapping are identical.
        guard let nsImage = NSImage(data: imageData),
              let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            throw DynamoMapError.invalid("regional_reocr_image_decode")
        }
        let imageWidth = CGFloat(cgImage.width)
        let imageHeight = CGFloat(cgImage.height)

        let cropX = max(0, min(imageWidth - 1, CGFloat(region.x) * imageWidth))
        let cropWidth = max(1, min(imageWidth - cropX, CGFloat(region.width) * imageWidth))
        // Vision/OCR coordinates are normalized with bottom-left origin; convert to CGImage's top-left origin.
        let cropYFromTop = (1.0 - CGFloat(region.y + region.height)) * imageHeight
        let cropY = max(0, min(imageHeight - 1, cropYFromTop))
        let cropHeight = max(1, min(imageHeight - cropY, CGFloat(region.height) * imageHeight))
        let cropRect = CGRect(x: cropX, y: cropY, width: cropWidth, height: cropHeight).integral

        guard let croppedCG = cgImage.cropping(to: cropRect) else {
            throw DynamoMapError.invalid("regional_reocr_crop_failed")
        }
        let bitmapRep = NSBitmapImageRep(cgImage: croppedCG)
        guard let pngData = bitmapRep.representation(using: .png, properties: [:]) else {
            throw DynamoMapError.invalid("regional_reocr_png_encode_failed")
        }
        return pngData
    }
    #endif

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

        struct Context {
            let message: SQSMessage
            let imageId: String
            let jobId: String
            let s3Bucket: String
            let jobType: OCRJobType
        }
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
            // Update processing stage to DOWNLOADING
            do {
                try await self.dynamo.updateOCRJobStage(imageId: imageId, jobId: jobId, stage: "DOWNLOADING")
            } catch {
                logger.debug("stage_update_failed image_id=\(imageId) stage=DOWNLOADING error=\(error)")
            }
            logger.debug("download_image bucket=\(job.s3Bucket) key=\(job.s3Key)")
            // Download image
            let imageData = try await Retry.withBackoff { try await self.s3.getObject(bucket: job.s3Bucket, key: job.s3Key) }
            let baseName = (job.s3Key as NSString).lastPathComponent
            let name = baseName.isEmpty ? "\(imageId)" : (baseName as NSString).deletingPathExtension
            let ext = (baseName as NSString).pathExtension
            var localName = ext.isEmpty ? "\(name)-\(jobId)" : "\(name)-\(jobId).\(ext)"
            let localURL = tempDir.appendingPathComponent(localName)
            if job.jobType == .regionalReocr, let region = job.reocrRegion {
                #if os(macOS)
                do {
                    let cropped = try cropImageData(imageData, region: region)
                    localName = "\(name)-\(jobId)-reocr.png"
                    let croppedURL = tempDir.appendingPathComponent(localName)
                    try cropped.write(to: croppedURL)
                    imageURLs.append(croppedURL)
                    contexts.append(
                        Context(
                            message: msg,
                            imageId: imageId,
                            jobId: jobId,
                            s3Bucket: job.s3Bucket,
                            jobType: job.jobType
                        )
                    )
                    logger.info(
                        "regional_reocr_crop_complete image_id=\(imageId) job_id=\(jobId) x=\(region.x) y=\(region.y) width=\(region.width) height=\(region.height)"
                    )
                    continue
                } catch {
                    logger.warning("regional_reocr_crop_failed image_id=\(imageId) job_id=\(jobId) error=\(error)")
                }
                #else
                logger.warning("regional_reocr_crop_skipped_non_macos image_id=\(imageId) job_id=\(jobId)")
                #endif
            }
            try imageData.write(to: localURL)

            imageURLs.append(localURL)
            contexts.append(
                Context(
                    message: msg,
                    imageId: imageId,
                    jobId: jobId,
                    s3Bucket: job.s3Bucket,
                    jobType: job.jobType
                )
            )
        }

        // Update all jobs to OCR_RUNNING stage
        for ctx in contexts {
            do {
                try await dynamo.updateOCRJobStage(imageId: ctx.imageId, jobId: ctx.jobId, stage: "OCR_RUNNING")
            } catch {
                logger.debug("stage_update_failed image_id=\(ctx.imageId) stage=OCR_RUNNING error=\(error)")
            }
        }

        // Run OCR engine with parallel processing (uses CPU count)
        let concurrency = ProcessInfo.processInfo.activeProcessorCount
        logger.info("ocr_run count=\(imageURLs.count) out_dir=\(tempDir.path) parallel=true concurrency=\(concurrency)")
        let ocrResults = try await ocr.processParallel(images: imageURLs, outputDirectory: tempDir, maxConcurrency: concurrency)

        // Upload results, write routing decision, send result message, update job
        let now = Date()
        for (resultURL, ctx) in zip(ocrResults, contexts) {
            // Update processing stage to UPLOADING_RESULTS
            do {
                try await dynamo.updateOCRJobStage(imageId: ctx.imageId, jobId: ctx.jobId, stage: "UPLOADING_RESULTS")
            } catch {
                logger.debug("stage_update_failed image_id=\(ctx.imageId) stage=UPLOADING_RESULTS error=\(error)")
            }

            // Parse the OCR result JSON to get receipt info.
            // Regional re-OCR jobs should not upload warped receipt images.
            let jsonData = try Data(contentsOf: resultURL)
            let receipts: [ParsedReceiptInfo]
            if ctx.jobType == .regionalReocr {
                receipts = []
                logger.debug("regional_reocr_skip_receipt_upload image_id=\(ctx.imageId) job_id=\(ctx.jobId)")
            } else {
                receipts = parseReceiptsFromJSON(jsonData)
            }

            // Upload receipt images to S3 concurrently
            let uploadedReceiptKeys = await withTaskGroup(of: String?.self) { group in
                for receipt in receipts {
                    let receiptLocalURL = tempDir.appendingPathComponent(receipt.localFileName)
                    let receiptS3Key = "receipts/\(ctx.imageId)/\(receipt.localFileName)"

                    group.addTask {
                        guard FileManager.default.fileExists(atPath: receiptLocalURL.path) else {
                            self.logger.warning("receipt_file_missing path=\(receiptLocalURL.path)")
                            return nil
                        }
                        self.logger.debug("upload_receipt bucket=\(ctx.s3Bucket) key=\(receiptS3Key)")
                        do {
                            try await Retry.withBackoff { try await self.s3.uploadFile(url: receiptLocalURL, bucket: ctx.s3Bucket, key: receiptS3Key) }
                            return receiptS3Key
                        } catch {
                            self.logger.warning("failed_upload_receipt key=\(receiptS3Key) error=\(error)")
                            return nil
                        }
                    }
                }
                // Collect successful uploads
                var keys: [String] = []
                for await key in group {
                    if let k = key { keys.append(k) }
                }
                return keys
            }
            logger.info("receipts_uploaded count=\(uploadedReceiptKeys.count) image_id=\(ctx.imageId)")

            // Upload LayoutLM predicted labels to DynamoDB as PENDING (concurrently)
            #if os(macOS)
            await withTaskGroup(of: Void.self) { group in
                guard ctx.jobType != .regionalReocr else {
                    return
                }
                for receipt in receipts {
                    guard let predictions = receipt.layoutLMPredictions, !predictions.isEmpty else { continue }
                    let receiptId = receipt.clusterId
                    let imageId = ctx.imageId

                    group.addTask {
                        // Convert parsed predictions to LinePrediction format for ReceiptWordLabel
                        let linePredictions = predictions.map { pred in
                            LinePrediction(
                                tokens: pred.tokens,
                                labels: pred.labels,
                                confidences: pred.confidences,
                                allProbabilities: nil
                            )
                        }

                        let labels = ReceiptWordLabel.fromLinePredictions(
                            predictions: linePredictions,
                            imageId: imageId,
                            receiptId: receiptId
                        )

                        guard !labels.isEmpty else { return }
                        self.logger.info("upload_labels image_id=\(imageId) receipt_id=\(receiptId) count=\(labels.count)")
                        do {
                            try await Retry.withBackoff {
                                try await self.dynamo.addReceiptWordLabels(labels)
                            }
                        } catch {
                            self.logger.warning("failed_upload_labels image_id=\(imageId) receipt_id=\(receiptId) error=\(error)")
                        }
                    }
                }
            }
            #endif

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
