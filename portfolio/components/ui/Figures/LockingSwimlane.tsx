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
  const LOCK_X = 160; // Single lock position (shared resource) - spaced to match job name distance from vertical line
  const LOCK_WIDTH = 32; // Lock icon width
  const SPACING = 50; // Consistent spacing between elements
  const WORK_START_X = LOCK_X + LOCK_WIDTH + SPACING; // 160 + 32 + 50 = 242
  const WORK_WIDTH = 200; // Progress bar width
  const WORK_END_X = WORK_START_X + WORK_WIDTH; // 242 + 200 = 442
  const LABEL_X = WORK_END_X + SPACING; // 442 + 50 = 492
  const LABEL_MAX_WIDTH = 70; // Approximate max width for labels like "Validate", "Upload"
  const CONTAINER_WIDTH = LABEL_X + LABEL_MAX_WIDTH; // 492 + 70 = 562
  const CONTAINER_HEIGHT = 350;

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

  React.useEffect(() => {
    // Start the cycle timer
    const id = setTimeout(() => {
      setCycle((c) => c + 1);
    }, totalCycleTime);
    return () => clearTimeout(id);
  }, [cycle, totalCycleTime]);

  // Animate progress bars (show during work phase)
  // Use explicit API control for precise timing
  const workWidth = WORK_WIDTH;
  const [progressSprings, progressApi] = useSprings(3, () => ({ width: 0, opacity: 0 }));

  React.useEffect(() => {
    const timeouts: NodeJS.Timeout[] = [];

    // Reset all animations immediately at cycle start (use setTimeout 0 to avoid blocking)
    timeouts.push(
      setTimeout(() => {
        progressApi.start((i) => ({
          width: 0,
          opacity: 0,
          immediate: true,
        }));
      }, 0)
    );

    [1, 2, 3].forEach((job) => {
      const workPhase = TIMELINE.find((t) => t.job === job && t.phase === "work");
      if (!workPhase) return;

      const jobIndex = job - 1;
      const fadeInDuration = 200;
      const fillDuration = workPhase.duration;
      const fadeOutDuration = 200;

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
                width: 100, // Animate from 0 to 100 (percentage)
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

    // Reset all animations immediately at cycle start (use setTimeout 0 to avoid blocking)
    timeouts.push(
      setTimeout(() => {
        lockApi.start((i) => ({
          opacity: 0,
          immediate: true,
        }));
      }, 0)
    );

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

    // Reset all animations immediately at cycle start (use setTimeout 0 to avoid blocking)
    timeouts.push(
      setTimeout(() => {
        waitingApi.start((i) => ({
          opacity: 0,
          immediate: true,
        }));
      }, 0)
    );

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

  // Track label DOM elements using refs to avoid re-renders
  const labelElementsRef = React.useRef<[HTMLDivElement | null, HTMLDivElement | null, HTMLDivElement | null]>([null, null, null]);

  React.useEffect(() => {
    const timeouts: NodeJS.Timeout[] = [];

    // Reset all labels immediately at cycle start
    labelElementsRef.current.forEach((el) => {
      if (el) {
        el.textContent = "";
        el.style.opacity = "0";
      }
    });

    [1, 2, 3].forEach((job) => {
      const lock1Phase = TIMELINE.find((t) => t.job === job && t.phase === "lock1");
      const workPhase = TIMELINE.find((t) => t.job === job && t.phase === "work");
      const lock2Phase = TIMELINE.find((t) => t.job === job && t.phase === "lock2");

      if (!lock1Phase || !workPhase || !lock2Phase) return;

      const jobIndex = job - 1;
      const fadeDuration = 200;

      // Show "Validate" (Download icon) during lock1
      timeouts.push(
        setTimeout(() => {
          const el = labelElementsRef.current[jobIndex];
          if (el) {
            el.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" style="display: block;">
              <circle cx="150" cy="150" r="136.82" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
              <g>
                <path d="M150,75.43l.38,121.09" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
                <polyline points="196.26 150.26 150.38 196.52 103.74 150.26" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
                <line x1="214.4" y1="224.57" x2="85.6" y2="224.57" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
              </g>
            </svg>`;
            el.style.transition = `opacity ${fadeDuration}ms`;
            el.style.opacity = "1";
          }
        }, lock1Phase.start)
      );

      timeouts.push(
        setTimeout(() => {
          const el = labelElementsRef.current[jobIndex];
          if (el) {
            el.style.transition = `opacity ${fadeDuration}ms`;
            el.style.opacity = "0";
          }
        }, lock1Phase.start + lock1Phase.duration - fadeDuration)
      );


      // Show "Upload" icon during lock2
      timeouts.push(
        setTimeout(() => {
          const el = labelElementsRef.current[jobIndex];
          if (el) {
            el.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" style="display: block;">
              <circle cx="150" cy="150" r="136.82" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
              <g>
                <path d="M150,203.98l.38-128.55" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
                <polyline points="196.26 121.69 150.38 75.43 103.74 121.69" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
                <line x1="214.4" y1="224.57" x2="85.6" y2="224.57" fill="none" stroke="var(--text-color)" stroke-miterlimit="10" stroke-width="20"/>
              </g>
            </svg>`;
            el.style.transition = `opacity ${fadeDuration}ms`;
            el.style.opacity = "1";
          }
        }, lock2Phase.start)
      );

      timeouts.push(
        setTimeout(() => {
          const el = labelElementsRef.current[jobIndex];
          if (el) {
            el.style.transition = `opacity ${fadeDuration}ms`;
            el.style.opacity = "0";
          }
        }, lock2Phase.start + lock2Phase.duration - fadeDuration)
      );
    });

    return () => {
      timeouts.forEach(clearTimeout);
    };
  }, [cycle, TIMELINE, totalCycleTime]);

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
        <div className={styles.swimlane}>
          {/* Job lanes */}
          {[1, 2, 3].map((job) => (
            <div key={`job-${job}`} className={styles.lane}>
              {/* Job label */}
              <div className={styles.jobLabel}>Job {job}</div>

              {/* Lock position */}
              <div className={styles.lockPosition}>
                {/* Lock icon */}
                <animated.div
                  className={styles.lockIcon}
                  style={{
                    ...lockSprings[job - 1],
                    pointerEvents: "none",
                  }}
                >
                  <svg
                    width="32"
                    height="32"
                    viewBox="0 0 300 300"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M150,293.04c55.3-.04,100.1-44.9,100.06-100.2-.02-26.14-10.26-51.24-28.54-69.94v-44.43c0-39.5-32.02-71.52-71.52-71.52s-71.52,32.02-71.52,71.52v44.43c-38.66,39.54-37.95,102.93,1.59,141.6,18.69,18.28,43.79,28.52,69.94,28.54ZM164.3,203.25v32.57c0,7.9-6.4,14.3-14.3,14.3s-14.3-6.4-14.3-14.3v-32.57c-13.68-7.9-18.37-25.4-10.47-39.08,7.9-13.68,25.4-18.37,39.08-10.47,13.68,7.9,18.37,25.4,10.47,39.08-2.51,4.35-6.12,7.96-10.47,10.47ZM107.09,78.48c0-23.7,19.21-42.91,42.91-42.91s42.91,19.21,42.91,42.91v24.07c-27.13-13.03-58.7-13.03-85.83,0v-24.07Z"
                      fill="var(--text-color)"
                    />
                  </svg>
                </animated.div>

                {/* Clock icon (waiting) */}
                <animated.div
                  className={styles.clockIcon}
                  style={{
                    ...waitingSprings[job - 1],
                    pointerEvents: "none",
                  }}
                >
                  <svg
                    width="32"
                    height="32"
                    viewBox="0 0 300 300"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <circle
                      cx="150"
                      cy="150"
                      r="136.82"
                      fill="none"
                      stroke="var(--text-color)"
                      strokeMiterlimit="10"
                      strokeWidth="20"
                    />
                    <path
                      d="M149.82,42.06l.18,107.94"
                      fill="none"
                      stroke="var(--text-color)"
                      strokeMiterlimit="10"
                      strokeWidth="20"
                    />
                    <line
                      x1="203.8"
                      y1="150"
                      x2="150"
                      y2="150"
                      fill="none"
                      stroke="var(--text-color)"
                      strokeMiterlimit="10"
                      strokeWidth="20"
                      strokeLinecap="square"
                    />
                  </svg>
                </animated.div>
              </div>

              {/* Work area with progress bar */}
              <animated.div
                className={styles.workArea}
                style={{
                  opacity: progressSprings[job - 1].opacity,
                }}
              >
                <div className={styles.progressBarTrack} />
                <animated.div
                  className={styles.progressBar}
                  style={{
                    width: progressSprings[job - 1].width.to((w) => `${w}%`),
                  }}
                />
              </animated.div>

              {/* Phase label */}
              <div
                ref={(el) => {
                  labelElementsRef.current[job - 1] = el;
                }}
                className={styles.phaseLabel}
                style={{
                  opacity: 0,
                  transition: "opacity 200ms",
                }}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default LockingSwimlane;
