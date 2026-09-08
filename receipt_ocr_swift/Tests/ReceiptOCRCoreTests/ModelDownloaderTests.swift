import Foundation
import Logging
import XCTest
@testable import ReceiptOCRCore

#if os(macOS)
final class ModelDownloaderTests: XCTestCase {
    final class FakeS3: S3ClientProtocol {
        var pointer: Data?
        var head: S3ObjectHead?
        var bundles: [String: Data] = [:]
        var downloads: [String] = []
        var pointerReads = 0

        func getObject(bucket: String, key: String) async throws -> Data {
            downloads.append(key)
            guard let data = bundles[key] else { throw ObjectNotFoundError(bucket: bucket, key: key) }
            return data
        }
        func getObjectIfExists(bucket: String, key: String) async throws -> Data? {
            XCTAssertEqual(bucket, "test-dev")
            XCTAssertEqual(key, "coreml/active.json")
            pointerReads += 1
            return pointer
        }
        func headObject(bucket: String, key: String) async throws -> S3ObjectHead? { head }
        func uploadFile(url: URL, bucket: String, key: String) async throws { XCTFail("Unexpected upload") }
    }

    final class LogStore: @unchecked Sendable {
        private let lock = NSLock()
        private var lines: [String] = []
        func append(_ line: String) { lock.lock(); defer { lock.unlock() }; lines.append(line) }
        var messages: [String] { lock.lock(); defer { lock.unlock() }; return lines }
    }

    struct CaptureLog: LogHandler {
        var metadata: Logger.Metadata = [:]
        var logLevel: Logger.Level = .info
        let store: LogStore
        subscript(metadataKey key: String) -> Logger.Metadata.Value? {
            get { metadata[key] }
            set { metadata[key] = newValue }
        }
        func log(level: Logger.Level, message: Logger.Message, metadata: Logger.Metadata?, source: String, file: String, function: String, line: UInt) {
            store.append(message.description)
        }
    }

    var root: URL!
    var s3: FakeS3!
    var logs: LogStore!
    var downloader: ModelDownloader!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        s3 = FakeS3()
        logs = LogStore()
        let store = logs!
        downloader = ModelDownloader(s3: s3, logger: Logger(label: "test", factory: { _ in CaptureLog(store: store) }))
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func bundle(exportID: String = "export-1", identity: Bool = true) throws -> Data {
        let source = root.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: source.appendingPathComponent("LayoutLM.mlpackage"), withIntermediateDirectories: true)
        try Data("vocab".utf8).write(to: source.appendingPathComponent("vocab.txt"))
        try Data("{}".utf8).write(to: source.appendingPathComponent("config.json"))
        if identity {
            let data = try JSONSerialization.data(withJSONObject: ["export_id": exportID, "training_job_id": "job-1"])
            try data.write(to: source.appendingPathComponent("model_identity.json"))
        }
        let zip = root.appendingPathComponent("\(UUID().uuidString).zip")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/zip")
        process.arguments = ["-q", "-r", zip.path, "."]
        process.currentDirectoryURL = source
        try process.run()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        return try Data(contentsOf: zip)
    }

    var cache: URL { root.appendingPathComponent("cache") }

    func resolve() async throws -> URL {
        try await downloader.ensureModelDownloaded(bucket: "test-dev", key: "alias.zip", localCachePath: cache.path, pointerKey: "coreml/active.json", env: "dev")
    }

    func setPointer(exportID: String = "export-1", data: Data) throws {
        let key = "coreml/versions/\(exportID)/layoutlm-coreml-bundle.zip"
        s3.bundles[key] = data
        s3.pointer = try JSONSerialization.data(withJSONObject: [
            "schema_version": 1, "export_id": exportID, "training_job_id": "job-1",
            "training_job_name": "training-1", "bundle_key": key,
            "bundle_etag": "\"etag\"", "bundle_size_bytes": data.count,
            "promoted_at": "2026-09-05T17:35:08+00:00", "promoted_by": "set_active_model",
        ])
    }

    func testPointerDownloadsAndReusesValidatedVersion() async throws {
        try setPointer(data: bundle())
        let first = try await resolve()
        XCTAssertEqual(first, cache.appendingPathComponent("export-1", isDirectory: true))
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.appendingPathComponent("model_identity.json").path))
        let second = try await resolve()
        XCTAssertEqual(second, first)
        XCTAssertEqual(s3.downloads, ["coreml/versions/export-1/layoutlm-coreml-bundle.zip"])
        XCTAssertEqual(s3.pointerReads, 2)
        XCTAssertEqual(logs.messages, [
            "layoutlm_model_active env=dev version=export-1 export_id=export-1 training_job=training-1 source=pointer cached=false path=\(first.path)",
            "layoutlm_model_active env=dev version=export-1 export_id=export-1 training_job=training-1 source=pointer cached=true path=\(first.path)",
        ])
    }

    func testAliasETagChangesKeepCurrentAndMostRecentOtherVersion() async throws {
        s3.bundles["alias.zip"] = try bundle()
        s3.head = S3ObjectHead(eTag: "\"etag-1-50\"", contentLength: 123)
        let first = try await resolve()
        // A compiled model in the old version must never reach the new one.
        try FileManager.default.createDirectory(at: first.appendingPathComponent("LayoutLM.mlmodelc"), withIntermediateDirectories: true)
        s3.head = S3ObjectHead(eTag: "\"etag-2-50\"", contentLength: 123)
        let second = try await resolve()
        XCTAssertEqual(second.lastPathComponent, "etag-2-50")
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: second.appendingPathComponent("LayoutLM.mlmodelc").path))
        s3.head = S3ObjectHead(eTag: "\"etag-3-50\"", contentLength: 123)
        let third = try await resolve()
        XCTAssertFalse(FileManager.default.fileExists(atPath: first.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: third.path))
        let cached = try await resolve()
        XCTAssertEqual(cached, third)
        XCTAssertEqual(s3.downloads, ["alias.zip", "alias.zip", "alias.zip"])
        XCTAssertTrue(logs.messages[0].contains("source=alias cached=false"))
    }

    func testIdentityMismatchRemovesTemporaryDirectory() async throws {
        try setPointer(data: bundle(exportID: "wrong"))
        do {
            _ = try await resolve()
            XCTFail("Expected identity mismatch")
        } catch ModelDownloaderError.identityMismatch(let expected, let found) {
            XCTAssertEqual(expected, "export-1")
            XCTAssertEqual(found, "wrong")
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: cache.path), [])
    }

    func testMissingActiveModelNeverReturnsExistingOtherVersionAndRemovesStaleTemp() async throws {
        let other = cache.appendingPathComponent("other-env-version")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: true)
        let temporary = cache.appendingPathComponent(".tmp-abandoned")
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
        do {
            _ = try await resolve()
            XCTFail("Expected no active model")
        } catch ModelDownloaderError.noActiveModel(let env) {
            XCTAssertEqual(env, "dev")
        }
        XCTAssertEqual(s3.downloads, [])
        XCTAssertTrue(FileManager.default.fileExists(atPath: other.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: temporary.path))
    }

    func testMissingIdentityAndFailedDownloadDoNotPublishVersion() async throws {
        try setPointer(data: bundle(identity: false))
        do { _ = try await resolve(); XCTFail("Expected invalid bundle") }
        catch ModelDownloaderError.extractionFailed { }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: cache.path), [])
        s3.bundles = [:]
        do { _ = try await resolve(); XCTFail("Expected failed download") }
        catch is ObjectNotFoundError { }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: cache.path), [])
    }

    func testMalformedPointerFailsWithoutFallingBackToAlias() async throws {
        s3.pointer = Data("{}".utf8)
        s3.head = S3ObjectHead(eTag: "alias", contentLength: 1)
        do { _ = try await resolve(); XCTFail("Expected invalid pointer") }
        catch ModelDownloaderError.invalidPointer { }
        XCTAssertEqual(s3.downloads, [])
    }

    func testLegacySignatureStillAcceptsBundleWithoutIdentity() async throws {
        s3.bundles["alias.zip"] = try bundle(identity: false)
        let result = try await downloader.ensureModelDownloaded(bucket: "test-dev", key: "alias.zip", localCachePath: cache.path)
        XCTAssertEqual(result.path, cache.path)
    }
}
#endif
