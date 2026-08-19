import type { CSSProperties } from "react";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
  PORTRAIT_SOURCES,
} from "./portraitConfig";
import styles from "./RotoscopePortrait.module.css";
import { RotoscopeWorkerClient } from "./workerClient";
import type {
  RotoscopeRenderSuccess,
  RotoscopeTimings,
} from "./workerProtocol";

type RenderState = "idle" | "processing" | "ready" | "unavailable";

type IdleWindow = Window & {
  requestIdleCallback?: (
    callback: () => void,
    options?: { timeout: number },
  ) => number;
  cancelIdleCallback?: (id: number) => void;
};

const paintWorkerResult = (
  canvas: HTMLCanvasElement,
  result: RotoscopeRenderSuccess,
): void => {
  canvas.width = result.width;
  canvas.height = result.height;
  if (result.bitmap) {
    const bitmapContext = canvas.getContext("bitmaprenderer");
    if (bitmapContext) {
      bitmapContext.transferFromImageBitmap(result.bitmap);
    } else {
      const context = canvas.getContext("2d");
      context?.drawImage(result.bitmap, 0, 0);
    }
    result.bitmap.close();
    return;
  }
  if (result.pixelsBuffer) {
    const context = canvas.getContext("2d");
    if (!context) return;
    context.putImageData(
      new ImageData(
        new Uint8ClampedArray(result.pixelsBuffer),
        result.width,
        result.height,
      ),
      0,
      0,
    );
  }
};

export default function RotoscopePortrait() {
  const imageRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<RotoscopeWorkerClient | null>(null);
  const scheduledRef = useRef<{ type: "idle" | "timeout"; id: number } | null>(null);
  const fillFrameRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const [renderState, setRenderState] = useState<RenderState>("idle");
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [renderPath, setRenderPath] = useState<RotoscopeRenderSuccess["path"] | null>(
    null,
  );
  const [timings, setTimings] = useState<RotoscopeTimings | null>(null);
  const [filled, setFilled] = useState(false);

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
    setRenderState("processing");
    setFilled(false);
    const requestedAt = performance.now();
    try {
      const result = await client.render({
        image,
        ...PORTRAIT_PROCESSING_SIZE,
        options: PORTRAIT_ROTOSCOPE_OPTIONS,
      });
      if (!mountedRef.current || !result) {
        result?.bitmap?.close();
        return;
      }
      paintWorkerResult(canvas, result);
      setElapsedMs(performance.now() - requestedAt);
      setRenderPath(result.path);
      setTimings(result.timings);
      setRenderState("ready");
      const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      if (reducedMotion) setFilled(true);
      else {
        fillFrameRef.current = window.requestAnimationFrame(() => {
          fillFrameRef.current = null;
          setFilled(true);
        });
      }
    } catch {
      if (mountedRef.current) setRenderState("unavailable");
    }
  }, []);

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
      if (fillFrameRef.current !== null) {
        window.cancelAnimationFrame(fillFrameRef.current);
      }
      clientRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    // Cached/preloaded images can finish before React attaches the `load`
    // listener. The complete check guarantees the idle render still starts.
    if (imageRef.current?.complete) scheduleRender();
  }, [scheduleRender]);

  const frameStyle = {
    "--fill-radius": filled ? "90%" : "0%",
  } as CSSProperties;
  const ready = renderState === "ready";

  return (
    <figure className={styles.figure}>
      <div
        className={styles.frame}
        style={frameStyle}
        data-state={renderState}
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
          {ready && elapsedMs !== null
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
