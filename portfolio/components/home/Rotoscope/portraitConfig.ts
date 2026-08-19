import type { RotoscopeOptions } from "./algorithm";

/** Full-viewport 4:3 pass. 960×720 matches the authored portrait files. */
export const PORTRAIT_PROCESSING_SIZE = { width: 960, height: 720 } as const;

/**
 * Authored geometry for `/rotoscope-portrait.*` after its full-frame 4:3 resize.
 * Dense face spacing keeps the homepage from looking like a stretched 480×360
 * splotch. Engine defaults stay in `algorithm.ts`; this object is homepage-only.
 */
export const PORTRAIT_ROTOSCOPE_OPTIONS: Partial<RotoscopeOptions> = {
  blurRadius: 6,
  markerBudget: 1600,
  quotas: { face: 0.7, body: 0.22, background: 0.08 },
  spacing: { face: 1, body: 4, background: 8 },
  focus: {
    face: {
      centerX: 0.4,
      centerY: 0.56,
      radiusX: 0.14,
      radiusY: 0.27,
    },
    body: [
      [0.31, 0.67],
      [0.53, 0.66],
      [0.75, 0.82],
      [0.8, 1],
      [0, 1],
      [0, 0.84],
      [0.26, 0.72],
    ],
  },
};

export const PORTRAIT_SOURCES = {
  basins: "/rotoscope-basins.webp",
  avif: "/rotoscope-portrait.avif",
  webp: "/rotoscope-portrait.webp",
  fallback: "/rotoscope-portrait.jpg",
} as const;
