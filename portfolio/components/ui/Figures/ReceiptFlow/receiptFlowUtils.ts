import { ReceiptQueuePosition } from "./types";

type MotionScaleWindow = Window & {
  __RECEIPT_MOTION_SCALE?: number | string;
};

const DEFAULT_MOTION_SCALE = 1;
const MAX_MOTION_SCALE = 12;

export const getReceiptMotionScale = (): number => {
  if (typeof window === "undefined") {
    return DEFAULT_MOTION_SCALE;
  }

  const queryValue = new URLSearchParams(window.location.search).get("receiptMotionScale");
  const globalValue = (window as MotionScaleWindow).__RECEIPT_MOTION_SCALE;
  const rawValue = queryValue ?? globalValue;
  const scale = typeof rawValue === "string" ? Number(rawValue) : rawValue;

  return typeof scale === "number" && Number.isFinite(scale) && scale > 0
    ? Math.min(scale, MAX_MOTION_SCALE)
    : DEFAULT_MOTION_SCALE;
};

export const getQueuePosition = (receiptId: string): ReceiptQueuePosition => {
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;

  return {
    rotation: Math.abs(random1) * 24 - 12,
    leftOffset: Math.abs(random2) * 10 - 5,
  };
};

export const getVisibleQueueIndices = (
  totalReceipts: number,
  currentIndex: number,
  maxVisible: number,
  wrap: boolean
): number[] => {
  if (totalReceipts <= 0) {
    return [];
  }

  const indices: number[] = [];
  for (let i = 1; i <= maxVisible; i += 1) {
    const idx = currentIndex + i;
    if (wrap) {
      indices.push(idx % totalReceipts);
    } else if (idx < totalReceipts) {
      indices.push(idx);
    }
  }

  return indices;
};
