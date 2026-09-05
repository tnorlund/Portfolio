import { useCallback, useEffect, useRef, useState } from "react";
import type { Command, Snapshot } from "./types";

async function request<T>(
  base: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${base}/planner/api/${path}`, {
    method: body ? "POST" : "GET",
    cache: "no-store",
    ...(body
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
    signal: AbortSignal.timeout(10000),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      value.error || "The planner could not complete this request.",
    );
  return value as T;
}
export function usePlanner() {
  const [base, setBase] = useState<string | null>(null);
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const current = useRef<Snapshot | null>(null);
  const mounted = useRef(false);
  const inFlight = useRef<Promise<void> | null>(null);
  const lastWrite = useRef<{ serialized: string; id: string } | null>(null);
  useEffect(() => {
    mounted.current = true;
    const configured = process.env.NEXT_PUBLIC_PLANNER_API_URL;
    if (configured) setBase(configured.replace(/\/$/, ""));
    else if (["localhost", "127.0.0.1"].includes(window.location.hostname))
      setBase("http://127.0.0.1:4317");
    else
      setError(
        "This planner needs a configured private API. Open the local app to continue.",
      );
    return () => {
      mounted.current = false;
    };
  }, []);
  const refresh = useCallback((): Promise<void> => {
    if (!base) return Promise.resolve();
    if (inFlight.current) return inFlight.current;
    const pending = request<Snapshot>(base, "snapshot")
      .then((snapshot) => {
        if (mounted.current) {
          current.current = snapshot;
          setData(snapshot);
          setError("");
        }
      })
      .finally(() => {
        inFlight.current = null;
      });
    inFlight.current = pending;
    return pending;
  }, [base]);
  useEffect(() => {
    if (!base) return;
    let active = true;
    let running = false;
    let timer: ReturnType<typeof setTimeout>;
    const fail = (reason: unknown) => {
      if (active)
        setError(
          reason instanceof Error
            ? reason.message
            : "Connection lost. Your saved work is safe.",
        );
    };
    const tick = async () => {
      if (running || !active) return;
      running = true;
      clearTimeout(timer);
      try {
        if (!current.current) {
          await refresh();
        } else {
          const clock = await request<{ version: number }>(base, "clock");
          if (clock.version !== current.current.version) await refresh();
          else if (active) setError("");
        }
      } catch (reason) {
        fail(reason);
      }
      running = false;
      if (active)
        timer = setTimeout(
          tick,
          document.visibilityState === "hidden" ? 30000 : 3000,
        );
    };
    const visibility = () => {
      clearTimeout(timer);
      void tick();
    };
    void tick();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [base, refresh]);
  const execute = useCallback(
    async (command: Command) => {
      if (!base) throw new Error("Connect the planner first.");
      const serialized = JSON.stringify(command);
      if (lastWrite.current?.serialized !== serialized)
        lastWrite.current = { serialized, id: crypto.randomUUID() };
      const id = lastWrite.current.id;
      setSaving(true);
      try {
        const response = await request<{ version: number; result: unknown }>(
          base,
          "commands",
          { command, request_id: id },
        );
        lastWrite.current = null;
        // A prior read may have started before this write. Wait it out, then obtain
        // a fresh snapshot. Never advance the observed version on a failed read.
        if (inFlight.current) await inFlight.current.catch(() => undefined);
        await refresh().catch((reason) =>
          setError(
            `Saved. ${reason instanceof Error ? reason.message : "Reconnecting…"}`,
          ),
        );
        return response;
      } finally {
        if (mounted.current) setSaving(false);
      }
    },
    [base, refresh],
  );
  return { data, error, saving, execute, refresh };
}
