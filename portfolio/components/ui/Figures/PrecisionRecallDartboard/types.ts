export interface DartPosition {
  /** X coordinate (0-1 normalized, 0.5 = center) */
  x: number;
  /** Y coordinate (0-1 normalized, 0.5 = center) */
  y: number;
  /** Rotation angle for the dart in degrees */
  angle?: number;
}

export interface DartboardScenario {
  id: string;
  title: string;
  precision: "high" | "low";
  recall: "high" | "low";
  darts: DartPosition[];
  description: string;
}

export interface PrecisionRecallDartboardProps {
  /** Animation delay between dartboards in ms (default: 300) */
  staggerDelay?: number;
  /** Animation duration for fade-in in ms (default: 600) */
  animationDuration?: number;
  /** Whether to show titles below each dartboard (default: true) */
  showTitles?: boolean;
  /** Whether to show precision/recall axis labels (default: true) */
  showLabels?: boolean;
  /** Duration for all darts to animate in on one dartboard in ms (default: 400) */
  dartSpreadDuration?: number;
  /** Pause between dartboard dart animations in ms (default: 300) */
  dartPauseDuration?: number;
}

export interface DartboardSVGProps {
  /** Width/height of the dartboard in pixels */
  size?: number;
  /** Darts to render on the board */
  darts: DartPosition[];
  /** Whether darts should animate in */
  animateDarts?: boolean;
  /** Delay before dart animation starts in ms */
  dartAnimationDelay?: number;
  /** Duration to spread dart animations across in ms */
  dartSpreadDuration?: number;
}

export interface DartProps {
  /** X position in SVG coordinates */
  x: number;
  /** Y position in SVG coordinates */
  y: number;
  /** Rotation angle in degrees */
  angle?: number;
  /** Animation delay in ms */
  animationDelay?: number;
  /** Size of the dart */
  size?: number;
}
