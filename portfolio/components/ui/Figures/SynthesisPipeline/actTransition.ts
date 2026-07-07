import { useEffect, useReducer, useRef } from "react";

/**
 * Act-to-act transition state machine for the SynthesisPipeline stage.
 *
 * A hard key-swap cuts between acts abruptly. To crossfade, we keep BOTH the
 * outgoing (`leaving`) and incoming (`current`) act mounted for a short window
 * so CSS can fade/translate one out while the other comes in. When the window
 * elapses the outgoing act is dropped.
 *
 * The reducer is pure so the enter/leave/settle logic is unit-testable without
 * a running clock; the hook wires it to `setTimeout` with an injectable
 * duration so component tests can drive it with fake timers.
 */

/** Duration of the crossfade window (ms). Kept in sync with the CSS keyframes. */
export const ACT_TRANSITION_MS = 380;

export interface TransitionState {
  /** The incoming act — always mounted, active, and progress-driven. */
  current: number;
  /** The outgoing act, mounted only during the crossfade window, else null. */
  leaving: number | null;
}

export type TransitionAction =
  | { type: "target"; act: number; animate: boolean }
  | { type: "settle" };

export const initialTransitionState = (act: number): TransitionState => ({
  current: act,
  leaving: null,
});

/**
 * `target` retargets the stage to `act`. When `animate` is false (reduced
 * motion) it swaps instantly with no outgoing layer. Otherwise the act being
 * replaced becomes `leaving` so both render during the crossfade. Retargeting
 * mid-transition drops whatever was already leaving and keeps only the two
 * most-recent acts. `settle` ends the window by clearing `leaving`.
 */
export const transitionReducer = (
  state: TransitionState,
  action: TransitionAction,
): TransitionState => {
  switch (action.type) {
    case "target": {
      if (action.act === state.current) {
        return state; // already showing this act; nothing to transition to
      }
      if (!action.animate) {
        return { current: action.act, leaving: null };
      }
      return { current: action.act, leaving: state.current };
    }
    case "settle": {
      if (state.leaving === null) {
        return state;
      }
      return { current: state.current, leaving: null };
    }
    default:
      return state;
  }
};

/**
 * Drive the transition machine from a target act index. Returns the current
 * `{ current, leaving }` so the caller can render both layers during the
 * crossfade. `durationMs` is injectable for fake-timer tests.
 */
export const useActTransition = (
  activeAct: number,
  animate: boolean,
  durationMs: number = ACT_TRANSITION_MS,
): TransitionState => {
  const [state, dispatch] = useReducer(
    transitionReducer,
    activeAct,
    initialTransitionState,
  );
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    dispatch({ type: "target", act: activeAct, animate });
  }, [activeAct, animate]);

  // While an act is leaving, schedule its removal. A retarget mid-window swaps
  // in a different `leaving` act (the outgoing current is always distinct from
  // the act it replaced), so this re-runs and restarts the timer, giving the
  // new pair a fresh crossfade rather than a stale early cleanup.
  useEffect(() => {
    if (state.leaving === null) {
      return;
    }
    timer.current = setTimeout(() => dispatch({ type: "settle" }), durationMs);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [state.leaving, durationMs]);

  return state;
};
