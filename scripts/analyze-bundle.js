#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
const { promisify } = require('util');

const execAsync = promisify(exec);

/**
 * Analyzes Next.js bundle sizes and generates a report
 */
async function analyzeBundles() {
  const buildDir = path.join(process.cwd(), '.next');
  
  if (!fs.existsSync(buildDir)) {
    console.error('Build directory not found. Please run "npm run build" first.');
    process.exit(1);
  }

  console.log('Analyzing bundle sizes...\n');

  const stats = {
    pages: {},
    chunks: {},
    total: {
      size: 0,
      gzipSize: 0,
    },
  };

  // Analyze page bundles
  const pagesDir = path.join(buildDir, 'static', 'chunks', 'pages');
  if (fs.existsSync(pagesDir)) {
    const pageFiles = fs.readdirSync(pagesDir).filter(f => f.endsWith('.js'));
    
    for (const file of pageFiles) {
      const filePath = path.join(pagesDir, file);
      const stat = fs.statSync(filePath);
      const gzipSize = await getGzipSize(filePath);
      
      const pageName = file.replace(/\.js$/, '').replace(/-[a-z0-9]+$/, '');
      stats.pages[pageName] = {
        size: stat.size,
        gzipSize,
        file,
      };
      
      stats.total.size += stat.size;
      stats.total.gzipSize += gzipSize;
    }
  }

  // Analyze chunk files
  const chunksDir = path.join(buildDir, 'static', 'chunks');
  const chunkFiles = fs.readdirSync(chunksDir)
    .filter(f => f.endsWith('.js') && !f.includes('pages'));

  for (const file of chunkFiles) {
    const filePath = path.join(chunksDir, file);
    const stat = fs.statSync(filePath);
    const gzipSize = await getGzipSize(filePath);
    
    stats.chunks[file] = {
      size: stat.size,
      gzipSize,
    };
    
    stats.total.size += stat.size;
    stats.total.gzipSize += gzipSize;
  }

  // Generate report
  generateReport(stats);
  
  // Save to file
  const reportPath = path.join(process.cwd(), 'bundle-analysis.json');
  fs.writeFileSync(reportPath, JSON.stringify(stats, null, 2));
  console.log(`\nDetailed report saved to: ${reportPath}`);
}

/**
 * Get gzip size of a file
 */
async function getGzipSize(filePath) {
  try {
    const { stdout } = await execAsync(`gzip -c "${filePath}" | wc -c`);
    return parseInt(stdout.trim(), 10);
  } catch (error) {
    return 0;
  }
}

/**
 * Format bytes to human readable
 */
function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

/**
 * Generate console report
 */
function generateReport(stats) {
  console.log('='.repeat(80));
  console.log('BUNDLE SIZE ANALYSIS');
  console.log('='.repeat(80));
  
  // Page bundles
  console.log('\nPAGE BUNDLES:');
  console.log('-'.repeat(80));
  console.log(
    'Page'.padEnd(30) +
    'Size'.padEnd(15) +
    'Gzipped'.padEnd(15) +
    'Filename'
  );
  console.log('-'.repeat(80));
  
  Object.entries(stats.pages)
    .sort((a, b) => b[1].gzipSize - a[1].gzipSize)
    .forEach(([page, info]) => {
      console.log(
        page.padEnd(30) +
        formatBytes(info.size).padEnd(15) +
        formatBytes(info.gzipSize).padEnd(15) +
        info.file
      );
    });

  // Chunk bundles
  console.log('\n\nCHUNK BUNDLES:');
  console.log('-'.repeat(80));
  console.log(
    'Chunk'.padEnd(40) +
    'Size'.padEnd(15) +
    'Gzipped'
  );
  console.log('-'.repeat(80));
  
  Object.entries(stats.chunks)
    .sort((a, b) => b[1].gzipSize - a[1].gzipSize)
    .forEach(([chunk, info]) => {
      console.log(
        chunk.padEnd(40) +
        formatBytes(info.size).padEnd(15) +
        formatBytes(info.gzipSize)
      );
    });

  // Total
  console.log('\n' + '='.repeat(80));
  console.log('TOTAL:');
  console.log(`  Original: ${formatBytes(stats.total.size)}`);
  console.log(`  Gzipped:  ${formatBytes(stats.total.gzipSize)}`);
  console.log('='.repeat(80));

  // Warnings
  const warnings = [];
  
  // Check for large pages
  Object.entries(stats.pages).forEach(([page, info]) => {
    if (info.gzipSize > 100 * 1024) { // 100KB
      warnings.push(`⚠️  Page "${page}" is large (${formatBytes(info.gzipSize)} gzipped)`);
    }
  });

  // Check for large chunks
  Object.entries(stats.chunks).forEach(([chunk, info]) => {
    if (info.gzipSize > 200 * 1024) { // 200KB
      warnings.push(`⚠️  Chunk "${chunk}" is large (${formatBytes(info.gzipSize)} gzipped)`);
    }
  });

  // Check total size
  if (stats.total.gzipSize > 1024 * 1024) { // 1MB
    warnings.push(`⚠️  Total bundle size is large (${formatBytes(stats.total.gzipSize)} gzipped)`);
  }

  if (warnings.length > 0) {
    console.log('\nWARNINGS:');
    warnings.forEach(w => console.log(w));
  } else {
    console.log('\n✅ Bundle sizes look good!');
  }
}

// Run the analysis
analyzeBundles().catch(console.error);