import ArgumentParser
import Foundation
import Logging
import ReceiptOCRCore

/// Purge the CoreML E5RT execution-plan cache that grows unboundedly with
/// every `MLModel.prediction()` call.  The cache lives under
/// `~/Library/Caches/<bundle>/com.apple.e5rt.e5bundlecache/` and Apple
/// provides no API to cap its size, so we delete it periodically.
private func purgeE5RTCache(logger: Logger) {
    let fm = FileManager.default
    // The cache root depends on the process bundle identifier.
    // For a CLI tool without a bundle ID, CoreML uses the executable name.
    let cacheBase = fm.urls(for: .cachesDirectory, in: .userDomainMask).first
    guard let cacheBase else { return }

    let candidates = [
        cacheBase.appendingPathComponent("receipt-ocr/com.apple.e5rt.e5bundlecache"),
        cacheBase.appendingPathComponent("com.apple.e5rt.e5bundlecache"),
    ]

    for dir in candidates {
        guard fm.fileExists(atPath: dir.path) else { continue }
        do {
            try fm.removeItem(at: dir)
            logger.info("purged_e5rt_cache path=\(dir.path)")
        } catch {
            logger.warning("purge_e5rt_cache_failed path=\(dir.path) error=\(error)")
        }
    }
}

@main
struct ReceiptOCR: AsyncParsableCommand {
    @Option(name: .long) var env: String?
    @Option(name: .long) var ocrJobQueueURL: String?
    @Option(name: .long) var ocrResultsQueueURL: String?
    @Option(name: .long) var dynamoTableName: String?
    @Option(name: .long, help: "AWS region, default us-east-1") var region: String = "us-east-1"
    @Flag(name: .long, help: "Use a stub OCR engine instead of Vision") var stubOCR: Bool = false
    @Option(name: .long, help: "LocalStack endpoint, e.g. http://localhost:4566") var localstackEndpoint: String?
    @Option(name: .long, help: "Log level: trace|debug|info|warn|error") var logLevel: String?
    @Option(name: .long, help: "Process a local image file instead of AWS flow") var processLocalImage: String?
    @Option(name: .long, help: "Output directory for local processing JSON") var outputDir: String?
    @Flag(name: .long, help: "Run continuously until queue is empty") var continuous: Bool = false
    @Option(name: .long, help: "Path to CoreML LayoutLM model bundle for local processing") var layoutlmModel: String?
    @Option(name: .long, help: "S3 bucket containing LayoutLM model bundle for worker mode") var layoutlmModelBucket: String?
    @Option(name: .long, help: "S3 key (path) to LayoutLM model bundle zip for worker mode") var layoutlmModelKey: String?
    @Option(name: .long, help: "Local cache path for downloaded model (default: .models/layoutlm)") var layoutlmCachePath: String?

    mutating func run() async throws {
        let config = try Config.load(
            env: env,
            ocrJobQueueURL: ocrJobQueueURL,
            ocrResultsQueueURL: ocrResultsQueueURL,
            dynamoTableName: dynamoTableName,
            region: region,
            localstackEndpoint: localstackEndpoint,
            layoutLMModelS3Bucket: layoutlmModelBucket,
            layoutLMModelS3Key: layoutlmModelKey,
            layoutLMLocalCachePath: layoutlmCachePath
        )
        // Override log level from flag if provided
        let effectiveConfig: Config
        if let ll = logLevel, !ll.isEmpty {
            effectiveConfig = Config(
                ocrJobQueueURL: config.ocrJobQueueURL,
                ocrResultsQueueURL: config.ocrResultsQueueURL,
                dynamoTableName: config.dynamoTableName,
                region: config.region,
                localstackEndpoint: config.localstackEndpoint,
                logLevel: ll,
                layoutLMModelS3Bucket: config.layoutLMModelS3Bucket,
                layoutLMModelS3Key: config.layoutLMModelS3Key,
                layoutLMLocalCachePath: config.layoutLMLocalCachePath
            )
        } else {
            effectiveConfig = config
        }
        if let imagePath = processLocalImage {
            guard let outputDir = outputDir else {
                throw ValidationError("--output-dir is required when using --process-local-image")
            }
            let imageURL = URL(fileURLWithPath: imagePath)
            let outURL = URL(fileURLWithPath: outputDir, isDirectory: true)
            #if os(macOS)
            let layoutlmBundlePath = layoutlmModel.map { URL(fileURLWithPath: $0) }
            let engine: OCREngineProtocol = stubOCR ? StubOCREngine() : VisionOCREngine(layoutLMBundlePath: layoutlmBundlePath)
            #else
            let engine: OCREngineProtocol = StubOCREngine()
            #endif
            _ = try engine.process(images: [imageURL], outputDirectory: outURL)
        } else {
            var cacheLogger = Logger(label: "receipt.ocr.cache")
            cacheLogger.logLevel = .from(string: effectiveConfig.logLevel)

            let worker = try await OCRWorker.make(config: effectiveConfig, stubOCR: stubOCR)
            if continuous {
                print("Running continuously until queue is empty...")
                var batchCount = 0
                // Purge every 50 batches (~500 receipts) to keep cache bounded
                let purgeCadence = 50
                while true {
                    batchCount += 1
                    print("Processing batch \(batchCount)...")
                    let hadMessages = try await worker.processBatch()
                    if !hadMessages {
                        print("Queue is empty, stopping.")
                        break
                    }
                    if batchCount % purgeCadence == 0 {
                        purgeE5RTCache(logger: cacheLogger)
                    }
                    // Small delay between batches to avoid tight loops
                    try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                }
                // Final purge before exit
                purgeE5RTCache(logger: cacheLogger)
            } else {
                _ = try await worker.processBatch()
                purgeE5RTCache(logger: cacheLogger)
            }
        }
    }
}


