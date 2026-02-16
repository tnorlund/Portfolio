import { ReceiptFlowGeometry, ReceiptQueuePosition } from "./types";

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

export interface FlyingTransformInput {
  itemWidth: number;
  itemHeight: number;
  displayWidth: number;
  leftOffset: number;
  geometry: ReceiptFlowGeometry;
}

export interface FlyingTransformResult {
  startX: number;
  startY: number;
  startScale: number;
}

export const calculateFlyingTransform = ({
  itemWidth,
  itemHeight,
  displayWidth,
  leftOffset,
  geometry,
}: FlyingTransformInput): FlyingTransformResult => {
  const safeItemWidth = Math.max(itemWidth, 1);
  const safeItemHeight = Math.max(itemHeight, 1);
  const safeDisplayWidth = Math.max(displayWidth, 1);

  const queueItemCenterX = geometry.queueItemLeftInset + leftOffset + geometry.queueItemWidth / 2;
  const distanceToQueueItemCenter =
    geometry.centerColumnWidth / 2 +
    geometry.gap +
    (geometry.queueWidth - queueItemCenterX);

  const queueItemHeight = (safeItemHeight / safeItemWidth) * geometry.queueItemWidth;
  const queueItemCenterFromTop =
    (geometry.centerColumnHeight - geometry.queueHeight) / 2 + queueItemHeight / 2;

  return {
    startX: -distanceToQueueItemCenter,
    startY: queueItemCenterFromTop - geometry.centerColumnHeight / 2,
    startScale: geometry.queueItemWidth / safeDisplayWidth,
  };
};
