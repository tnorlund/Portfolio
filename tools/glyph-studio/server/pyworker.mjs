// Persistent python worker: JSON-lines RPC over stdio, restart on crash.
import { spawn } from "node:child_process";
import { PYTHON, PY_ENV, STUDIO_ROOT } from "./env.mjs";
import path from "node:path";

let proc = null;
let nextId = 1;
const pending = new Map();
let buffer = "";

function start() {
  proc = spawn(PYTHON, ["-m", "glyphstudio.worker"], {
    cwd: path.join(STUDIO_ROOT, "py"),
    env: PY_ENV,
    stdio: ["pipe", "pipe", "inherit"],
  });
  proc.stdout.on("data", (chunk) => {
    buffer += chunk.toString();
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 1);
      if (!line.trim()) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }
      const entry = pending.get(msg.id);
      if (entry) {
        pending.delete(msg.id);
        msg.ok ? entry.resolve(msg) : entry.reject(new Error(msg.error));
      }
    }
  });
  proc.on("exit", () => {
    for (const { reject } of pending.values()) {
      reject(new Error("python worker exited"));
    }
    pending.clear();
    proc = null;
  });
}

export function call(op, args = {}, { timeoutMs = 30000 } = {}) {
  if (!proc) start();
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`worker op ${op} timed out`));
    }, timeoutMs);
    pending.set(id, {
      resolve: (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      reject: (e) => {
        clearTimeout(timer);
        reject(e);
      },
    });
    proc.stdin.write(JSON.stringify({ id, op, ...args }) + "\n");
  });
}
