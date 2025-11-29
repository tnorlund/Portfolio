# CI/CD Loop Visualization Component

## 📋 Description

This PR adds a new animated CI/CD loop visualization component that displays the eight stages of a CI/CD pipeline (Plan, Code, Build, Test, Release, Deploy, Operate, Monitor) with sequential animations and continuous pulsing effects.

### Key Features

1. **SVG-Based Visualization**: Recreates the CI/CD loop design from Illustrator SVG with programmatic rendering for scalability
2. **Sequential Animation**: Each section animates in order (Plan → Code → Build → Test → Release → Deploy → Operate → Monitor)
3. **Continuous Pulsing**: After initial animation, sections continuously pulse to maintain visual interest
4. **Color-Coded Sections**: 
   - Plan & Monitor: Yellow (uses `var(--color-yellow)`)
   - Code & Build: Green (uses `var(--color-green)`)
   - Test & Release: Blue (uses `var(--color-blue)`)
   - Deploy & Operate: Red (uses `var(--color-red)`)
   
   Colors automatically adapt to light/dark mode via CSS variables
5. **Responsive Design**: Maintains 2:1 aspect ratio, scales appropriately for mobile and desktop
6. **Scroll-Triggered Animation**: Uses intersection observer to trigger animations when component enters viewport

## 🔄 Type of Change

- [x] ✨ New feature (non-breaking change which adds functionality)

## 🎯 Motivation

This visualization helps illustrate the CI/CD workflow described in the portfolio, making the development process more tangible and engaging for readers. The animated loop demonstrates the continuous nature of the development cycle.

## 📝 Changes Made

### New Component

- **`portfolio/components/ui/Figures/CICDLoop.tsx`**: New React component implementing the CI/CD loop visualization
  - Extracts SVG paths from Illustrator design
  - Implements sequential fade-in and scale animations using `@react-spring/web`
  - Adds continuous pulsing animation loop after initial animation completes
  - Responsive sizing based on viewport width
  - Uses `useOptimizedInView` hook for scroll-triggered animations

### Updated Files

- **`portfolio/components/ui/Figures/index.ts`**: Added export for `CICDLoop` component

## 🎨 Technical Details

### Animation System

- **Initial Animation**: Sections fade in sequentially with 200ms stagger delay
- **Continuous Animation**: After all sections are visible, they pulse every 3 seconds with staggered timing
- **Reset Behavior**: When scrolled out of view, animations reset and replay when scrolled back into view

### SVG Rendering

- Uses hardcoded SVG paths extracted from Illustrator design
- Supports both `<path>` and `<polygon>` elements
- Maintains original 200x100 viewBox aspect ratio
- Scales responsively while preserving design integrity

### Performance

- Uses `useOptimizedInView` hook with optimized intersection observer settings
- Client-side only rendering (wrapped in `ClientOnly` component)
- Efficient animation cleanup on unmount

## 🧪 Testing

- [x] Component renders correctly on desktop
- [x] Component renders correctly on mobile
- [x] Animations trigger when scrolled into view
- [x] Continuous pulsing works after initial animation
- [x] No console errors or warnings
- [x] Responsive design works across screen sizes

## 📚 Usage

```tsx
import { CICDLoop } from "../components/ui/Figures";

// Basic usage
<CICDLoop />

// With custom settings
<CICDLoop
  staggerDelay={300}      // Delay between section animations (ms)
  animationDuration={800} // Duration of each animation (ms)
/>
```

## 🎯 Future Enhancements

- Configurable text labels for each section
- Additional animation variants
- Interactive hover effects
- Click handlers for each section

## ✅ Checklist

- [x] My code follows this project's style guidelines
- [x] I have performed a self-review of my code
- [x] My changes generate no new warnings
- [x] Component is properly exported
- [x] Responsive design tested
- [x] Animations work as expected
- [x] No performance issues observed

## 📷 Visual Details

The visualization displays a continuous loop with eight sections:
- Each section is color-coded according to its function
- Sections animate in sequentially to show the flow
- Continuous pulsing maintains visual interest
- Design matches the original Illustrator SVG while being fully scalable

---

**Note**: This component is ready for use but has been removed from the receipt page in this PR. It can be easily added to any page where the CI/CD workflow visualization is needed.

