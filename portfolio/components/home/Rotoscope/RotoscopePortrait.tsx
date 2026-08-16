import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
  PORTRAIT_SOURCES,
} from "./portraitConfig";
import styles from "./RotoscopePortrait.module.css";
import { applyBasinRevealPhase } from "./reveal";
import { RotoscopeWorkerClient } from "./workerClient";
import type {
  RotoscopeRenderSuccess,
  RotoscopeTimings,
} from "./workerProtocol";

type RenderState = "idle" | "processing" | "ready" | "unavailable";
type RevealState = "waiting" | "revealing" | "complete";

type IdleWindow = Window & {
  requestIdleCallback?: (
    callback: () => void,
    options?: { timeout: number },
  ) => number;
  cancelIdleCallback?: (id: number) => void;
};

const prepareWorkerResult = (
  canvas: HTMLCanvasElement,
  result: RotoscopeRenderSuccess,
): {
  context: CanvasRenderingContext2D;
  imageData: ImageData;
  source: Uint8ClampedArray;
  phases: Uint8Array;
} => {
  const expectedPixels = result.width * result.height;
  const source = new Uint8ClampedArray(result.pixelsBuffer);
  const phases = new Uint8Array(result.revealPhasesBuffer);
  if (
    source.length !== expectedPixels * 4 ||
    phases.length !== expectedPixels ||
    !Number.isInteger(result.revealPhaseCount) ||
    result.revealPhaseCount < 2 ||
    result.revealPhaseCount > 255
  ) {
    throw new Error("invalid basin reveal result");
  }
  canvas.width = result.width;
  canvas.height = result.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2D canvas is unavailable");
  const imageData = context.createImageData(result.width, result.height);
  context.clearRect(0, 0, result.width, result.height);
  return { context, imageData, source, phases };
};

const REVEAL_DURATION_MS = 1100;

export default function RotoscopePortrait() {
  const imageRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const clientRef = useRef<RotoscopeWorkerClient | null>(null);
  const scheduledRef = useRef<{ type: "idle" | "timeout"; id: number } | null>(null);
  const revealFrameRef = useRef<number | null>(null);
  const revealGenerationRef = useRef(0);
  const mountedRef = useRef(true);
  const [renderState, setRenderState] = useState<RenderState>("idle");
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [renderPath, setRenderPath] = useState<RotoscopeRenderSuccess["path"] | null>(
    null,
  );
  const [timings, setTimings] = useState<RotoscopeTimings | null>(null);
  const [revealState, setRevealState] = useState<RevealState>("waiting");

  const cancelReveal = useCallback((clearCanvas: boolean) => {
    revealGenerationRef.current += 1;
    if (revealFrameRef.current !== null) {
      window.cancelAnimationFrame(revealFrameRef.current);
      revealFrameRef.current = null;
    }
    const frame = frameRef.current;
    if (frame) {
      frame.dataset.revealPhase = "0";
      frame.dataset.revealState = "waiting";
    }
    if (clearCanvas) {
      const canvas = canvasRef.current;
      canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    }
  }, []);

  const revealResult = useCallback(
    (canvas: HTMLCanvasElement, result: RotoscopeRenderSuccess) => {
      const prepared = prepareWorkerResult(canvas, result);
      const frame = frameRef.current;
      const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      if (reducedMotion) {
        prepared.imageData.data.set(prepared.source);
        prepared.context.putImageData(prepared.imageData, 0, 0);
        if (frame) {
          frame.dataset.revealPhase = String(result.revealPhaseCount - 1);
          frame.dataset.revealState = "complete";
        }
        setRevealState("complete");
        return;
      }

      const generation = revealGenerationRef.current;
      let previousPhase = -1;
      const startedAt = performance.now();
      setRevealState("revealing");
      if (frame) frame.dataset.revealState = "revealing";

      const paint = (now: number) => {
        if (
          generation !== revealGenerationRef.current ||
          !mountedRef.current
        ) {
          return;
        }
        const progress = Math.min(1, (now - startedAt) / REVEAL_DURATION_MS);
        const nextPhase = Math.min(
          result.revealPhaseCount - 1,
          Math.floor(progress * result.revealPhaseCount),
        );
        if (nextPhase > previousPhase) {
          applyBasinRevealPhase(
            prepared.imageData.data,
            prepared.source,
            prepared.phases,
            previousPhase,
            nextPhase,
          );
          prepared.context.putImageData(prepared.imageData, 0, 0);
          previousPhase = nextPhase;
          if (frame) frame.dataset.revealPhase = String(nextPhase);
        }
        if (nextPhase < result.revealPhaseCount - 1) {
          revealFrameRef.current = window.requestAnimationFrame(paint);
          return;
        }
        revealFrameRef.current = null;
        if (frame) frame.dataset.revealState = "complete";
        setRevealState("complete");
      };

      revealFrameRef.current = window.requestAnimationFrame(paint);
    },
    [],
  );

  const renderPortrait = useCallback(async () => {
    const image = imageRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas) return;
    const client = clientRef.current ?? new RotoscopeWorkerClient();
    clientRef.current = client;
    if (!client.available()) {
      setRenderState("unavailable");
      return;
    }
    cancelReveal(true);
    setRenderState("processing");
    setRevealState("waiting");
    const requestedAt = performance.now();
    try {
      const result = await client.render({
        image,
        ...PORTRAIT_PROCESSING_SIZE,
        options: PORTRAIT_ROTOSCOPE_OPTIONS,
      });
      if (!mountedRef.current || !result) return;
      revealResult(canvas, result);
      setElapsedMs(performance.now() - requestedAt);
      setRenderPath(result.path);
      setTimings(result.timings);
      setRenderState("ready");
    } catch {
      if (mountedRef.current) setRenderState("unavailable");
    }
  }, [cancelReveal, revealResult]);

  const scheduleRender = useCallback(() => {
    if (scheduledRef.current || renderState !== "idle") return;
    const idleWindow = window as IdleWindow;
    if (idleWindow.requestIdleCallback) {
      const id = idleWindow.requestIdleCallback(
        () => {
          scheduledRef.current = null;
          void renderPortrait();
        },
        { timeout: 500 },
      );
      scheduledRef.current = { type: "idle", id };
    } else {
      const id = window.setTimeout(() => {
        scheduledRef.current = null;
        void renderPortrait();
      }, 80);
      scheduledRef.current = { type: "timeout", id };
    }
  }, [renderPortrait, renderState]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const scheduled = scheduledRef.current;
      if (scheduled?.type === "idle") {
        (window as IdleWindow).cancelIdleCallback?.(scheduled.id);
      } else if (scheduled) {
        window.clearTimeout(scheduled.id);
      }
      cancelReveal(false);
      clientRef.current?.dispose();
    };
  }, [cancelReveal]);

  useEffect(() => {
    // Cached/preloaded images can finish before React attaches the `load`
    // listener. The complete check guarantees the idle render still starts.
    if (imageRef.current?.complete) scheduleRender();
  }, [scheduleRender]);

  const ready = renderState === "ready";

  return (
    <figure className={styles.figure}>
      <div
        ref={frameRef}
        className={styles.frame}
        data-state={renderState}
        data-reveal-state={revealState}
        data-render-path={renderPath ?? undefined}
        data-render-ms={elapsedMs === null ? undefined : elapsedMs.toFixed(2)}
        data-pipeline-ms={timings?.pipelineMs.toFixed(2)}
        data-wasm-load-ms={timings?.wasmLoadMs.toFixed(2)}
        data-focus-map-ms={timings?.focusMapMs.toFixed(2)}
        data-decode-ms={timings?.decodeAndResizeMs.toFixed(2)}
        data-paint-ms={timings?.paintMs.toFixed(2)}
      >
        {/* This pre-generated static asset avoids the next/image client wrapper. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className={`${styles.mediaLayer} ${styles.basinMap}`}
          src={PORTRAIT_SOURCES.basins}
          alt="Catchment basins outlining Tyler Norlund's portrait"
          width={480}
          height={360}
          loading="eager"
          decoding="async"
          fetchPriority="high"
        />
        <picture>
          <source srcSet={PORTRAIT_SOURCES.avif} type="image/avif" />
          <source srcSet={PORTRAIT_SOURCES.webp} type="image/webp" />
          <img
            ref={imageRef}
            className={`${styles.mediaLayer} ${styles.sourcePortrait}`}
            src={PORTRAIT_SOURCES.fallback}
            alt=""
            aria-hidden="true"
            width={960}
            height={720}
            loading="eager"
            decoding="async"
            onLoad={scheduleRender}
          />
        </picture>
        <canvas
          ref={canvasRef}
          className={`${styles.mediaLayer} ${styles.rotoscoped}`}
          aria-hidden="true"
        />
      </div>
      <figcaption className={styles.caption} aria-live="polite">
        <span>
          <strong>Best-features rotoscope.</strong>{" "}
          {ready && revealState === "revealing"
            ? "Filling each catchment basin…"
            : ready && elapsedMs !== null
            ? `Single-image · ${Math.round(elapsedMs)} ms.`
            : renderState === "processing"
              ? "Filling catchment basins…"
              : "From my 2017 paper."}
        </span>
        <span className={styles.links}>
          {ready ? (
            <button className={styles.textButton} type="button" onClick={renderPortrait}>
              Replay
            </button>
          ) : null}
          <a
            href="https://doi.org/10.1109/ACSSC.2017.8335175"
            target="_blank"
            rel="noreferrer"
          >
            Paper
          </a>
          <a
            href="https://github.com/tnorlund/BestFeatureRotoscope"
            target="_blank"
            rel="noreferrer"
          >
            Source
          </a>
        </span>
      </figcaption>
    </figure>
  );
}
