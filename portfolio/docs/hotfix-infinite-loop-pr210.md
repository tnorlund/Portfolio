# Hotfix: Infinite Loop in Receipt Page (PR #210)

## Issue Description
After PR #206 (performance optimizations for ImageStack and ReceiptStack), the receipt page (`/receipt`) started experiencing "Maximum update depth exceeded" errors, causing the application to crash with an infinite render loop.

## Root Causes Identified

### 1. Performance Monitor Causing Render Loops
The `usePerformanceMonitor` hook in ReceiptStack was tracking component renders and notifying all subscribers on every render. This created a feedback loop:
- Component renders → Performance monitor tracks render → Notifies subscribers → Component re-renders → Repeat

### 2. startAnimation State in useEffect Dependencies
Both ImageStack and ReceiptStack had `startAnimation` in their useEffect dependency arrays while also updating it inside the effect:
```javascript
useEffect(() => {
  if (inView && images.length > 0 && !startAnimation) {
    setStartAnimation(true);  // Updates startAnimation
  }
}, [inView, images.length, startAnimation]); // startAnimation in deps causes re-run
```

### 3. Stale Closure Issues (Fixed)
The `loadRemainingImages` and `loadRemainingReceipts` effects were accessing array lengths directly, creating stale closures. This was fixed by using refs and functional setState.

## Changes Made

### 1. components/ui/Figures/ImageStack.tsx
- Fixed `currentSrc` dependency issue in ImageItem component
- Removed `startAnimation` from useEffect dependencies to prevent infinite loop
- Added `isLoadingRef` to prevent concurrent loading operations
- Fixed stale closure in `loadRemainingImages` effect
- Temporarily disabled render tracking

### 2. components/ui/Figures/ReceiptStack.tsx
- Fixed `currentSrc` dependency issue in ReceiptItem component
- Removed `startAnimation` from useEffect dependencies to prevent infinite loop
- Added `isLoadingRef` to prevent concurrent loading operations
- Fixed stale closure in `loadRemainingReceipts` effect
- **Disabled performance monitor** (main cause of infinite loop)
- Temporarily disabled render tracking

### 3. pages/receipt.tsx
- Fixed `uploadToS3` callback to use functional setState
- Temporarily disabled render tracking

### 4. hooks/useRenderTracker.ts (New)
- Added development-only hook for tracking excessive renders
- Helps identify components with render issues

## Temporary Measures
The following features have been temporarily disabled and need redesign:
1. Performance monitoring in ReceiptStack - needs to avoid causing re-renders
2. Render tracking hooks - for debugging only

## Testing
- Build successful with no TypeScript errors
- Receipt page loads without infinite loop errors
- Progressive image loading still functions correctly

## Future Improvements Needed
1. Redesign performance monitoring to use refs or separate context that doesn't trigger re-renders
2. Re-enable render tracking with proper safeguards
3. Add automated tests to prevent similar issues