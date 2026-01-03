import Foundation
import os

#if os(macOS)
import CoreGraphics

private let logger = Logger(subsystem: "ReceiptOCRCore", category: "ReceiptProcessor")

// MARK: - Receipt Processor

/// Processes OCR line data to detect and extract individual receipts from images.
///
/// Handles both PHOTO and SCAN image types with different algorithms:
/// - **PHOTO**: Uses perspective transform to correct keystone distortion
/// - **SCAN**: Uses affine transform for simpler rotation/translation correction
public struct ReceiptProcessor {

    public init() {}

    // MARK: - Main Entry Point

    /// Process an image to detect and extract receipts.
    ///
    /// Routes to the appropriate processing method based on image type.
    /// NATIVE images are skipped (already single receipts).
    ///
    /// - Parameters:
    ///   - image: Source CGImage
    ///   - lines: OCR lines from VisionOCREngine
    ///   - classification: Image classification result
    ///   - clustering: DBSCAN clustering result
    /// - Returns: Array of processed receipts with warped images
    public func process(
        image: CGImage,
        lines: [Line],
        classification: ClassificationResult,
        clustering: ClusteringResult
    ) -> [ProcessedReceipt] {
        switch classification.imageType {
        case .photo:
            return processPhoto(image: image, lines: lines, clustering: clustering)
        case .scan:
            return processScan(image: image, lines: lines, clustering: clustering)
        case .native:
            // NATIVE images are already single receipts, skip geometry processing
            logger.debug("Skipping geometry processing for NATIVE image")
            return []
        }
    }

    // MARK: - PHOTO Processing

    /// Process a PHOTO image using perspective transform.
    ///
    /// Algorithm:
    /// 1. For each cluster, compute convex hull of all word corners
    /// 2. Find top/bottom lines by Y centroid position
    /// 3. Compute average edge angle using circular mean
    /// 4. Find left/right hull extremes along perpendicular axis
    /// 5. Intersect boundary lines to get receipt corners
    /// 6. Apply perspective transform to extract warped receipt
    ///
    /// - Parameters:
    ///   - image: Source CGImage
    ///   - lines: OCR lines
    ///   - clustering: DBSCAN clustering result
    /// - Returns: Array of processed receipts
    public func processPhoto(
        image: CGImage,
        lines: [Line],
        clustering: ClusteringResult
    ) -> [ProcessedReceipt] {
        var results: [ProcessedReceipt] = []
        let imageWidth = CGFloat(image.width)
        let imageHeight = CGFloat(image.height)

        for (clusterId, lineIndices) in clustering.clusters {
            // Skip noise cluster
            guard clusterId != -1 else { continue }

            // Need at least 3 lines for meaningful processing
            guard lineIndices.count >= 3 else {
                logger.debug("Skipping cluster \(clusterId): insufficient lines (\(lineIndices.count))")
                continue
            }

            // Get lines for this cluster
            let clusterLines = lineIndices.compactMap { idx -> Line? in
                guard idx >= 0 && idx < lines.count else { return nil }
                return lines[idx]
            }

            guard clusterLines.count >= 3 else { continue }

            // Collect all corner points (in normalized coordinates)
            var allCorners: [(CGFloat, CGFloat)] = []
            for line in clusterLines {
                allCorners.append((line.topLeft.x, line.topLeft.y))
                allCorners.append((line.topRight.x, line.topRight.y))
                allCorners.append((line.bottomLeft.x, line.bottomLeft.y))
                allCorners.append((line.bottomRight.x, line.bottomRight.y))
            }

            guard allCorners.count >= 4 else { continue }

            // Compute convex hull
            let hull = convexHull(points: allCorners)
            guard hull.count >= 4 else {
                logger.debug("Skipping cluster \(clusterId): hull has insufficient points (\(hull.count))")
                continue
            }

            // Find top and bottom lines by centroid Y position
            // In normalized coords: higher Y = top of receipt
            let sortedLines = clusterLines.sorted { a, b in
                let aY = (a.topLeft.y + a.topRight.y + a.bottomLeft.y + a.bottomRight.y) / 4.0
                let bY = (b.topLeft.y + b.topRight.y + b.bottomLeft.y + b.bottomRight.y) / 4.0
                return aY > bY  // Descending, so highest Y first
            }

            var topLine = sortedLines.first!
            var bottomLine = sortedLines.last!

            // Check for upside-down receipt
            let avgAngleDeg = clusterLines.reduce(0.0) { $0 + $1.angleDegrees } / CGFloat(clusterLines.count)
            if abs(avgAngleDeg) > 90 {
                logger.debug("Cluster \(clusterId) appears upside down (avg_angle=\(avgAngleDeg)°), swapping top/bottom")
                swap(&topLine, &bottomLine)
            }

            // Get corners from top/bottom lines
            // Order: [TL, TR, BL, BR]
            let topLineCorners: [(CGFloat, CGFloat)] = [
                (topLine.topLeft.x, topLine.topLeft.y),
                (topLine.topRight.x, topLine.topRight.y),
                (topLine.bottomLeft.x, topLine.bottomLeft.y),
                (topLine.bottomRight.x, topLine.bottomRight.y),
            ]
            let bottomLineCorners: [(CGFloat, CGFloat)] = [
                (bottomLine.topLeft.x, bottomLine.topLeft.y),
                (bottomLine.topRight.x, bottomLine.topRight.y),
                (bottomLine.bottomLeft.x, bottomLine.bottomLeft.y),
                (bottomLine.bottomRight.x, bottomLine.bottomRight.y),
            ]

            // Compute receipt corners using rotated bounding box approach
            let receiptCorners = computeRotatedBoundingBoxCorners(
                hull: hull,
                topLineCorners: topLineCorners,
                bottomLineCorners: bottomLineCorners
            )

            guard receiptCorners.count == 4 else {
                logger.debug("Skipping cluster \(clusterId): failed to compute receipt corners")
                continue
            }

            // Convert to image coordinates (flip Y, scale to pixels)
            let srcCornersPixels = receiptCorners.map { corner -> (CGFloat, CGFloat) in
                (corner.0 * imageWidth, (1.0 - corner.1) * imageHeight)
            }

            // Validate corners
            if !validateCorners(srcCornersPixels) {
                logger.debug("Skipping cluster \(clusterId): invalid corners")
                continue
            }

            // Compute output dimensions
            let topWidth = distance(srcCornersPixels[0], srcCornersPixels[1])
            let bottomWidth = distance(srcCornersPixels[3], srcCornersPixels[2])
            let leftHeight = distance(srcCornersPixels[0], srcCornersPixels[3])
            let rightHeight = distance(srcCornersPixels[1], srcCornersPixels[2])

            let warpedWidth = Int(round((topWidth + bottomWidth) / 2.0))
            let warpedHeight = Int(round((leftHeight + rightHeight) / 2.0))

            guard warpedWidth >= 10 && warpedHeight >= 10 else {
                logger.debug("Skipping cluster \(clusterId): output too small (\(warpedWidth)x\(warpedHeight))")
                continue
            }

            // Apply perspective transform
            let outputSize = CGSize(width: warpedWidth, height: warpedHeight)
            guard let warpedImage = applyPerspectiveTransform(
                image: image,
                srcCorners: srcCornersPixels,
                outputSize: outputSize
            ) else {
                logger.warning("Cluster \(clusterId): perspective transform failed")
                continue
            }

            // Create bounds in normalized coordinates
            guard let bounds = ReceiptBounds(corners: receiptCorners) else { continue }

            let processed = ProcessedReceipt(
                clusterId: clusterId,
                bounds: bounds,
                warpedImage: warpedImage,
                warpedWidth: warpedWidth,
                warpedHeight: warpedHeight,
                lineIndices: lineIndices
            )
            results.append(processed)

            logger.debug("Processed PHOTO cluster \(clusterId): \(warpedWidth)x\(warpedHeight)")
        }

        return results
    }

    // MARK: - SCAN Processing

    /// Process a SCAN image using affine transform.
    ///
    /// Algorithm:
    /// 1. Merge overlapping clusters using IoU
    /// 2. For each cluster, compute minimum area bounding rectangle
    /// 3. Force portrait orientation (swap if landscape)
    /// 4. Apply affine transform to extract warped receipt
    ///
    /// - Parameters:
    ///   - image: Source CGImage
    ///   - lines: OCR lines
    ///   - clustering: DBSCAN clustering result
    /// - Returns: Array of processed receipts
    public func processScan(
        image: CGImage,
        lines: [Line],
        clustering: ClusteringResult
    ) -> [ProcessedReceipt] {
        var results: [ProcessedReceipt] = []
        let imageWidth = CGFloat(image.width)
        let imageHeight = CGFloat(image.height)

        // Merge overlapping clusters
        let mergedClusters = joinOverlappingClusters(
            clusters: clustering.clusters,
            lines: lines,
            imageWidth: imageWidth,
            imageHeight: imageHeight,
            iouThreshold: 0.01
        )

        for (clusterId, lineIndices) in mergedClusters {
            guard clusterId != -1 else { continue }
            guard !lineIndices.isEmpty else { continue }

            // Collect all corners in absolute coordinates
            var pointsAbs: [(CGFloat, CGFloat)] = []
            for idx in lineIndices {
                guard idx >= 0 && idx < lines.count else { continue }
                let line = lines[idx]

                // Convert normalized to absolute coords (flip Y)
                pointsAbs.append((line.topLeft.x * imageWidth, (1 - line.topLeft.y) * imageHeight))
                pointsAbs.append((line.topRight.x * imageWidth, (1 - line.topRight.y) * imageHeight))
                pointsAbs.append((line.bottomLeft.x * imageWidth, (1 - line.bottomLeft.y) * imageHeight))
                pointsAbs.append((line.bottomRight.x * imageWidth, (1 - line.bottomRight.y) * imageHeight))
            }

            guard !pointsAbs.isEmpty else { continue }

            // Compute minimum area bounding rectangle
            var (center, size, angleDeg) = minAreaRect(points: pointsAbs)
            var w = Int(round(size.0))
            var h = Int(round(size.1))

            guard w >= 1 && h >= 1 else {
                logger.debug("Skipping cluster \(clusterId): degenerate bounding box")
                continue
            }

            // Force portrait orientation (receipts are taller than wide)
            if w > h {
                // Rotate by -90° and swap dimensions
                angleDeg -= 90.0
                swap(&w, &h)
                size = (size.1, size.0)
            }

            // Get box corners
            let boxCorners = boxPoints(center: center, size: size, angleDeg: angleDeg)
            let orderedCorners = reorderBoxPoints(boxCorners)

            // Validate corners
            if !validateCorners(orderedCorners) {
                logger.debug("Skipping cluster \(clusterId): invalid corners")
                continue
            }

            // Apply affine transform
            let outputSize = CGSize(width: w, height: h)
            guard let warpedImage = applyAffineTransform(
                image: image,
                srcCorners: orderedCorners,
                outputSize: outputSize
            ) else {
                logger.warning("Cluster \(clusterId): affine transform failed")
                continue
            }

            // Convert corners back to normalized coordinates
            let normalizedCorners = orderedCorners.map { corner -> (CGFloat, CGFloat) in
                (corner.0 / imageWidth, 1.0 - corner.1 / imageHeight)
            }

            guard let bounds = ReceiptBounds(corners: normalizedCorners) else { continue }

            let processed = ProcessedReceipt(
                clusterId: clusterId,
                bounds: bounds,
                warpedImage: warpedImage,
                warpedWidth: w,
                warpedHeight: h,
                lineIndices: lineIndices
            )
            results.append(processed)

            logger.debug("Processed SCAN cluster \(clusterId): \(w)x\(h)")
        }

        return results
    }

    // MARK: - Helpers

    /// Euclidean distance between two points.
    private func distance(_ p1: (CGFloat, CGFloat), _ p2: (CGFloat, CGFloat)) -> CGFloat {
        return sqrt(pow(p2.0 - p1.0, 2) + pow(p2.1 - p1.1, 2))
    }

    /// Validate that corners form a reasonable quadrilateral.
    ///
    /// Checks for:
    /// - Duplicate corners
    /// - Too small area
    /// - Invalid dimensions
    private func validateCorners(_ corners: [(CGFloat, CGFloat)]) -> Bool {
        guard corners.count == 4 else { return false }

        // Check for duplicate corners (distance < 1 pixel)
        for i in 0..<4 {
            for j in (i + 1)..<4 {
                if distance(corners[i], corners[j]) < 1.0 {
                    return false
                }
            }
        }

        // Check bounding box isn't degenerate
        let xs = corners.map { $0.0 }
        let ys = corners.map { $0.1 }
        let width = (xs.max() ?? 0) - (xs.min() ?? 0)
        let height = (ys.max() ?? 0) - (ys.min() ?? 0)

        if width < 10 || height < 10 {
            return false
        }

        return true
    }
}

#endif
