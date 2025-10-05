import Foundation

public struct Config {
    public let ocrJobQueueURL: String
    public let ocrResultsQueueURL: String
    public let dynamoTableName: String
    public let region: String
    public let localstackEndpoint: URL?
    public let logLevel: String

    public init(
        ocrJobQueueURL: String,
        ocrResultsQueueURL: String,
        dynamoTableName: String,
        region: String,
        localstackEndpoint: URL?,
        logLevel: String
    ) {
        self.ocrJobQueueURL = ocrJobQueueURL
        self.ocrResultsQueueURL = ocrResultsQueueURL
        self.dynamoTableName = dynamoTableName
        self.region = region
        self.localstackEndpoint = localstackEndpoint
        self.logLevel = logLevel
    }
}

public enum ConfigError: Error, CustomStringConvertible {
    case missing(_ field: String)
    case invalid(_ message: String)

    public var description: String {
        switch self {
        case .missing(let field): return "Missing required config: \(field)"
        case .invalid(let message): return message
        }
    }
}

public protocol PulumiLoading {
    func loadOutputs(env: String) throws -> [String: Any]
}

public struct PulumiLoader: PulumiLoading {
    public init() {}

    public func loadOutputs(env: String) throws -> [String: Any] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            "pulumi", "stack", "output",
            "--stack", "tnorlund/portfolio/\(env)",
            "--json"
        ]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        try process.run()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            return [:]
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return obj
    }
}

public extension Config {
    static func load(
        env: String?,
        ocrJobQueueURL: String?,
        ocrResultsQueueURL: String?,
        dynamoTableName: String?,
        region: String,
        localstackEndpoint: String?,
        pulumi: PulumiLoading = PulumiLoader()
    ) throws -> Config {
        let endpointURL: URL? = localstackEndpoint.flatMap { URL(string: $0) }
        let logLevel = ProcessInfo.processInfo.environment["LOG_LEVEL"] ?? "info"

        func value(_ key: String, explicit: String?, from outputs: [String: Any]) -> String? {
            if let v = explicit, !v.isEmpty { return v }
            if let v = outputs[key] as? String, !v.isEmpty { return v }
            return nil
        }

        let outputs: [String: Any]
        if let env = env {
            outputs = (try? pulumi.loadOutputs(env: env)) ?? [:]
        } else {
            outputs = [:]
        }

        guard let ocrJobQueueURL = value("ocr_job_queue_url", explicit: ocrJobQueueURL, from: outputs) else {
            throw ConfigError.missing("ocr_job_queue_url")
        }
        guard let ocrResultsQueueURL = value("ocr_results_queue_url", explicit: ocrResultsQueueURL, from: outputs) else {
            throw ConfigError.missing("ocr_results_queue_url")
        }
        guard let dynamoTableName = value("dynamodb_table_name", explicit: dynamoTableName, from: outputs) else {
            throw ConfigError.missing("dynamodb_table_name")
        }

        return Config(
            ocrJobQueueURL: ocrJobQueueURL,
            ocrResultsQueueURL: ocrResultsQueueURL,
            dynamoTableName: dynamoTableName,
            region: region,
            localstackEndpoint: endpointURL,
            logLevel: logLevel
        )
    }
}


