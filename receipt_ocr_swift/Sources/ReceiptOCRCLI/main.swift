import ArgumentParser
import Foundation
import ReceiptOCRCore

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

    mutating func run() async throws {
        let config = try Config.load(
            env: env,
            ocrJobQueueURL: ocrJobQueueURL,
            ocrResultsQueueURL: ocrResultsQueueURL,
            dynamoTableName: dynamoTableName,
            region: region,
            localstackEndpoint: localstackEndpoint
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
                logLevel: ll
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
            let engine: OCREngineProtocol = stubOCR ? StubOCREngine() : VisionOCREngine()
            #else
            let engine: OCREngineProtocol = StubOCREngine()
            #endif
            _ = try engine.process(images: [imageURL], outputDirectory: outURL)
        } else {
            let worker = try OCRWorker.make(config: effectiveConfig, stubOCR: stubOCR)
            if continuous {
                print("Running continuously until queue is empty...")
                var batchCount = 0
                while true {
                    batchCount += 1
                    print("Processing batch \(batchCount)...")
                    let hadMessages = try await worker.processBatch()
                    if !hadMessages {
                        print("Queue is empty, stopping.")
                        break
                    }
                    // Small delay between batches to avoid tight loops
                    try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                }
            } else {
                _ = try await worker.processBatch()
            }
        }
    }
}


