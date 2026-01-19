/**
 * Mock fixtures for image details API responses.
 * Used in E2E tests to avoid hitting real infrastructure.
 */

import type { CachedImageDetailsResponse, ImageDetailsApiResponse } from "../../types/api";

/**
 * Mock scan image details - represents a scanned receipt
 */
export const mockScanImageDetails: ImageDetailsApiResponse = {
  image: {
    image_id: "test-scan-image-001",
    width: 1200,
    height: 1600,
    timestamp_added: "2024-01-01T00:00:00Z",
    raw_s3_bucket: "test-bucket",
    raw_s3_key: "raw/test-scan.jpg",
    sha256: "abc123",
    cdn_s3_bucket: "test-cdn-bucket",
    cdn_s3_key: "cdn/test-scan.jpg",
    cdn_webp_s3_key: "cdn/test-scan.webp",
    cdn_avif_s3_key: "cdn/test-scan.avif",
    image_type: "scan",
  },
  lines: [
    {
      image_id: "test-scan-image-001",
      line_id: 1,
      text: "SPROUTS",
      bounding_box: { x: 0.3, y: 0.05, width: 0.4, height: 0.03 },
      top_left: { x: 0.3, y: 0.92 },
      top_right: { x: 0.7, y: 0.92 },
      bottom_left: { x: 0.3, y: 0.95 },
      bottom_right: { x: 0.7, y: 0.95 },
      angle_degrees: 0,
      angle_radians: 0,
      confidence: 0.99,
    },
    {
      image_id: "test-scan-image-001",
      line_id: 2,
      text: "FARMERS MARKET",
      bounding_box: { x: 0.25, y: 0.08, width: 0.5, height: 0.025 },
      top_left: { x: 0.25, y: 0.89 },
      top_right: { x: 0.75, y: 0.89 },
      bottom_left: { x: 0.25, y: 0.915 },
      bottom_right: { x: 0.75, y: 0.915 },
      angle_degrees: 0,
      angle_radians: 0,
      confidence: 0.98,
    },
    {
      image_id: "test-scan-image-001",
      line_id: 3,
      text: "MILK $4.99",
      bounding_box: { x: 0.2, y: 0.3, width: 0.6, height: 0.025 },
      top_left: { x: 0.2, y: 0.67 },
      top_right: { x: 0.8, y: 0.67 },
      bottom_left: { x: 0.2, y: 0.695 },
      bottom_right: { x: 0.8, y: 0.695 },
      angle_degrees: 0,
      angle_radians: 0,
      confidence: 0.97,
    },
  ],
  receipts: [
    {
      receipt_id: 1,
      image_id: "test-scan-image-001",
      width: 1200,
      height: 1600,
      timestamp_added: "2024-01-01T00:00:00Z",
      raw_s3_bucket: "test-bucket",
      raw_s3_key: "raw/test-scan.jpg",
      top_left: { x: 0.1, y: 0.95 },
      top_right: { x: 0.9, y: 0.95 },
      bottom_left: { x: 0.1, y: 0.05 },
      bottom_right: { x: 0.9, y: 0.05 },
      sha256: "abc123",
      cdn_s3_bucket: "test-cdn-bucket",
      cdn_s3_key: "cdn/test-scan.jpg",
      cdn_webp_s3_key: "cdn/test-scan.webp",
      cdn_avif_s3_key: "cdn/test-scan.avif",
    },
  ],
};

/**
 * Mock photo image details - represents a photographed receipt
 */
export const mockPhotoImageDetails: ImageDetailsApiResponse = {
  image: {
    image_id: "test-photo-image-001",
    width: 1080,
    height: 1920,
    timestamp_added: "2024-01-01T00:00:00Z",
    raw_s3_bucket: "test-bucket",
    raw_s3_key: "raw/test-photo.jpg",
    sha256: "def456",
    cdn_s3_bucket: "test-cdn-bucket",
    cdn_s3_key: "cdn/test-photo.jpg",
    cdn_webp_s3_key: "cdn/test-photo.webp",
    cdn_avif_s3_key: "cdn/test-photo.avif",
    image_type: "photo",
  },
  lines: [
    {
      image_id: "test-photo-image-001",
      line_id: 1,
      text: "RESTAURANT",
      bounding_box: { x: 0.3, y: 0.1, width: 0.4, height: 0.03 },
      top_left: { x: 0.3, y: 0.87 },
      top_right: { x: 0.7, y: 0.87 },
      bottom_left: { x: 0.3, y: 0.9 },
      bottom_right: { x: 0.7, y: 0.9 },
      angle_degrees: 0,
      angle_radians: 0,
      confidence: 0.95,
    },
    {
      image_id: "test-photo-image-001",
      line_id: 2,
      text: "TOTAL $25.00",
      bounding_box: { x: 0.2, y: 0.5, width: 0.6, height: 0.03 },
      top_left: { x: 0.2, y: 0.47 },
      top_right: { x: 0.8, y: 0.47 },
      bottom_left: { x: 0.2, y: 0.5 },
      bottom_right: { x: 0.8, y: 0.5 },
      angle_degrees: 0,
      angle_radians: 0,
      confidence: 0.96,
    },
  ],
  receipts: [
    {
      receipt_id: 1,
      image_id: "test-photo-image-001",
      width: 1080,
      height: 1920,
      timestamp_added: "2024-01-01T00:00:00Z",
      raw_s3_bucket: "test-bucket",
      raw_s3_key: "raw/test-photo.jpg",
      top_left: { x: 0.15, y: 0.9 },
      top_right: { x: 0.85, y: 0.9 },
      bottom_left: { x: 0.15, y: 0.1 },
      bottom_right: { x: 0.85, y: 0.1 },
      sha256: "def456",
      cdn_s3_bucket: "test-cdn-bucket",
      cdn_s3_key: "cdn/test-photo.jpg",
      cdn_webp_s3_key: "cdn/test-photo.webp",
      cdn_avif_s3_key: "cdn/test-photo.avif",
    },
  ],
};

/**
 * Cached response wrapper for scan images
 */
export const mockScanCachedResponse: CachedImageDetailsResponse = {
  images: [mockScanImageDetails],
  cached_at: "2024-01-01T00:00:00Z",
};

/**
 * Cached response wrapper for photo images
 */
export const mockPhotoCachedResponse: CachedImageDetailsResponse = {
  images: [mockPhotoImageDetails],
  cached_at: "2024-01-01T00:00:00Z",
};

