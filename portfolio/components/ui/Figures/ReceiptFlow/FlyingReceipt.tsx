import React, { useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { getQueuePosition } from "./receiptFlowUtils";
import styles from "./FlyingReceipt.module.css";

// SSR-safe useLayoutEffect
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? React.useLayoutEffect : React.useEffect;

// Fly-in duration. Must stay comfortably below the consumers' transition timer
// (TRANSITION_DURATION ~600ms) so the receipt reaches its exact resting target
// and holds there before the flying copy is swapped for the static receipt.
const FLY_DURATION_MS = 500;
// Decelerating ease — keeps the spring-like "lands softly" feel while
// guaranteeing the animation reaches its exact endpoint at FLY_DURATION_MS.
const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);

export interface FlyingReceiptProps {
  imageUrl: string;
  displayWidth: number;
  displayHeight: number;
  /** Used for deterministic rotation + leftOffset via getQueuePosition */
  receiptId: string;
  /** Width of queue thumbnails (default 100) */
  queueItemWidth?: number;
  /** Left inset inside the queue pane (default 10; LabelValidation uses 90) */
  queueItemLeftInset?: number;
  /** Border around the image (default 1) */
  borderWidth?: number;
  onImageError?: React.ReactEventHandler<HTMLImageElement>;
}

/**
 * Shared flying-receipt animation component.
 *
 * Uses DOM measurement (via data-attributes on ReceiptFlowShell) to compute
 * the exact starting position rather than relying on geometry constants.
 *
 * Spring config uses clamp: true to prevent overshoot/bounce.
 */
export const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  imageUrl,
  displayWidth,
  displayHeight,
  receiptId,
  queueItemWidth = 100,
  queueItemLeftInset = 10,
  // borderWidth is kept in the props interface for API compatibility; the
  // 1px border is now applied purely in CSS (.flyingReceipt).
  onImageError,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  const { rotation, leftOffset } = getQueuePosition(receiptId);

  // Starting transform used before the DOM has been measured. It is computed
  // purely from props so the spring can be *initialized* with it below. This is
  // what prevents the one-frame "pop-in": without it the spring starts at the
  // final centered/full-size state and the browser paints that for a frame
  // before react-spring's rAF scheduler applies the real queue start position.
  const fallbackFrom = {
    x: -300,
    y: -50,
    scale: queueItemWidth / Math.max(displayWidth, 1),
    rotate: rotation,
  };

  // Compute the starting transform by measuring the DOM
  const computeFrom = (): { x: number; y: number; scale: number; rotate: number } => {
    const fallback = fallbackFrom;

    if (typeof window === "undefined" || !containerRef.current) return fallback;

    const shell = containerRef.current.closest("[data-rf-shell]");
    if (!shell) return fallback;

    const queuePane = shell.querySelector("[data-rf-queue]");
    const target = shell.querySelector("[data-rf-target]");
    if (!queuePane || !target) return fallback;

    const targetRect = target.getBoundingClientRect();
    // Target center: center of the flying container
    const targetCenterX = targetRect.left + targetRect.width / 2;
    const targetCenterY = targetRect.top + targetRect.height / 2;

    // Launch from the actual top card of the stack when it's available, so the
    // flying receipt lifts off exactly where the thumbnail sits. The card's
    // rendered height is far taller than queueItemWidth, so the old
    // square-item estimate (queueRect.top + queueItemWidth/2) started the
    // flight well above the card.
    const topCard = queuePane.firstElementChild;
    if (topCard) {
      const cardRect = topCard.getBoundingClientRect();
      return {
        x: cardRect.left + cardRect.width / 2 - targetCenterX,
        y: cardRect.top + cardRect.height / 2 - targetCenterY,
        scale: queueItemWidth / Math.max(displayWidth, 1),
        rotate: rotation,
      };
    }

    // Fallback: estimate the source from queue-pane geometry.
    const queueRect = queuePane.getBoundingClientRect();
    const sourceCenterX = queueRect.left + queueItemLeftInset + leftOffset + queueItemWidth / 2;
    const sourceCenterY = queueRect.top + queueItemWidth / 2; // top of queue stack

    return {
      x: sourceCenterX - targetCenterX,
      y: sourceCenterY - targetCenterY,
      scale: queueItemWidth / Math.max(displayWidth, 1),
      rotate: rotation,
    };
  };

  // Initialize the spring AT the start position (not the final centered state)
  // and fully transparent. Because react-spring flushes through its own rAF
  // scheduler rather than synchronously in useLayoutEffect, whatever we set
  // here is what the browser paints on the first frame — starting from the
  // queue position + opacity 0 guarantees no full-size center flash even if the
  // measured-DOM correction lands a frame later.
  //
  // A duration-based ease (not a physics spring) is used deliberately: the
  // consumer swaps the flying copy for the resting receipt on a fixed timer
  // (~600ms). A physics spring only asymptotes toward its target, so at the
  // swap it is still ~0.1% short (≈0.5px off-center, scale 0.999), and that
  // last fraction snaps into place when the resting receipt is revealed — a
  // subtle "pop". FLY_DURATION_MS is comfortably shorter than the swap timer,
  // so the receipt reaches its EXACT target and holds there before the handoff.
  const [springValues, api] = useSpring(() => ({
    ...fallbackFrom,
    opacity: 0,
    config: { duration: FLY_DURATION_MS, easing: easeOutCubic },
  }));

  useIsomorphicLayoutEffect(() => {
    const from = computeFrom();
    // Snap to the measured start position while still hidden...
    api.set({ ...from, opacity: 0 });
    // ...then fly to center and fade in together.
    api.start({ to: { x: 0, y: 0, scale: 1, rotate: 0, opacity: 1 } });
  }, [receiptId]);

  return (
    <animated.div
      ref={containerRef}
      className={styles.flyingReceipt}
      style={{
        transform: to(
          [springValues.x, springValues.y, springValues.scale, springValues.rotate],
          (xVal, yVal, scaleVal, rotateVal) =>
            `translate(${xVal}px, ${yVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`,
        ),
        opacity: springValues.opacity,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl}
        alt="Flying receipt"
        className={styles.flyingReceiptImage}
        // Width drives size; height comes from aspect-ratio so the frame stays
        // the receipt's aspect ratio when max-width clamps it at narrow widths.
        style={{ width: displayWidth, aspectRatio: `${displayWidth} / ${displayHeight}` }}
        onError={onImageError}
      />
    </animated.div>
  );
};
