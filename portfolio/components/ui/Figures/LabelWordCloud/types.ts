export interface LabelNode {
  label: string;
  displayName: string;
  validCount: number;
  fontSize: number;
  width: number;
  height: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

export interface LabelWordCloudProps {
  width?: number;
  height?: number;
}
