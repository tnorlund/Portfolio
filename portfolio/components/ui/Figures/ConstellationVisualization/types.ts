/**
 * Types for ConstellationVisualization
 */

export interface ConstellationWord {
  id: string;
  text: string;
  x: number; // normalized 0-1
  y: number; // normalized 0-1
  width: number;
  height: number;
  label: string | null;
  isInConstellation: boolean;
  isFlagged: boolean;
}

export interface ConstellationMember {
  label: string;
  expected: { dx: number; dy: number }; // offset from centroid
  actual: { dx: number; dy: number };
  isFlagged: boolean;
  deviation?: number;
}

export interface ConstellationInfo {
  labels: string[]; // e.g., ["ADDRESS_LINE", "DATE", "TIME"]
  centroid: { x: number; y: number };
  members: ConstellationMember[];
  flaggedLabel: string;
  reasoning: string;
}

export interface ConstellationData {
  receipt: {
    imageId: string;
    receiptId: number;
    merchantName: string;
    words: ConstellationWord[];
  };
  constellation: ConstellationInfo;
}
