/**
 * Allocation-free scalar browser rotoscope.
 *
 * The TypeScript scalar implementation is the semantic oracle. This module
 * preserves its stage order and deterministic tie order while accepting a
 * precomputed focus-tier map from the worker.
 */

const ABI_VERSION: i32 = 1;
const MAX_DIMENSION: i32 = 2048;
const MAX_PIXELS: i32 = 1024 * 1024;

const STATUS_OK: i32 = 0;
const STATUS_INVALID_DIMENSIONS: i32 = 1;
const STATUS_INSUFFICIENT_CAPACITY: i32 = 2;
const STATUS_INVALID_FOCUS_TIER: i32 = 3;
const STATUS_INVALID_LAYOUT: i32 = 4;

const TIER_FACE: u8 = 0;
const TIER_BODY: u8 = 1;
const TIER_BACKGROUND: u8 = 2;

let gStatus: i32 = STATUS_OK;
let gMarkerCount: i32 = 0;
let gFaceMarkerCount: i32 = 0;
let gBodyMarkerCount: i32 = 0;
let gBackgroundMarkerCount: i32 = 0;

let gPixelCount: i32 = 0;
let gBudget: i32 = 0;
let gRequiredBytes: i32 = 0;
let gInputPtr: i32 = 0;
let gFocusPtr: i32 = 0;
let gGrayPtr: i32 = 0;
let gScratchPtr: i32 = 0;
let gHorizontalPtr: i32 = 0;
let gGradientXPtr: i32 = 0;
let gGradientYPtr: i32 = 0;
let gScoresPtr: i32 = 0;
let gSeenPtr: i32 = 0;
let gBlockedPtr: i32 = 0;
let gCandidatesPtr: i32 = 0;
let gMarkersPtr: i32 = 0;
let gBucketsPtr: i32 = 0;
let gPalettePtr: i32 = 0;

@inline
function minI32(left: i32, right: i32): i32 {
  return left < right ? left : right;
}

@inline
function maxI32(left: i32, right: i32): i32 {
  return left > right ? left : right;
}

@inline
function clampI32(value: i32, low: i32, high: i32): i32 {
  return minI32(high, maxI32(low, value));
}

@inline
function aligned4(value: i64): i64 {
  return ((value + 3) / 4) * 4;
}

@inline
function dimensionsAreValid(width: i32, height: i32): bool {
  if (width <= 0 || height <= 0 || width > MAX_DIMENSION || height > MAX_DIMENSION) {
    return false;
  }
  const count = i64(width) * i64(height);
  return count > 0 && count <= MAX_PIXELS;
}

@inline
function normalizedBudget(markerBudget: i32, count: i32): i32 {
  return clampI32(markerBudget, 1, count);
}

/**
 * Compute and cache the aligned arena layout. All arithmetic is performed as
 * i64 before narrowing, so no pointer can wrap through signed i32.
 */
function configureLayout(width: i32, height: i32, markerBudget: i32): bool {
  if (!dimensionsAreValid(width, height)) return false;

  const count = width * height;
  const budget = normalizedBudget(markerBudget, count);
  const base = i64(arenaBase());
  let cursor = base;

  gInputPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 4);
  gFocusPtr = i32(cursor);
  cursor = aligned4(cursor + count);
  gGrayPtr = i32(cursor);
  cursor = aligned4(cursor + count);
  gScratchPtr = i32(cursor);
  cursor = aligned4(cursor + count);
  gHorizontalPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 2);
  gGradientXPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 2);
  gGradientYPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 2);
  gScoresPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 4);
  gSeenPtr = i32(cursor);
  cursor = aligned4(cursor + count);
  gBlockedPtr = i32(cursor);
  cursor = aligned4(cursor + count);
  gCandidatesPtr = i32(cursor);
  cursor = aligned4(cursor + i64(count) * 4);
  gMarkersPtr = i32(cursor);
  cursor = aligned4(cursor + i64(budget) * 4);
  gBucketsPtr = i32(cursor);
  // 256 i32 heads followed by 256 i32 tails.
  cursor = aligned4(cursor + 2048);
  gPalettePtr = i32(cursor);
  cursor = aligned4(cursor + i64(budget + 1) * 16);

  const bytes = cursor - base;
  const end = base + bytes;
  if (bytes <= 0 || bytes > i32.MAX_VALUE || end > i32.MAX_VALUE) return false;

  gPixelCount = count;
  gBudget = budget;
  gRequiredBytes = i32(bytes);
  return true;
}

export function abiVersion(): i32 {
  return ABI_VERSION;
}

/** Host-owned arena base above AssemblyScript static data. */
export function arenaBase(): i32 {
  return i32(__heap_base);
}

export function requiredBytes(width: i32, height: i32, markerBudget: i32): i32 {
  return configureLayout(width, height, markerBudget) ? gRequiredBytes : -1;
}

/** The only function allowed to grow linear memory. */
export function ensureCapacity(bytes: i32): i32 {
  if (bytes <= 0) return 0;
  const end = i64(arenaBase()) + bytes;
  if (end <= 0 || end > i32.MAX_VALUE) return 0;
  const pagesNeeded = i32((end + 0xffff) >> 16);
  const currentPages = memory.size();
  if (pagesNeeded > currentPages) {
    if (memory.grow(pagesNeeded - currentPages) < 0) return 0;
  }
  return 1;
}

export function inputRgbaPtr(width: i32, height: i32, markerBudget: i32): i32 {
  return configureLayout(width, height, markerBudget) ? gInputPtr : -1;
}

export function focusTierPtr(width: i32, height: i32, markerBudget: i32): i32 {
  return configureLayout(width, height, markerBudget) ? gFocusPtr : -1;
}

export function outputRgbaPtr(width: i32, height: i32, markerBudget: i32): i32 {
  return configureLayout(width, height, markerBudget) ? gInputPtr : -1;
}

export function status(): i32 {
  return gStatus;
}

export function markerCount(): i32 {
  return gMarkerCount;
}

export function faceMarkerCount(): i32 {
  return gFaceMarkerCount;
}

export function bodyMarkerCount(): i32 {
  return gBodyMarkerCount;
}

export function backgroundMarkerCount(): i32 {
  return gBackgroundMarkerCount;
}

function resetMetadata(): void {
  gStatus = STATUS_OK;
  gMarkerCount = 0;
  gFaceMarkerCount = 0;
  gBodyMarkerCount = 0;
  gBackgroundMarkerCount = 0;
}

@inline
function finiteOrZero(value: f64): f64 {
  return value == value && value != Infinity && value != -Infinity ? value : 0.0;
}

@inline
function roundedMean(sum: u32, population: u32): u8 {
  return u8((sum + (population >> 1)) / population);
}

function validateFocusMap(): bool {
  for (let index: i32 = 0; index < gPixelCount; index += 1) {
    if (load<u8>(gFocusPtr + index) > TIER_BACKGROUND) return false;
  }
  return true;
}

function grayscaleAndDifference(width: i32, height: i32, blurRadius: i32): void {
  const count = gPixelCount;
  for (let index: i32 = 0, offset: i32 = 0; index < count; index += 1, offset += 4) {
    const red = i32(load<u8>(gInputPtr + offset));
    const green = i32(load<u8>(gInputPtr + offset + 1));
    const blue = i32(load<u8>(gInputPtr + offset + 2));
    store<u8>(gGrayPtr + index, u8((77 * red + 150 * green + 29 * blue + 128) >> 8));
  }

  const radius = clampI32(blurRadius, 1, 64);
  for (let y: i32 = 0; y < height; y += 1) {
    const row = y * width;
    let sum: i32 = 0;
    let right = minI32(width - 1, radius);
    for (let x: i32 = 0; x <= right; x += 1) sum += load<u8>(gGrayPtr + row + x);

    for (let x: i32 = 0; x < width; x += 1) {
      const left = maxI32(0, x - radius);
      right = minI32(width - 1, x + radius);
      const divisor = right - left + 1;
      const average = (sum + (divisor >> 1)) / divisor;
      store<u16>(gHorizontalPtr + (row + x) * 2, u16(average));
      const remove = x - radius;
      const add = x + radius + 1;
      if (remove >= 0) sum -= load<u8>(gGrayPtr + row + remove);
      if (add < width) sum += load<u8>(gGrayPtr + row + add);
    }
  }

  for (let x: i32 = 0; x < width; x += 1) {
    let sum: i32 = 0;
    let bottom = minI32(height - 1, radius);
    for (let y: i32 = 0; y <= bottom; y += 1) {
      sum += load<u16>(gHorizontalPtr + (y * width + x) * 2);
    }
    for (let y: i32 = 0; y < height; y += 1) {
      const top = maxI32(0, y - radius);
      bottom = minI32(height - 1, y + radius);
      const divisor = bottom - top + 1;
      const blurred = (sum + (divisor >> 1)) / divisor;
      const index = y * width + x;
      const gray = i32(load<u8>(gGrayPtr + index));
      store<u8>(gScratchPtr + index, u8(abs(gray - blurred)));
      const remove = y - radius;
      const add = y + radius + 1;
      if (remove >= 0) sum -= load<u16>(gHorizontalPtr + (remove * width + x) * 2);
      if (add < height) sum += load<u16>(gHorizontalPtr + (add * width + x) * 2);
    }
  }
}

function sobelGradients(sourcePtr: i32, width: i32, height: i32): void {
  memory.fill(gGradientXPtr, 0, gPixelCount * 2);
  memory.fill(gGradientYPtr, 0, gPixelCount * 2);
  for (let y: i32 = 1; y < height - 1; y += 1) {
    const row = y * width;
    for (let x: i32 = 1; x < width - 1; x += 1) {
      const index = row + x;
      const topLeft = i32(load<u8>(sourcePtr + index - width - 1));
      const top = i32(load<u8>(sourcePtr + index - width));
      const topRight = i32(load<u8>(sourcePtr + index - width + 1));
      const left = i32(load<u8>(sourcePtr + index - 1));
      const right = i32(load<u8>(sourcePtr + index + 1));
      const bottomLeft = i32(load<u8>(sourcePtr + index + width - 1));
      const bottom = i32(load<u8>(sourcePtr + index + width));
      const bottomRight = i32(load<u8>(sourcePtr + index + width + 1));
      const gx = -topLeft + topRight - 2 * left + 2 * right - bottomLeft + bottomRight;
      const gy = -topLeft - 2 * top - topRight + bottomLeft + 2 * bottom + bottomRight;
      store<i16>(gGradientXPtr + index * 2, i16(gx));
      store<i16>(gGradientYPtr + index * 2, i16(gy));
    }
  }
}

function shiTomasiScores(width: i32, height: i32): void {
  sobelGradients(gScratchPtr, width, height);
  memory.fill(gScoresPtr, 0, gPixelCount * 4);
  for (let y: i32 = 2; y < height - 2; y += 1) {
    for (let x: i32 = 2; x < width - 2; x += 1) {
      let xx: i32 = 0;
      let xy: i32 = 0;
      let yy: i32 = 0;
      for (let dy: i32 = -1; dy <= 1; dy += 1) {
        let index = (y + dy) * width + x - 1;
        for (let dx: i32 = -1; dx <= 1; dx += 1, index += 1) {
          const gx = i32(load<i16>(gGradientXPtr + index * 2));
          const gy = i32(load<i16>(gGradientYPtr + index * 2));
          xx += gx * gx;
          xy += gx * gy;
          yy += gy * gy;
        }
      }
      const a = f64(xx);
      const b = f64(xy);
      const c = f64(yy);
      const trace = a + c;
      const discriminant = Math.sqrt((a - c) * (a - c) + 4.0 * b * b);
      const score = max<f64>(0.0, (trace - discriminant) * 0.5);
      store<f32>(gScoresPtr + (y * width + x) * 4, f32(score));
    }
  }
}

@inline
function isLocalMaximum(index: i32, width: i32): bool {
  const score = load<f32>(gScoresPtr + index * 4);
  for (let dy: i32 = -1; dy <= 1; dy += 1) {
    for (let dx: i32 = -1; dx <= 1; dx += 1) {
      if (dx == 0 && dy == 0) continue;
      const neighbor = index + dy * width + dx;
      const neighborScore = load<f32>(gScoresPtr + neighbor * 4);
      if (neighborScore > score || (neighborScore == score && neighbor < index)) return false;
    }
  }
  return true;
}

@inline
function candidateAt(position: i32): i32 {
  return i32(load<u32>(gCandidatesPtr + position * 4));
}

@inline
function storeCandidate(position: i32, index: i32): void {
  store<u32>(gCandidatesPtr + position * 4, u32(index));
}

@inline
function candidateIsWorse(leftIndex: i32, rightIndex: i32): bool {
  const leftScore = load<f32>(gScoresPtr + leftIndex * 4);
  const rightScore = load<f32>(gScoresPtr + rightIndex * 4);
  return leftScore < rightScore || (leftScore == rightScore && leftIndex > rightIndex);
}

function siftWorst(root: i32, endExclusive: i32): void {
  let current = root;
  while (true) {
    let child = current * 2 + 1;
    if (child >= endExclusive) return;
    if (child + 1 < endExclusive && candidateIsWorse(candidateAt(child + 1), candidateAt(child))) {
      child += 1;
    }
    const currentIndex = candidateAt(current);
    const childIndex = candidateAt(child);
    if (!candidateIsWorse(childIndex, currentIndex)) return;
    storeCandidate(current, childIndex);
    storeCandidate(child, currentIndex);
    current = child;
  }
}

/** Heap sort into the oracle's total order: score descending, index ascending. */
function sortCandidates(count: i32): void {
  let start = count >> 1;
  while (start > 0) {
    start -= 1;
    siftWorst(start, count);
  }
  let end = count;
  while (end > 1) {
    const first = candidateAt(0);
    const last = candidateAt(end - 1);
    storeCandidate(0, last);
    storeCandidate(end - 1, first);
    end -= 1;
    siftWorst(0, end);
  }
}

function candidatesForTier(tier: u8, width: i32, height: i32, spacing: i32): i32 {
  memory.fill(gSeenPtr, 0, gPixelCount);
  let candidateCount: i32 = 0;

  for (let y: i32 = 2; y < height - 2; y += 1) {
    for (let x: i32 = 2; x < width - 2; x += 1) {
      const index = y * width + x;
      if (
        load<f32>(gScoresPtr + index * 4) > 0.0 &&
        load<u8>(gFocusPtr + index) == tier &&
        isLocalMaximum(index, width)
      ) {
        storeCandidate(candidateCount, index);
        candidateCount += 1;
        store<u8>(gSeenPtr + index, 1);
      }
    }
  }

  const cell = maxI32(2, spacing);
  for (let tileY: i32 = 0; tileY < height; tileY += cell) {
    for (let tileX: i32 = 0; tileX < width; tileX += cell) {
      let bestIndex: i32 = -1;
      let bestScore: f32 = -1.0;
      const yEnd = minI32(height, tileY + cell);
      const xEnd = minI32(width, tileX + cell);
      for (let y: i32 = tileY; y < yEnd; y += 1) {
        for (let x: i32 = tileX; x < xEnd; x += 1) {
          const index = y * width + x;
          if (load<u8>(gFocusPtr + index) != tier) continue;
          const score = load<f32>(gScoresPtr + index * 4);
          if (score > bestScore || (score == bestScore && index < bestIndex)) {
            bestIndex = index;
            bestScore = score;
          }
        }
      }
      if (bestIndex >= 0 && load<u8>(gSeenPtr + bestIndex) == 0) {
        storeCandidate(candidateCount, bestIndex);
        candidateCount += 1;
        store<u8>(gSeenPtr + bestIndex, 1);
      }
    }
  }

  sortCandidates(candidateCount);
  return candidateCount;
}

function blockDiamond(index: i32, width: i32, height: i32, radius: i32): void {
  const centerY = index / width;
  const centerX = index - centerY * width;
  for (let dy: i32 = -radius; dy <= radius; dy += 1) {
    const y = centerY + dy;
    if (y < 0 || y >= height) continue;
    const horizontal = radius - abs(dy);
    for (let dx: i32 = -horizontal; dx <= horizontal; dx += 1) {
      const x = centerX + dx;
      if (x >= 0 && x < width) store<u8>(gBlockedPtr + y * width + x, 1);
    }
  }
}

function tierQuotas(markerBudget: i32, faceQuota: f64, bodyQuota: f64, backgroundQuota: f64): void {
  let face = max<f64>(0.0, finiteOrZero(faceQuota));
  let body = max<f64>(0.0, finiteOrZero(bodyQuota));
  let background = max<f64>(0.0, finiteOrZero(backgroundQuota));
  let sum = face + body + background;
  if (sum <= 0.0) {
    face = 0.5;
    body = 0.3;
    background = 0.2;
    sum = 1.0;
  }
  face /= sum;
  body /= sum;
  background /= sum;

  const exactFace = f64(markerBudget) * face;
  const exactBody = f64(markerBudget) * body;
  const exactBackground = f64(markerBudget) * background;
  let faceCount = i32(Math.floor(exactFace));
  let bodyCount = i32(Math.floor(exactBody));
  let backgroundCount = i32(Math.floor(exactBackground));
  const faceRemainder = exactFace - faceCount;
  const bodyRemainder = exactBody - bodyCount;
  const backgroundRemainder = exactBackground - backgroundCount;
  let remaining = markerBudget - faceCount - bodyCount - backgroundCount;
  let faceUsed = false;
  let bodyUsed = false;
  let backgroundUsed = false;

  while (remaining > 0) {
    let best: i32 = -1;
    let bestRemainder: f64 = -1.0;
    if (!faceUsed) {
      best = 0;
      bestRemainder = faceRemainder;
    }
    if (!bodyUsed && bodyRemainder > bestRemainder) {
      best = 1;
      bestRemainder = bodyRemainder;
    }
    if (!backgroundUsed && backgroundRemainder > bestRemainder) {
      best = 2;
    }
    if (best == 0) {
      faceCount += 1;
      faceUsed = true;
    } else if (best == 1) {
      bodyCount += 1;
      bodyUsed = true;
    } else if (best == 2) {
      backgroundCount += 1;
      backgroundUsed = true;
    } else {
      break;
    }
    remaining -= 1;
  }

  // Before marker selection these globals hold the requested per-tier caps.
  gFaceMarkerCount = faceCount;
  gBodyMarkerCount = bodyCount;
  gBackgroundMarkerCount = backgroundCount;
}

function selectMarkers(
  width: i32,
  height: i32,
  markerBudget: i32,
  faceQuota: f64,
  bodyQuota: f64,
  backgroundQuota: f64,
  faceSpacing: i32,
  bodySpacing: i32,
  backgroundSpacing: i32,
): i32 {
  tierQuotas(markerBudget, faceQuota, bodyQuota, backgroundQuota);
  const faceCap = gFaceMarkerCount;
  const bodyCap = gBodyMarkerCount;
  const backgroundCap = gBackgroundMarkerCount;
  gFaceMarkerCount = 0;
  gBodyMarkerCount = 0;
  gBackgroundMarkerCount = 0;
  memory.fill(gBlockedPtr, 0, gPixelCount);
  let selected: i32 = 0;

  for (let tier: i32 = 0; tier < 3; tier += 1) {
    const spacing = tier == 0
      ? clampI32(faceSpacing, 1, 64)
      : tier == 1
        ? clampI32(bodySpacing, 1, 64)
        : clampI32(backgroundSpacing, 1, 64);
    const cap = tier == 0 ? faceCap : tier == 1 ? bodyCap : backgroundCap;
    const candidateCount = candidatesForTier(u8(tier), width, height, spacing);
    let tierCount: i32 = 0;
    for (let position: i32 = 0; position < candidateCount && tierCount < cap; position += 1) {
      const index = candidateAt(position);
      if (load<u8>(gBlockedPtr + index) != 0) continue;
      store<u32>(gMarkersPtr + selected * 4, u32(index));
      selected += 1;
      tierCount += 1;
      blockDiamond(index, width, height, spacing);
    }
    if (tier == 0) gFaceMarkerCount = tierCount;
    else if (tier == 1) gBodyMarkerCount = tierCount;
    else gBackgroundMarkerCount = tierCount;
  }

  if (selected == 0) {
    store<u32>(gMarkersPtr, u32(gPixelCount >> 1));
    selected = 1;
    gBackgroundMarkerCount = 1;
  }
  return selected;
}

function luminanceGradient(width: i32, height: i32): void {
  memory.fill(gScratchPtr, 0, gPixelCount);
  for (let y: i32 = 1; y < height - 1; y += 1) {
    const row = y * width;
    for (let x: i32 = 1; x < width - 1; x += 1) {
      const index = row + x;
      const topLeft = i32(load<u8>(gGrayPtr + index - width - 1));
      const top = i32(load<u8>(gGrayPtr + index - width));
      const topRight = i32(load<u8>(gGrayPtr + index - width + 1));
      const left = i32(load<u8>(gGrayPtr + index - 1));
      const right = i32(load<u8>(gGrayPtr + index + 1));
      const bottomLeft = i32(load<u8>(gGrayPtr + index + width - 1));
      const bottom = i32(load<u8>(gGrayPtr + index + width));
      const bottomRight = i32(load<u8>(gGrayPtr + index + width + 1));
      const gx = -topLeft + topRight - 2 * left + 2 * right - bottomLeft + bottomRight;
      const gy = -topLeft - 2 * top - topRight + bottomLeft + 2 * bottom + bottomRight;
      store<u8>(gScratchPtr + index, u8(minI32(255, (abs(gx) + abs(gy) + 2) >> 2)));
    }
  }
}

@inline
function enqueue(index: i32, level: i32, headsPtr: i32, tailsPtr: i32, nextPtr: i32): void {
  const headAddress = headsPtr + level * 4;
  const tailAddress = tailsPtr + level * 4;
  const head = load<i32>(headAddress);
  if (head < 0) store<i32>(headAddress, index);
  else store<i32>(nextPtr + load<i32>(tailAddress) * 4, index);
  store<i32>(tailAddress, index);
}

@inline
function neighborX(neighbor: i32): i32 {
  if (neighbor == 0 || neighbor == 3 || neighbor == 5) return -1;
  if (neighbor == 2 || neighbor == 4 || neighbor == 7) return 1;
  return 0;
}

@inline
function neighborY(neighbor: i32): i32 {
  if (neighbor < 3) return -1;
  if (neighbor > 4) return 1;
  return 0;
}

function watershed(width: i32, height: i32, selectedCount: i32): i32 {
  // Reuse score storage for labels, seen storage for queued state, and the
  // candidate buffer for intrusive queue links.
  const labelsPtr = gScoresPtr;
  const queuedPtr = gSeenPtr;
  const nextPtr = gCandidatesPtr;
  const headsPtr = gBucketsPtr;
  const tailsPtr = gBucketsPtr + 256 * 4;
  memory.fill(labelsPtr, 0, gPixelCount * 4);
  memory.fill(queuedPtr, 0, gPixelCount);
  memory.fill(nextPtr, 0xff, gPixelCount * 4);
  memory.fill(headsPtr, 0xff, 256 * 4);
  memory.fill(tailsPtr, 0xff, 256 * 4);

  let regionCount: i32 = 0;
  for (let marker: i32 = 0; marker < selectedCount; marker += 1) {
    const index = i32(load<u32>(gMarkersPtr + marker * 4));
    if (index < 0 || index >= gPixelCount || load<u8>(queuedPtr + index) != 0) continue;
    regionCount += 1;
    store<u32>(labelsPtr + index * 4, u32(regionCount));
    store<u8>(queuedPtr + index, 1);
    enqueue(index, 0, headsPtr, tailsPtr, nextPtr);
  }
  if (regionCount == 0) {
    const center = gPixelCount >> 1;
    regionCount = 1;
    store<u32>(labelsPtr + center * 4, 1);
    store<u8>(queuedPtr + center, 1);
    enqueue(center, 0, headsPtr, tailsPtr, nextPtr);
  }

  let level: i32 = 0;
  let visited = regionCount;
  while (visited < gPixelCount) {
    while (level < 256 && load<i32>(headsPtr + level * 4) < 0) level += 1;
    if (level >= 256) break;
    const headAddress = headsPtr + level * 4;
    const index = load<i32>(headAddress);
    const next = load<i32>(nextPtr + index * 4);
    store<i32>(headAddress, next);
    if (next < 0) store<i32>(tailsPtr + level * 4, -1);
    store<i32>(nextPtr + index * 4, -1);
    const y = index / width;
    const x = index - y * width;
    const label = load<u32>(labelsPtr + index * 4);

    for (let neighbor: i32 = 0; neighbor < 8; neighbor += 1) {
      const nx = x + neighborX(neighbor);
      const ny = y + neighborY(neighbor);
      if (nx < 0 || nx >= width || ny < 0 || ny >= height) continue;
      const nextIndex = ny * width + nx;
      if (load<u8>(queuedPtr + nextIndex) != 0) continue;
      store<u8>(queuedPtr + nextIndex, 1);
      store<u32>(labelsPtr + nextIndex * 4, label);
      visited += 1;
      const nextLevel = maxI32(level, i32(load<u8>(gScratchPtr + nextIndex)));
      enqueue(nextIndex, nextLevel, headsPtr, tailsPtr, nextPtr);
    }
  }

  if (visited < gPixelCount) {
    for (let index: i32 = 0; index < gPixelCount; index += 1) {
      if (load<u32>(labelsPtr + index * 4) == 0) store<u32>(labelsPtr + index * 4, 1);
    }
  }
  return regionCount;
}

function colorizeRegions(regionCount: i32): void {
  const labelsPtr = gScoresPtr;
  const redPtr = gPalettePtr;
  const greenPtr = redPtr + (gBudget + 1) * 4;
  const bluePtr = greenPtr + (gBudget + 1) * 4;
  const populationPtr = bluePtr + (gBudget + 1) * 4;
  memory.fill(gPalettePtr, 0, (gBudget + 1) * 16);

  for (let index: i32 = 0, offset: i32 = 0; index < gPixelCount; index += 1, offset += 4) {
    const label = i32(load<u32>(labelsPtr + index * 4));
    if (label <= 0 || label > regionCount) continue;
    const address = label * 4;
    store<u32>(redPtr + address, load<u32>(redPtr + address) + load<u8>(gInputPtr + offset));
    store<u32>(greenPtr + address, load<u32>(greenPtr + address) + load<u8>(gInputPtr + offset + 1));
    store<u32>(bluePtr + address, load<u32>(bluePtr + address) + load<u8>(gInputPtr + offset + 2));
    store<u32>(populationPtr + address, load<u32>(populationPtr + address) + 1);
  }

  for (let index: i32 = 0, offset: i32 = 0; index < gPixelCount; index += 1, offset += 4) {
    const label = i32(load<u32>(labelsPtr + index * 4));
    if (label <= 0 || label > regionCount) continue;
    const address = label * 4;
    const population = load<u32>(populationPtr + address);
    if (population == 0) continue;
    store<u8>(gInputPtr + offset, roundedMean(load<u32>(redPtr + address), population));
    store<u8>(gInputPtr + offset + 1, roundedMean(load<u32>(greenPtr + address), population));
    store<u8>(gInputPtr + offset + 2, roundedMean(load<u32>(bluePtr + address), population));
    // Alpha remains the source alpha, exactly like the scalar oracle.
  }
}

/**
 * Execute the complete pipeline. The host must call `ensureCapacity` and copy
 * exact-sized RGBA/focus buffers before entering this function.
 */
export function run(
  width: i32,
  height: i32,
  blurRadius: i32,
  markerBudget: i32,
  faceQuota: f64,
  bodyQuota: f64,
  backgroundQuota: f64,
  faceSpacing: i32,
  bodySpacing: i32,
  backgroundSpacing: i32,
): i32 {
  resetMetadata();
  if (!dimensionsAreValid(width, height)) {
    gStatus = STATUS_INVALID_DIMENSIONS;
    return gStatus;
  }
  if (!configureLayout(width, height, markerBudget)) {
    gStatus = STATUS_INVALID_LAYOUT;
    return gStatus;
  }
  const capacity = i64(memory.size()) << 16;
  if (i64(arenaBase()) + gRequiredBytes > capacity) {
    gStatus = STATUS_INSUFFICIENT_CAPACITY;
    return gStatus;
  }
  if (!validateFocusMap()) {
    gStatus = STATUS_INVALID_FOCUS_TIER;
    return gStatus;
  }

  grayscaleAndDifference(width, height, blurRadius);
  shiTomasiScores(width, height);
  const selectedCount = selectMarkers(
    width,
    height,
    gBudget,
    faceQuota,
    bodyQuota,
    backgroundQuota,
    faceSpacing,
    bodySpacing,
    backgroundSpacing,
  );
  luminanceGradient(width, height);
  const regions = watershed(width, height, selectedCount);
  colorizeRegions(regions);
  gMarkerCount = regions;
  gStatus = STATUS_OK;
  return gStatus;
}
