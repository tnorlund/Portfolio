import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
} from "../../../utils/imageFormat";
import { boundsForLineIds } from "../../ui/Figures/GeometricReader/geometry";
import { ImageFormatSupport } from "../../ui/Figures/ReceiptFlow/types";
import styles from "./Validation.module.css";
import { OverlayMode, ValidationReceipt } from "./types";

const SECTION_COLORS: Record<string, string> = {
  ITEMS: "var(--color-yellow)",
  ITEMS_VALUE: "var(--color-yellow)",
  ITEMS_DESCRIPTION: "var(--color-yellow)",
  SECTION_HEADER: "var(--color-yellow)",
  SUMMARY: "var(--color-green)",
  TOTAL_LINE: "var(--color-green)",
  PAYMENT: "var(--color-purple)",
};

const sectionColor = (sectionType: string): string =>
  SECTION_COLORS[sectionType] ??
  "color-mix(in srgb, var(--text-color) 35%, transparent)";

const lineRectsForLineIds = (
  lines: ValidationReceipt["lines"],
  lineIds: number[],
) =>
  lineIds.flatMap((lineId) => {
    const bounds = boundsForLineIds(lines, [lineId]);
    return bounds ? [bounds] : [];
  });

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

interface ReceiptCanvasProps {
  receipt: ValidationReceipt;
  formatSupport: ImageFormatSupport | null;
  highlightLineIds: number[] | null;
  overlayMode: OverlayMode;
}

interface DragStart {
  pointerId: number;
  x: number;
  y: number;
  panX: number;
  panY: number;
}

export const ReceiptCanvas: React.FC<ReceiptCanvasProps> = ({
  receipt,
  formatSupport,
  highlightLineIds,
  overlayMode,
}) => {
  const image = receipt.image;
  const width = image?.width && image.width > 0 ? image.width : 800;
  const height = image?.height && image.height > 0 ? image.height : 1100;
  const hasImageReference = Boolean(image?.cdn_s3_key);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [imageFailed, setImageFailed] = useState(false);
  const dragRef = useRef<DragStart | null>(null);

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setImageFailed(false);
  }, [receipt.image_id, receipt.receipt_id]);

  const imageUrl = useMemo(
    () =>
      image && hasImageReference && formatSupport
        ? getBestImageUrl(image, formatSupport)
        : null,
    [formatSupport, hasImageReference, image],
  );

  const highlight = useMemo(
    () =>
      highlightLineIds && highlightLineIds.length > 0
        ? boundsForLineIds(receipt.lines, highlightLineIds)
        : null,
    [receipt.lines, highlightLineIds],
  );

  const showSections = overlayMode === "sections" || overlayMode === "both";
  const showItems = overlayMode === "items" || overlayMode === "both";
  const setClampedZoom = (next: number) =>
    setZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, next)));
  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: pan.x,
      panY: pan.y,
    };
    event.currentTarget.dataset.dragging = "true";
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    setPan({
      x: start.panX + event.clientX - start.x,
      y: start.panY + event.clientY - start.y,
    });
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    delete event.currentTarget.dataset.dragging;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const imageStatus = !hasImageReference
    ? "Image record missing"
    : imageFailed
      ? "Image asset unavailable"
      : !formatSupport
        ? "Preparing image…"
        : null;

  return (
    <div className={styles.canvasFrame} data-testid="receipt-canvas">
      <div className={styles.canvasToolbar}>
        <div className={styles.targetChips} aria-label="Receipt data coverage">
          {receipt.sections.length === 0 ? (
            <span data-kind="warning">No sections</span>
          ) : null}
          {receipt.items.length === 0 ? (
            <span data-kind="warning">No items</span>
          ) : null}
          {imageStatus ? <span data-kind="warning">{imageStatus}</span> : null}
          {!imageStatus && receipt.sections.length > 0 && receipt.items.length > 0 ? (
            <span data-kind="ready">Evidence loaded</span>
          ) : null}
        </div>
        <div className={styles.zoomControls} role="group" aria-label="Canvas zoom">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={zoom <= MIN_ZOOM}
            onClick={() => setClampedZoom(zoom - ZOOM_STEP)}
          >
            −
          </button>
          <button type="button" className={styles.zoomValue} onClick={resetView}>
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={zoom >= MAX_ZOOM}
            onClick={() => setClampedZoom(zoom + ZOOM_STEP)}
          >
            +
          </button>
          <button type="button" onClick={resetView}>
            Reset
          </button>
        </div>
      </div>

      <div
        className={styles.canvas}
        data-testid="canvas-viewport"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={resetView}
      >
        <div
          className={styles.canvasInner}
          data-testid="canvas-stage"
          style={{
            aspectRatio: `${width} / ${height}`,
            maxWidth: `${width}px`,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
          {imageUrl && !imageFailed ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageUrl}
              alt={`${receipt.merchant_name} receipt ${receipt.receipt_id}`}
              width={width}
              height={height}
              className={styles.canvasImage}
              draggable={false}
              onError={(event) => {
                if (!image) return;
                const fallback = getJpegFallbackUrl(image);
                if (event.currentTarget.src !== fallback) {
                  event.currentTarget.src = fallback;
                } else {
                  setImageFailed(true);
                }
              }}
            />
          ) : (
            <div className={styles.canvasPlaceholder} data-testid="image-placeholder">
              <strong>{imageStatus ?? "Loading image…"}</strong>
              <span>
                OCR geometry and extracted values remain available for review.
              </span>
            </div>
          )}

          <svg
            className={styles.overlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {showSections
              ? receipt.sections.map((section, sectionIndex) => {
                  const color = sectionColor(section.section_type);
                  return (
                    <g
                      key={`${section.section_type}-${sectionIndex}`}
                      data-testid="section-overlay"
                    >
                      {lineRectsForLineIds(receipt.lines, section.line_ids).map(
                        (band, index) => (
                          <rect
                            key={`${section.section_type}-${sectionIndex}-${index}`}
                            x={band.x * width}
                            y={band.y * height}
                            width={band.width * width}
                            height={band.height * height}
                            fill={color}
                            fillOpacity={0.16}
                            stroke={color}
                            strokeWidth={1}
                          />
                        ),
                      )}
                    </g>
                  );
                })
              : null}

            {showItems
              ? receipt.items.map((item) => {
                  const bounds = boundsForLineIds(receipt.lines, item.line_ids);
                  if (!bounds) return null;
                  const color = item.is_discount
                    ? "var(--color-purple)"
                    : "var(--color-blue)";
                  return (
                    <rect
                      key={`item-${item.item_index}`}
                      data-testid="item-overlay"
                      x={bounds.x * width}
                      y={bounds.y * height}
                      width={bounds.width * width}
                      height={bounds.height * height}
                      fill="none"
                      stroke={color}
                      strokeWidth={3}
                    />
                  );
                })
              : null}

            {highlight ? (
              <rect
                data-testid="hover-highlight"
                x={highlight.x * width}
                y={highlight.y * height}
                width={highlight.width * width}
                height={highlight.height * height}
                fill="var(--color-red, #e5484d)"
                fillOpacity={0.24}
                stroke="var(--color-red, #e5484d)"
                strokeWidth={5}
              />
            ) : null}
          </svg>
        </div>
      </div>
    </div>
  );
};

export default ReceiptCanvas;
