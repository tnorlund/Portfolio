export interface ImageFormatSupport {
  supportsWebP: boolean;
  supportsAVIF: boolean;
}

export interface ReceiptQueuePosition {
  rotation: number;
  leftOffset: number;
}

export interface ReceiptFlowGeometry {
  queueItemWidth: number;
  queueWidth: number;
  queueHeight: number;
  queueItemLeftInset: number;
  centerColumnWidth: number;
  centerColumnHeight: number;
  gap: number;
}
