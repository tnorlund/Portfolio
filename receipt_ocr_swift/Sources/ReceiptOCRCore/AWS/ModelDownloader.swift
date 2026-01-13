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

    public var errorDescription: String? {
        switch self {
        case .extractionFailed(let message):
            return "Model extraction failed: \(message)"
        case .modelNotFound:
            return "Model bundle not found in S3"
        }
    }
}

#endif
