import { useEffect, useState, useRef } from "react";

/**
 * Generic hook that manages flying-receipt state for ReceiptFlowShell consumers.
 *
 * When `isTransitioning` becomes true it snapshots the next item and sets
 * `showFlying = true`.  When it becomes false a 50 ms delay clears state so
 * the incoming receipt can render before the flying copy disappears.
 *
 * The default `getNextItem` wraps with modulo; pass a custom callback for
 * pool-exhaustion / non-wrapping behaviour.
 */
export function useFlyingReceipt<T>(
  isTransitioning: boolean,
  items: T[],
  currentIndex: number,
  getNextItem?: (items: T[], idx: number) => T | null,
): { flyingItem: T | null; showFlying: boolean } {
  const [flyingItem, setFlyingItem] = useState<T | null>(null);
  const [showFlying, setShowFlying] = useState(false);

  // Keep a stable ref for the custom resolver so callers don't need useCallback
  const resolverRef = useRef(getNextItem);
  resolverRef.current = getNextItem;

  useEffect(() => {
    if (isTransitioning) {
      const resolver = resolverRef.current;
      const next = resolver
        ? resolver(items, currentIndex)
        : items.length > 0
          ? items[(currentIndex + 1) % items.length]
          : null;
      setFlyingItem(next);
      setShowFlying(true);
      return;
    }

    // Delay hiding so the next receipt can render first (prevents flash)
    const timeout = setTimeout(() => {
      setShowFlying(false);
      setFlyingItem(null);
    }, 50);
    return () => clearTimeout(timeout);
  }, [isTransitioning, currentIndex, items]);

  return { flyingItem, showFlying };
}
