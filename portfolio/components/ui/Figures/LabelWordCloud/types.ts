export interface LabelNode {
  label: string;
  displayLines: string[];
  validCount: number;
  radius: number;        // Circle radius based on count
  fontSize: number;      // Font size (scales with radius)
  textFitsInside: boolean | null; // Whether text fits inside the circle (null = needs measurement)
  x: number;
  y: number;
  vx: number;
  vy: number;
  // Leader line properties (only used when textFitsInside is false)
  leaderAngle?: number;  // Stable angle for leader line (set after settling)
  leaderLength?: number; // Length of leader line
}

export interface LabelWordCloudProps {
  width?: number;
  height?: number;
}
