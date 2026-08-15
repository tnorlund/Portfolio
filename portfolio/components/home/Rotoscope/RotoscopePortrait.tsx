import type { CSSProperties } from "react";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
  PORTRAIT_SOURCES,
} from "./portraitConfig";
import styles from "./RotoscopePortrait.module.css";
import { RotoscopeWorkerClient } from "./workerClient";
import type { RotoscopeRenderSuccess } from "./workerProtocol";

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
  const revealFrameRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const [renderState, setRenderState] = useState<RenderState>("idle");
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [reveal, setReveal] = useState(0);

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
    setReveal(0);
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
      setElapsedMs(result.timings.totalMs);
      setRenderState("ready");
      const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      if (reducedMotion) setReveal(58);
      else {
        revealFrameRef.current = window.requestAnimationFrame(() => {
          revealFrameRef.current = null;
          setReveal(58);
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
      if (revealFrameRef.current !== null) {
        window.cancelAnimationFrame(revealFrameRef.current);
      }
      clientRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    // Cached/preloaded images can finish before React attaches the `load`
    // listener. The complete check guarantees the idle render still starts.
    if (imageRef.current?.complete) scheduleRender();
  }, [scheduleRender]);

  const frameStyle = { "--reveal": `${reveal}%` } as CSSProperties;
  const ready = renderState === "ready";

  return (
    <figure className={styles.figure}>
      <div className={styles.frame} style={frameStyle} data-state={renderState}>
        <picture>
          <source srcSet={PORTRAIT_SOURCES.avif} type="image/avif" />
          <source srcSet={PORTRAIT_SOURCES.webp} type="image/webp" />
          <img
            ref={imageRef}
            className={styles.portrait}
            src={PORTRAIT_SOURCES.fallback}
            alt="Tyler Norlund smiling outside"
            width={960}
            height={720}
            loading="eager"
            decoding="async"
            onLoad={scheduleRender}
          />
        </picture>
        <canvas
          ref={canvasRef}
          className={styles.rotoscoped}
          aria-hidden="true"
        />
        {ready ? (
          <>
            <span className={`${styles.label} ${styles.originalLabel}`}>
              Original
            </span>
            <span className={`${styles.label} ${styles.rotoscopeLabel}`}>
              Rotoscoped
            </span>
            <span className={styles.divider} aria-hidden="true" />
            <input
              className={styles.revealControl}
              type="range"
              min="0"
              max="100"
              value={reveal}
              aria-label="Compare original and rotoscoped portrait"
              onChange={(event) => setReveal(Number(event.currentTarget.value))}
            />
          </>
        ) : null}
      </div>
      <figcaption className={styles.caption} aria-live="polite">
        <span>
          <strong>Best-features rotoscope.</strong>{" "}
          {ready && elapsedMs !== null
            ? `Single-image · ${Math.round(elapsedMs)} ms.`
            : renderState === "processing"
              ? "Rendering here in your browser…"
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
