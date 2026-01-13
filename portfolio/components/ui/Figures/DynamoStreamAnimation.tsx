import React from "react";
import { animated, to, useSprings } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

interface DynamoStreamAnimationProps {
  /** Width of the component */
  width?: number;
  /** Height of the component */
  height?: number;
  /** Duration between shard shifts in ms (default: 1000) */
  shiftInterval?: number;
  /** Number of visible shard slots (fixed at 3 for the Diagram-40 geometry) */
  shardSlots?: 3;
}

// Exact geometry from /Users/tnorlund/Diagram-40.svg (updated export)
const VIEWBOX = "0 0 300 150";
const COLOR = "#5c77ba";
const STROKE_W = 4;
const STROKE_MITER = 10;
const STROKE_JOIN = "round" as const;

// Bars (lines in SVG)
const BAR_X1 = 107.46;
const BAR_X2 = 192.46;
const BAR_Y_TOP = 34.43;
const BAR_Y_BOTTOM = 115.57;

// Shards (rects in SVG)
const SHARD_W = 19.48;
const SHARD_H = 42.72;
const SLOT0 = { x: 109.44, y: 53.47, r: 0 };
const SLOT1 = { x: 134.47, y: 53.47, r: 0 };
const SLOT2 = { x: 166.05, y: 55.57, r: 15 };

const SLOTS = [SLOT0, SLOT1, SLOT2] as const;

export default function DynamoStreamAnimation({
  width = 300,
  height = 300,
  shiftInterval = 1000,
  shardSlots = 3,
}: DynamoStreamAnimationProps) {
  const [containerRef, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  // 0 = stable, 1 = shifting
  const [offset, setOffset] = React.useState<0 | 1>(0);
  const [cycleKey, setCycleKey] = React.useState(0);
  // Which shard index is currently in slot 0 (we cycle this so the animation never "ping-pongs").
  const [lead, setLead] = React.useState(0);

  // We animate 4 shard instances:
  // - 3 visible
  // - 1 entering from the left during shift
  const numShards = shardSlots + 1;

  const rolesForLead = React.useCallback(
    (l: number) => {
      const slot0 = ((l % numShards) + numShards) % numShards;
      const slot1 = (slot0 + 1) % numShards;
      const slot2 = (slot0 + 2) % numShards;
      const hidden = (slot0 + 3) % numShards;
      return { slot0, slot1, slot2, hidden };
    },
    [numShards]
  );

  const targetFor = React.useCallback(
    (shardIndex: number, currentOffset: 0 | 1) => {
      const { slot0, slot1, slot2, hidden } = rolesForLead(lead);
      const getVisualSlot = (): number => {
        const stableMap: Record<number, number> = {
          [hidden]: -2, // hidden off-screen left
          [slot0]: 0,
          [slot1]: 1,
          [slot2]: 2,
        };

        const shiftingMap: Record<number, number> = {
          [hidden]: 0, // hidden enters slot0
          [slot0]: 1,
          [slot1]: 2,
          [slot2]: 3, // exiting
        };

        const map = currentOffset === 0 ? stableMap : shiftingMap;
        return map[shardIndex] ?? -2;
      };

      const visualSlot = getVisualSlot();

      // Default: upright geometry
      let x = SLOT0.x;
      let y = SLOT0.y;
      let baseRot = 0;
      let opacity = 1;
      let extraRot = 0;
      let scale = 1;

      if (visualSlot < 0) {
        // Hidden off-screen left
        x = BAR_X1 - 55;
        y = SLOT0.y;
        opacity = 0;
        scale = 0.85;
      } else if (visualSlot >= shardSlots) {
        // Exiting: fall off to the right/down and rotate a bit more
        const s2 = SLOTS[2];
        x = s2.x + 45;
        y = s2.y + 55;
        baseRot = s2.r;
        extraRot = 25;
        opacity = 0;
        scale = 0.8;
      } else {
        const slot = SLOTS[visualSlot as 0 | 1 | 2];
        x = slot.x;
        y = slot.y;
        baseRot = slot.r;
        opacity = 1;
        scale = 1;
      }

      return {
        x,
        y,
        opacity,
        scale,
        baseRot,
        extraRot,
        config:
          visualSlot >= shardSlots
            ? { tension: 60, friction: 14 }
            : { tension: 120, friction: 18 },
      };
    },
    [lead, rolesForLead, shardSlots]
  );

  const [springs, api] = useSprings(
    numShards,
    (i) => targetFor(i, offset),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [offset]
  );

  // Loop
  React.useEffect(() => {
    if (!inView) return;
    const id = window.setTimeout(() => {
      setOffset(1);
      window.setTimeout(() => {
        // Advance the "lead" so the right-most exited shard becomes the next hidden-left shard,
        // avoiding any ping-pong back animation.
        setLead((l) => (l + 3) % numShards);
        setOffset(0);
        setCycleKey((k) => k + 1);
      }, 800);
    }, shiftInterval);
    return () => window.clearTimeout(id);
  }, [inView, shiftInterval, cycleKey, numShards]);

  React.useEffect(() => {
    api.start((i) => targetFor(i, offset));
  }, [api, offset, targetFor]);

  // When we finish a shift, we "teleport" the new hidden shard back to the left immediately.
  React.useEffect(() => {
    if (offset !== 0) return;
    api.start((i) => ({
      ...targetFor(i, 0),
      immediate: true,
    }));
  }, [api, cycleKey, offset, targetFor]);

  return (
    <div ref={containerRef} style={{ display: "flex", justifyContent: "center" }}>
      <svg
        width={width}
        height={height}
        viewBox={VIEWBOX}
        style={{ overflow: "visible", display: "block" }}
      >
        {/* Bars */}
        <line
          x1={BAR_X1}
          y1={BAR_Y_TOP}
          x2={BAR_X2}
          y2={BAR_Y_TOP}
          fill="none"
          stroke={COLOR}
          strokeWidth={STROKE_W}
          strokeMiterlimit={STROKE_MITER}
        />
        <line
          x1={BAR_X1}
          y1={BAR_Y_BOTTOM}
          x2={BAR_X2}
          y2={BAR_Y_BOTTOM}
          fill="none"
          stroke={COLOR}
          strokeWidth={STROKE_W}
          strokeMiterlimit={STROKE_MITER}
        />

        {/* Shards (stroke-only, like the SVG; interior is naturally empty) */}
        {springs.map((spring, i) => (
          <animated.g
            key={`shard-${cycleKey}-${i}`}
            opacity={spring.opacity}
            transform={to(
              [spring.x, spring.y, spring.scale, spring.baseRot, spring.extraRot],
              (x, y, s, br, er) => {
                const cx = SHARD_W / 2;
                const cy = SHARD_H / 2;
                const ang = br + er;
                // Rotate + scale about the rect center without scaling the translation:
                // T(x,y) · T(cx,cy) · R(ang) · S(s) · T(-cx,-cy)
                return `translate(${x} ${y}) translate(${cx} ${cy}) rotate(${ang}) scale(${s}) translate(${-cx} ${-cy})`;
              }
            )}
          >
            <rect
              x={0}
              y={0}
              width={SHARD_W}
              height={SHARD_H}
              fill="none"
              stroke={COLOR}
              strokeWidth={STROKE_W}
              strokeLinejoin={STROKE_JOIN}
              strokeMiterlimit={STROKE_MITER}
            />
          </animated.g>
        ))}
      </svg>
    </div>
  );
}

