#!/usr/bin/env node
/**
 * Image Optimization Script
 * Converts PNG/JPG images to WebP and AVIF formats for better performance
 *
 * Usage: node scripts/optimize-images.js [image-path]
 * Example: node scripts/optimize-images.js public/hero.png
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

// Configuration
const WEBP_QUALITY = 85;
const AVIF_QUALITY = 85;

function optimizeImage(imagePath) {
  if (!fs.existsSync(imagePath)) {
    console.error(`❌ Image not found: ${imagePath}`);
    process.exit(1);
  }

  const { dir, name, ext } = path.parse(imagePath);
  const webpPath = path.join(dir, `${name}.webp`);
  const avifPath = path.join(dir, `${name}.avif`);

  console.log(`🎨 Optimizing: ${imagePath}`);

  // Get original file size
  const originalStats = fs.statSync(imagePath);
  const originalSize = originalStats.size;

  try {
    // Convert to WebP
    console.log("📦 Creating WebP version...");
    if (fs.existsSync("/opt/homebrew/bin/cwebp")) {
      execSync(
        `/opt/homebrew/bin/cwebp -q ${WEBP_QUALITY} "${imagePath}" -o "${webpPath}"`
      );
    } else {
      console.log("⚠️  cwebp not found, skipping WebP conversion");
    }

    // Convert to AVIF using ImageMagick
    console.log("🚀 Creating AVIF version...");
    try {
      execSync(`magick "${imagePath}" -quality ${AVIF_QUALITY} "${avifPath}"`);
    } catch (error) {
      console.log("⚠️  ImageMagick/AVIF conversion failed, skipping AVIF");
    }

    // Report results
    console.log("\n📊 Optimization Results:");
    console.log(
      `Original (${ext.toUpperCase()}): ${formatBytes(originalSize)}`
    );

    if (fs.existsSync(webpPath)) {
      const webpSize = fs.statSync(webpPath).size;
      const webpSavings = (
        ((originalSize - webpSize) / originalSize) *
        100
      ).toFixed(1);
      console.log(`WebP: ${formatBytes(webpSize)} (${webpSavings}% smaller)`);
    }

    if (fs.existsSync(avifPath)) {
      const avifSize = fs.statSync(avifPath).size;
      const avifSavings = (
        ((originalSize - avifSize) / originalSize) *
        100
      ).toFixed(1);
      console.log(`AVIF: ${formatBytes(avifSize)} (${avifSavings}% smaller)`);
    }

    console.log("\n✅ Optimization complete!");
    console.log("\n📝 To use these optimized images, update your code:");
    console.log(`
<picture>
  <source srcSet="${path.relative("public", avifPath)}" type="image/avif" />
  <source srcSet="${path.relative("public", webpPath)}" type="image/webp" />
  <img src="${path.relative(
    "public",
    imagePath
  )}" alt="Description" loading="lazy" decoding="async" />
</picture>
    `);
  } catch (error) {
    console.error(`❌ Optimization failed: ${error.message}`);
    process.exit(1);
  }
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// Main execution
const imagePath = process.argv[2];

if (!imagePath) {
  console.log(`
🎨 Image Optimization Script

Usage: node scripts/optimize-images.js [image-path]

Examples:
  node scripts/optimize-images.js public/hero.png
  node scripts/optimize-images.js public/images/profile.jpg

This script will create optimized WebP and AVIF versions alongside your original image.
  `);
  process.exit(0);
}

optimizeImage(imagePath);
