import Foundation
import Logging

#if os(macOS)
import Compression

/// Downloads and caches LayoutLM model bundles from S3.
///
/// The model bundle should be a directory containing:
/// - *.mlpackage/ (CoreML model)
/// - vocab.txt (BERT tokenizer vocabulary)
/// - config.json (model configuration with labels)
public final class ModelDownloader {
    private let s3: S3ClientProtocol
    private let logger: Logger

    public init(s3: S3ClientProtocol, logger: Logger) {
        self.s3 = s3
        self.logger = logger
    }

    /// Ensure the model is downloaded and cached locally.
    ///
    /// Checks if the model is already cached at the specified path.
    /// If not, downloads from S3 and extracts to the cache directory.
    ///
    /// - Parameters:
    ///   - bucket: S3 bucket containing the model
    ///   - key: S3 key (path) to the model bundle (zip file)
    ///   - localCachePath: Local directory path for caching (e.g., ".models/layoutlm")
    /// - Returns: URL to the local model bundle directory
    public func ensureModelDownloaded(
        bucket: String,
        key: String,
        localCachePath: String
    ) async throws -> URL {
        let cacheDir = URL(fileURLWithPath: localCachePath)
        let fileManager = FileManager.default

        // Check if already cached
        if isModelCached(at: cacheDir) {
            logger.info("model_cached path=\(cacheDir.path)")
            return cacheDir
        }

        logger.info("model_download_start bucket=\(bucket) key=\(key)")

        // Create cache directory
        try fileManager.createDirectory(at: cacheDir, withIntermediateDirectories: true)

        // Download from S3
        let modelData = try await s3.getObject(bucket: bucket, key: key)
        logger.info("model_download_complete size=\(modelData.count)")

        // Determine archive type and extract
        if key.hasSuffix(".zip") {
            try extractZip(data: modelData, to: cacheDir)
        } else if key.hasSuffix(".tar.gz") || key.hasSuffix(".tgz") {
            try extractTarGz(data: modelData, to: cacheDir)
        } else {
            // Assume it's a zip file
            try extractZip(data: modelData, to: cacheDir)
        }

        // Verify extraction
        guard isModelCached(at: cacheDir) else {
            throw ModelDownloaderError.extractionFailed("Model files not found after extraction")
        }

        logger.info("model_extract_complete path=\(cacheDir.path)")
        return cacheDir
    }

    private struct ActiveModel: Decodable {
        let schema_version: Int
        let export_id: String
        let training_job_id: String
        let training_job_name: String
        let bundle_key: String
        let bundle_etag: String
        let bundle_size_bytes: Int
        let promoted_at: String
        let promoted_by: String
    }

    private struct ModelIdentity: Decodable {
        let export_id: String?
        let training_job_id: String?
    }

    /// Resolve the environment's active model on every drain, then reuse only
    /// that version's validated directory. The legacy overload remains available.
    public func ensureModelDownloaded(
        bucket: String,
        key: String,
        localCachePath: String,
        pointerKey: String,
        env: String
    ) async throws -> URL {
        let fm = FileManager.default
        let root = URL(fileURLWithPath: localCachePath)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        for item in try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            where item.lastPathComponent.hasPrefix(".tmp-") {
            try fm.removeItem(at: item)
        }

        let pointer: ActiveModel?
        let version: String
        let bundleKey: String
        if let data = try await s3.getObjectIfExists(bucket: bucket, key: pointerKey) {
            let active: ActiveModel
            do {
                active = try JSONDecoder().decode(ActiveModel.self, from: data)
            } catch {
                throw ModelDownloaderError.invalidPointer("Cannot decode active model: \(error)")
            }
            guard active.schema_version == 1, !active.bundle_key.isEmpty,
                  !active.bundle_etag.isEmpty, active.bundle_size_bytes > 0 else {
                throw ModelDownloaderError.invalidPointer("Unsupported schema or incomplete bundle metadata")
            }
            pointer = active
            version = active.export_id
            bundleKey = active.bundle_key
        } else {
            guard let head = try await s3.headObject(bucket: bucket, key: key) else {
                throw ModelDownloaderError.noActiveModel(env)
            }
            pointer = nil
            version = head.eTag.trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            bundleKey = key
        }
        // Versions are single path components, never paths supplied by S3.
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        guard !version.isEmpty, version.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
            throw ModelDownloaderError.invalidPointer("Invalid model version: \(version)")
        }
        let destination = root.appendingPathComponent(version, isDirectory: true)
        var identity = try validatedIdentity(at: destination, expected: pointer?.export_id)
        let cached = identity != nil
        if identity == nil {
            let temporary = root.appendingPathComponent(".tmp-\(version)-\(UUID().uuidString)")
            try fm.createDirectory(at: temporary, withIntermediateDirectories: false)
            defer { try? fm.removeItem(at: temporary) }
            let data = try await s3.getObject(bucket: bucket, key: bundleKey)
            if let pointer = pointer, data.count != pointer.bundle_size_bytes {
                throw ModelDownloaderError.extractionFailed("Bundle size differs from active pointer")
            }
            if bundleKey.hasSuffix(".tar.gz") || bundleKey.hasSuffix(".tgz") {
                try extractTarGz(data: data, to: temporary)
            } else {
                try extractZip(data: data, to: temporary)
            }
            guard let downloaded = try validatedIdentity(at: temporary, expected: pointer?.export_id) else {
                throw ModelDownloaderError.extractionFailed("Model files or model_identity.json missing after extraction")
            }
            // Compiled output belongs to this machine and this version. Never
            // trust a precompiled artifact carried in an archive.
            for item in try fm.contentsOfDirectory(at: temporary, includingPropertiesForKeys: nil)
                where item.pathExtension == "mlmodelc" {
                try fm.removeItem(at: item)
            }
            if fm.fileExists(atPath: destination.path) {
                try fm.removeItem(at: destination)
            }
            // Same-filesystem move publishes the entire validated bundle atomically.
            try fm.moveItem(at: temporary, to: destination)
            identity = downloaded
        }
        try fm.setAttributes([.modificationDate: Date()], ofItemAtPath: destination.path)
        try pruneVersions(in: root, keeping: destination)
        let source = pointer == nil ? "alias" : "pointer"
        let trainingJob = pointer?.training_job_name ?? identity?.training_job_id ?? "none"
        logger.info("layoutlm_model_active env=\(env) version=\(version) export_id=\(identity?.export_id ?? "none") training_job=\(trainingJob) source=\(source) cached=\(cached) path=\(destination.path)")
        return destination
    }

    private func validatedIdentity(at path: URL, expected: String?) throws -> ModelIdentity? {
        guard isModelCached(at: path),
              let data = try? Data(contentsOf: path.appendingPathComponent("model_identity.json")),
              let identity = try? JSONDecoder().decode(ModelIdentity.self, from: data) else {
            return nil
        }
        if let expected = expected, identity.export_id != expected {
            throw ModelDownloaderError.identityMismatch(expected: expected, found: identity.export_id)
        }
        return identity
    }

    private func pruneVersions(in root: URL, keeping current: URL) throws {
        let fm = FileManager.default
        let others = try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey])
            .filter {
                $0.standardizedFileURL.path != current.standardizedFileURL.path && !$0.lastPathComponent.hasPrefix(".")
                    && $0.pathExtension != "mlpackage" && $0.pathExtension != "mlmodelc"
                    && (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
            }
            .sorted {
                let left = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let right = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return left == right ? $0.lastPathComponent < $1.lastPathComponent : left > right
            }
        for old in others.dropFirst() { try fm.removeItem(at: old) }
    }

    /// Check if the model bundle is already cached with required files.
    private func isModelCached(at path: URL) -> Bool {
        let fileManager = FileManager.default

        guard fileManager.fileExists(atPath: path.path) else {
            return false
        }

        // Check for required files
        let vocabPath = path.appendingPathComponent("vocab.txt")
        let configPath = path.appendingPathComponent("config.json")

        guard fileManager.fileExists(atPath: vocabPath.path),
              fileManager.fileExists(atPath: configPath.path) else {
            return false
        }

        // Check for .mlpackage directory
        do {
            let contents = try fileManager.contentsOfDirectory(at: path, includingPropertiesForKeys: nil)
            let hasMLPackage = contents.contains { $0.pathExtension == "mlpackage" }
            return hasMLPackage
        } catch {
            return false
        }
    }

    /// Extract a zip archive to the destination directory.
    private func extractZip(data: Data, to destination: URL) throws {
        let fileManager = FileManager.default
        let tempZipPath = destination.appendingPathComponent("model.zip")

        // Write zip to temp file
        try data.write(to: tempZipPath)

        // Use Process to unzip (more reliable than Archive framework)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
        process.arguments = ["-o", "-q", tempZipPath.path, "-d", destination.path]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        try process.run()
        process.waitUntilExit()

        // Clean up temp zip
        try? fileManager.removeItem(at: tempZipPath)

        if process.terminationStatus != 0 {
            throw ModelDownloaderError.extractionFailed("unzip failed with status \(process.terminationStatus)")
        }

        // If files are in a subdirectory, move them up
        try flattenExtractedDirectory(at: destination)
    }

    /// Extract a tar.gz archive to the destination directory.
    private func extractTarGz(data: Data, to destination: URL) throws {
        let fileManager = FileManager.default
        let tempTarPath = destination.appendingPathComponent("model.tar.gz")

        // Write tar.gz to temp file
        try data.write(to: tempTarPath)

        // Use Process to extract
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        process.arguments = ["-xzf", tempTarPath.path, "-C", destination.path]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        try process.run()
        process.waitUntilExit()

        // Clean up temp tar
        try? fileManager.removeItem(at: tempTarPath)

        if process.terminationStatus != 0 {
            throw ModelDownloaderError.extractionFailed("tar failed with status \(process.terminationStatus)")
        }

        // If files are in a subdirectory, move them up
        try flattenExtractedDirectory(at: destination)
    }

    /// If the archive extracted to a single subdirectory, move contents up.
    private func flattenExtractedDirectory(at path: URL) throws {
        let fileManager = FileManager.default
        let contents = try fileManager.contentsOfDirectory(at: path, includingPropertiesForKeys: [.isDirectoryKey])

        // Filter out hidden files and temp files
        let visibleContents = contents.filter { !$0.lastPathComponent.hasPrefix(".") }

        // If there's exactly one directory and no files, move its contents up
        if visibleContents.count == 1,
           let onlyItem = visibleContents.first,
           (try? onlyItem.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {

            let subdirContents = try fileManager.contentsOfDirectory(at: onlyItem, includingPropertiesForKeys: nil)

            for item in subdirContents {
                let destPath = path.appendingPathComponent(item.lastPathComponent)
                try fileManager.moveItem(at: item, to: destPath)
            }

            // Remove empty subdirectory
            try fileManager.removeItem(at: onlyItem)
        }
    }
}

/// Errors that can occur during model download.
public enum ModelDownloaderError: Error, LocalizedError {
    case extractionFailed(String)
    case modelNotFound
    case noActiveModel(String)
    case identityMismatch(expected: String, found: String?)
    case invalidPointer(String)

    public var errorDescription: String? {
        switch self {
        case .extractionFailed(let message):
            return "Model extraction failed: \(message)"
        case .noActiveModel(let env):
            return "No active LayoutLM model for \(env)"
        case .identityMismatch(let expected, let found):
            return "Model identity mismatch: expected \(expected), found \(found ?? "none")"
        case .invalidPointer(let message):
            return "Invalid active model pointer: \(message)"
        case .modelNotFound:
            return "Model bundle not found in S3"
        }
    }
}

#endif
