import React from "react";
import { useSprings, animated } from "@react-spring/web";
import styles from "./LockingSwimlane.module.css";

interface LockingSwimlaneProps {
  /** Optional deterministic sequence of characters for animations */
  chars?: string[];
}

const LockingSwimlane: React.FC<LockingSwimlaneProps> = ({ chars }) => {
  // Animation constants - matching actual business logic timing
  // Lock 1: ~100ms (quick validation)
  // Work: ~3000-4000ms (heavy work off-lock)
  // Lock 2: ~2000-3000ms (critical upload)
  const LOCK_1_DURATION = 2000; // 2 seconds (scaled for visibility, actual ~100ms)
  const WORK_DURATION = 4000; // 4 seconds (matches actual ~3-4s)
  const LOCK_2_DURATION = 3000; // 3 seconds (matches actual ~2-3s)
  const STAGGER = 500; // Stagger between jobs
  const CYCLE_PAUSE = 2000;

  // Layout constants
  const LANE_HEIGHT = 100;
  const LANE_1_Y = 50; // Reduced from 120 since we removed top labels
  const LANE_2_Y = LANE_1_Y + LANE_HEIGHT;
  const LANE_3_Y = LANE_2_Y + LANE_HEIGHT;
  const LOCK_X = 200; // Single lock position (shared resource)
  const WORK_START_X = 300;
  const WORK_END_X = 500;
  const SVG_WIDTH = 650;
  const SVG_HEIGHT = 350; // Reduced from 420 since we removed top padding

  // Timeline: Each job goes through Lock (quick validation) → Work (long, off-lock) → Lock (medium, upload)
  // Jobs compete for the SAME lock - only one can hold it at a time
  // Jobs wait when trying to acquire lock while another job holds it (during Lock1 or Lock2)
  const TIMELINE = React.useMemo(() => {
    // Job 1 timeline - starts first, no waiting
    const job1Lock1Start = 0;
    const job1Lock1End = job1Lock1Start + LOCK_1_DURATION;
    const job1WorkStart = job1Lock1End + STAGGER;
    const job1WorkEnd = job1WorkStart + WORK_DURATION;
    const job1Lock2Start = job1WorkEnd + STAGGER;
    const job1Lock2End = job1Lock2Start + LOCK_2_DURATION;

    // Job 2 timeline - tries to get lock at 1000ms
    // If Job 1 has lock (Lock1 or Lock2), Job 2 must wait
    // Job 2 can acquire lock after Job 1 releases Lock2
    const job2TryLockTime = 1000;
    const job2Lock1Start = job1Lock2End + STAGGER; // After Job 1 releases Lock2
    const job2Lock1End = job2Lock1Start + LOCK_1_DURATION;
    const job2WorkStart = job2Lock1End + STAGGER;
    const job2WorkEnd = job2WorkStart + WORK_DURATION;
    const job2Lock2Start = job2WorkEnd + STAGGER;
    const job2Lock2End = job2Lock2Start + LOCK_2_DURATION;

    // Job 3 timeline - tries to get lock at 2000ms
    // Must wait for both Job 1 and Job 2 to finish
    const job3TryLockTime = 2000;
    const job3Lock1Start = job2Lock2End + STAGGER; // After Job 2 releases Lock2
    const job3Lock1End = job3Lock1Start + LOCK_1_DURATION;
    const job3WorkStart = job3Lock1End + STAGGER;
    const job3WorkEnd = job3WorkStart + WORK_DURATION;
    const job3Lock2Start = job3WorkEnd + STAGGER;
    const job3Lock2End = job3Lock2Start + LOCK_2_DURATION;

    // Calculate waiting periods - Job 2 waits if Job 1 has lock when it tries
    const job2WaitStart = job2TryLockTime;
    const job2WaitEnd = job1Lock2End; // Can acquire after Job 1 releases Lock2
    const job2WaitDuration = Math.max(0, job2WaitEnd - job2WaitStart);

    // Job 3 waits if Job 1 or Job 2 has lock when it tries
    const job3WaitStart = job3TryLockTime;
    const job3WaitEnd = job2Lock2End; // Can acquire after Job 2 releases Lock2
    const job3WaitDuration = Math.max(0, job3WaitEnd - job3WaitStart);

    return [
      // Job 1 - no waiting, starts immediately
      { job: 1, phase: "lock1", start: job1Lock1Start, duration: LOCK_1_DURATION },
      { job: 1, phase: "work", start: job1WorkStart, duration: WORK_DURATION },
      { job: 1, phase: "lock2", start: job1Lock2Start, duration: LOCK_2_DURATION },
      // Job 2 - waits if Job 1 has lock, then proceeds
      { job: 2, phase: "waiting", start: job2WaitStart, duration: job2WaitDuration },
      { job: 2, phase: "lock1", start: job2Lock1Start, duration: LOCK_1_DURATION },
      { job: 2, phase: "work", start: job2WorkStart, duration: WORK_DURATION },
      { job: 2, phase: "lock2", start: job2Lock2Start, duration: LOCK_2_DURATION },
      // Job 3 - waits if Job 1 or Job 2 has lock, then proceeds
      { job: 3, phase: "waiting", start: job3WaitStart, duration: job3WaitDuration },
      { job: 3, phase: "lock1", start: job3Lock1Start, duration: LOCK_1_DURATION },
      { job: 3, phase: "work", start: job3WorkStart, duration: WORK_DURATION },
      { job: 3, phase: "lock2", start: job3Lock2Start, duration: LOCK_2_DURATION },
    ];
  }, []);

  const totalCycleTime =
    Math.max(...TIMELINE.map((t) => t.start + t.duration)) + CYCLE_PAUSE;

  const [cycle, setCycle] = React.useState(0);
  const svgRef = React.useRef<SVGSVGElement>(null);
  const [fontSize, setFontSize] = React.useState("1rem");

  // Calculate font size based on SVG's rendered size to match body text
  React.useEffect(() => {
    const updateFontSize = () => {
      if (!svgRef.current) return;

      // Get the SVG's rendered dimensions
      const rect = svgRef.current.getBoundingClientRect();
      const scaleFactor = rect.width / SVG_WIDTH;

      // Body text is 16px (1rem). We want the SVG text to render at the same visual size
      // So we need to account for the SVG's scale factor
      // If SVG is scaled to 50% width, we need 2x the font size in SVG units
      const bodyFontSizePx = 16; // 1rem = 16px at default browser size
      const svgFontSizePx = bodyFontSizePx / scaleFactor;

      // Convert to rem for consistency, but adjust for SVG scaling
      // Since SVG text is in SVG coordinate space, we use the calculated pixel size
      setFontSize(`${svgFontSizePx}px`);
    };

    updateFontSize();
    window.addEventListener("resize", updateFontSize);
    return () => window.removeEventListener("resize", updateFontSize);
  }, []);

  // Get lane Y position for a job
  const getLaneY = (job: number) => {
    if (job === 1) return LANE_1_Y;
    if (job === 2) return LANE_2_Y;
    return LANE_3_Y;
  };

  // Animate progress bars (show during work phase)
  // Use explicit API control for precise timing
  const workWidth = WORK_END_X - WORK_START_X;
  const [progressSprings, progressApi] = useSprings(3, () => ({ width: 0, opacity: 0 }));

  React.useEffect(() => {
    const timeouts: NodeJS.Timeout[] = [];

    [1, 2, 3].forEach((job) => {
      const workPhase = TIMELINE.find((t) => t.job === job && t.phase === "work");
      if (!workPhase) return;

      const jobIndex = job - 1;
      const fadeInDuration = 200;
      const fillDuration = workPhase.duration;
      const fadeOutDuration = 200;

      // Reset at cycle start
      progressApi.start((i) => {
        if (i === jobIndex) {
          return { width: 0, opacity: 0, immediate: true };
        }
        return false;
      });

      // Wait until work phase starts, then fade in
      timeouts.push(
        setTimeout(() => {
          progressApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 1, config: { duration: fadeInDuration } };
            }
            return false;
          });
        }, workPhase.start)
      );

      // Start filling after fade in
      timeouts.push(
        setTimeout(() => {
          progressApi.start((i) => {
            if (i === jobIndex) {
              return {
                width: workWidth,
                config: { duration: fillDuration, easing: (t: number) => t },
              };
            }
            return false;
          });
        }, workPhase.start + fadeInDuration)
      );

      // Fade out after fill completes
      timeouts.push(
        setTimeout(() => {
          progressApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, config: { duration: fadeOutDuration } };
            }
            return false;
          });
        }, workPhase.start + fadeInDuration + fillDuration)
      );

      // Reset after fade out
      timeouts.push(
        setTimeout(() => {
          progressApi.start((i) => {
            if (i === jobIndex) {
              return { width: 0, opacity: 0, immediate: true };
            }
            return false;
          });
        }, workPhase.start + fadeInDuration + fillDuration + fadeOutDuration)
      );
    });

    return () => {
      timeouts.forEach(clearTimeout);
    };
  }, [cycle, progressApi, TIMELINE, workWidth, totalCycleTime]);

  // Animate lock indicators - show when job holds the lock (either lock1 or lock2 phase)
  const [lockSprings, lockApi] = useSprings(3, () => ({ opacity: 0 }));

  React.useEffect(() => {
    const timeouts: NodeJS.Timeout[] = [];

    [1, 2, 3].forEach((job) => {
      const lock1Phase = TIMELINE.find((t) => t.job === job && t.phase === "lock1");
      const lock2Phase = TIMELINE.find((t) => t.job === job && t.phase === "lock2");
      if (!lock1Phase || !lock2Phase) return;

      const jobIndex = job - 1;
      const fadeInDuration = 150;
      const fadeOutDuration = 150;

      const lock1End = lock1Phase.start + lock1Phase.duration;
      const lock2Start = lock2Phase.start;
      const lock2End = lock2Start + lock2Phase.duration;

      // Reset at cycle start
      lockApi.start((i) => {
        if (i === jobIndex) {
          return { opacity: 0, immediate: true };
        }
        return false;
      });

      // Lock 1 phase - fade in
      timeouts.push(
        setTimeout(() => {
          lockApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 1, config: { duration: fadeInDuration } };
            }
            return false;
          });
        }, lock1Phase.start)
      );

      // Lock 1 phase - fade out
      timeouts.push(
        setTimeout(() => {
          lockApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, config: { duration: fadeOutDuration } };
            }
            return false;
          });
        }, lock1End - fadeOutDuration)
      );

      // Lock 2 phase - fade in
      timeouts.push(
        setTimeout(() => {
          lockApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 1, config: { duration: fadeInDuration } };
            }
            return false;
          });
        }, lock2Start)
      );

      // Lock 2 phase - fade out
      timeouts.push(
        setTimeout(() => {
          lockApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, config: { duration: fadeOutDuration } };
            }
            return false;
          });
        }, lock2End - fadeOutDuration)
      );

      // Reset after lock2 completes
      timeouts.push(
        setTimeout(() => {
          lockApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, immediate: true };
            }
            return false;
          });
        }, lock2End)
      );
    });

    return () => {
      timeouts.forEach(clearTimeout);
    };
  }, [cycle, lockApi, TIMELINE, totalCycleTime]);

  // Animate waiting indicators - show when job is waiting for the lock (only when NOT holding lock)
  const [waitingSprings, waitingApi] = useSprings(3, () => ({ opacity: 0 }));

  React.useEffect(() => {
    const timeouts: NodeJS.Timeout[] = [];

    [1, 2, 3].forEach((job) => {
      const waitingPhase = TIMELINE.find((t) => t.job === job && t.phase === "waiting");
      const lock1Phase = TIMELINE.find((t) => t.job === job && t.phase === "lock1");

      if (!waitingPhase) return;

      const jobIndex = job - 1;
      const fadeInDuration = 200;
      const fadeOutDuration = 200;

      // Clock should disappear 100ms before lock1 starts (or at end of waiting phase if no lock1)
      const clockEndTime = lock1Phase ? lock1Phase.start - 100 : waitingPhase.start + waitingPhase.duration;
      const fadeOutStart = clockEndTime - fadeOutDuration; // Start fade out so it completes at clockEndTime

      // Reset at cycle start
      waitingApi.start((i) => {
        if (i === jobIndex) {
          return { opacity: 0, immediate: true };
        }
        return false;
      });

      // Wait until waiting phase starts, then fade in
      timeouts.push(
        setTimeout(() => {
          waitingApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0.7, config: { duration: fadeInDuration } };
            }
            return false;
          });
        }, waitingPhase.start)
      );

      // Start fade out before lock appears
      timeouts.push(
        setTimeout(() => {
          waitingApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, config: { duration: fadeOutDuration } };
            }
            return false;
          });
        }, fadeOutStart)
      );

      // Reset after fade out completes
      timeouts.push(
        setTimeout(() => {
          waitingApi.start((i) => {
            if (i === jobIndex) {
              return { opacity: 0, immediate: true };
            }
            return false;
          });
        }, clockEndTime)
      );
    });

    return () => {
      timeouts.forEach(clearTimeout);
    };
  }, [cycle, waitingApi, TIMELINE, totalCycleTime]);

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <div className={styles.container}>
        <svg
          ref={svgRef}
          height={SVG_HEIGHT}
          width={SVG_WIDTH}
          viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
          className={styles.svg}
        >
          <defs>
            <style>
              {`.lock-icon { fill: var(--text-color); stroke: var(--text-color); stroke-width: 2; }`}
            </style>
          </defs>

          {/* Lane dividers */}
          <line
            x1="0"
            y1={LANE_1_Y + LANE_HEIGHT / 2}
            x2={SVG_WIDTH}
            y2={LANE_1_Y + LANE_HEIGHT / 2}
            stroke="var(--text-color)"
            strokeWidth="1"
            opacity="0.15"
          />
          <line
            x1="0"
            y1={LANE_2_Y + LANE_HEIGHT / 2}
            x2={SVG_WIDTH}
            y2={LANE_2_Y + LANE_HEIGHT / 2}
            stroke="var(--text-color)"
            strokeWidth="1"
            opacity="0.15"
          />

          {/* Job labels */}
          {[1, 2, 3].map((job) => (
            <text
              key={`job-label-${job}`}
              x="20"
              y={getLaneY(job) + 5}
              fill="var(--text-color)"
              fontSize={fontSize}
              fontFamily="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
              fontWeight="normal"
            >
              Job {job}
            </text>
          ))}


          {/* Waiting indicators (clock icons) - positioned at same Y as lock to replace it */}
          {[1, 2, 3].map((job) => (
            <animated.g
              key={`waiting-${job}`}
              transform={`translate(${LOCK_X}, ${getLaneY(job)})`}
              style={waitingSprings[job - 1]}
            >
              {/* Clock icon showing waiting state - same position as lock */}
              <circle
                r="14"
                fill="var(--code-background)"
                stroke="var(--text-color)"
                strokeWidth="1.5"
              />
              <path
                d="M0,0 L0,-8"
                stroke="var(--text-color)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <path
                d="M0,0 L5,0"
                stroke="var(--text-color)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </animated.g>
          ))}

          {/* Active lock indicators (full opacity when job has the lock) */}
          {[1, 2, 3].map((job) => (
            <animated.g
              key={`lock-${job}`}
              transform={`translate(${LOCK_X}, ${getLaneY(job)}) scale(1.5)`}
              style={lockSprings[job - 1]}
            >
              {/* Lock icon from SVG */}
              <path
                d="M12,22a7,7,0,0,0,5-11.894V7A5,5,0,0,0,7,7v3.106A7,7,0,0,0,12,22Zm1-6.277V18a1,1,0,0,1-2,0V15.723a2,2,0,1,1,2,0ZM9,7a3,3,0,0,1,6,0V8.683a6.93,6.93,0,0,0-6,0Z"
                fill="var(--text-color)"
                transform="translate(-12, -12)"
              />
            </animated.g>
          ))}


          {/* Progress bars during work phase */}
          {[1, 2, 3].map((job) => {
            const spring = progressSprings[job - 1];
            return (
              <g key={`progress-${job}`}>
                {/* Background track - only visible when progress bar is active */}
                <animated.rect
                  x={WORK_START_X}
                  y={getLaneY(job) - 8}
                  width={WORK_END_X - WORK_START_X}
                  height="16"
                  fill="var(--code-background)"
                  stroke="var(--text-color)"
                  strokeWidth="1"
                  opacity={spring.opacity.to((o) => o * 0.3)}
                  rx="8"
                />
                {/* Animated progress bar */}
                <animated.rect
                  x={WORK_START_X}
                  y={getLaneY(job) - 8}
                  width={spring.width}
                  height="16"
                  fill="var(--text-color)"
                  opacity={spring.opacity}
                  rx="8"
                />
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
};

export default LockingSwimlane;
