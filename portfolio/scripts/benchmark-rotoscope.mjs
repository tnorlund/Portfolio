#!/usr/bin/env node
/* global MutationObserver, URL, clearTimeout, console, document, performance, queueMicrotask, setTimeout */

import { existsSync } from "node:fs";
import process from "node:process";
import { chromium, firefox, webkit } from "@playwright/test";

const browserTypes = { chromium, firefox, webkit };
const wasmPath = "/wasm/rotoscope_v1.wasm";

const usage = `
Usage:
  node scripts/benchmark-rotoscope.mjs <production-url> [options]

Options:
  --browsers <list>  Comma-separated chromium,firefox,webkit (default: all)
  --warmups <count>  Unrecorded Replay runs; minimum 5 (default: 5)
  --runs <count>     Recorded Replay runs; minimum 20 (default: 25)
  --timeout <ms>     Navigation/render timeout (default: 30000)
  --json             Also print the complete machine-readable result
  --help             Show this help

Each browser is measured normally, then with ${wasmPath} aborted to exercise
the scalar fallback. Firefox also gets a diagnostic forced-Wasm run so its
evidence-based routing guard can be revisited. Missing browser executables are
skipped.
`;

const failUsage = (message) => {
  if (message) console.error(`Error: ${message}\n`);
  console.error(usage.trim());
  process.exitCode = 2;
};

const takeValue = (args, index, name) => {
  const argument = args[index];
  const prefix = `${name}=`;
  if (argument.startsWith(prefix)) return { value: argument.slice(prefix.length), used: 1 };
  if (argument === name && index + 1 < args.length) {
    return { value: args[index + 1], used: 2 };
  }
  return null;
};

const parseInteger = (value, name, minimum) => {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum) {
    throw new Error(`${name} must be an integer >= ${minimum}`);
  }
  return parsed;
};

const parseArguments = (args) => {
  const options = {
    url: null,
    browsers: Object.keys(browserTypes),
    warmups: 5,
    runs: 25,
    timeoutMs: 30_000,
    json: false,
  };

  for (let index = 0; index < args.length; ) {
    const argument = args[index];
    if (argument === "--help" || argument === "-h") return { help: true };
    if (argument === "--json") {
      options.json = true;
      index += 1;
      continue;
    }

    const browsers = takeValue(args, index, "--browsers");
    if (browsers) {
      const requested = browsers.value.split(",").map((name) => name.trim()).filter(Boolean);
      const unknown = requested.filter((name) => !(name in browserTypes));
      if (requested.length === 0 || unknown.length > 0) {
        throw new Error(`unknown browser(s): ${unknown.join(", ") || "none"}`);
      }
      options.browsers = [...new Set(requested)];
      index += browsers.used;
      continue;
    }

    const warmups = takeValue(args, index, "--warmups");
    if (warmups) {
      options.warmups = parseInteger(warmups.value, "--warmups", 5);
      index += warmups.used;
      continue;
    }

    const runs = takeValue(args, index, "--runs");
    if (runs) {
      options.runs = parseInteger(runs.value, "--runs", 20);
      index += runs.used;
      continue;
    }

    const timeout = takeValue(args, index, "--timeout");
    if (timeout) {
      options.timeoutMs = parseInteger(timeout.value, "--timeout", 1_000);
      index += timeout.used;
      continue;
    }

    if (argument.startsWith("--")) throw new Error(`unknown option: ${argument}`);
    if (options.url !== null) throw new Error("only one production URL may be supplied");
    const url = new URL(argument);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      throw new Error("production URL must use http or https");
    }
    options.url = url.href;
    index += 1;
  }

  if (!options.url) throw new Error("a production URL is required");
  return options;
};

const round = (value) => Math.round(value * 100) / 100;

const median = (values) => {
  const sorted = [...values].sort((left, right) => left - right);
  const midpoint = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[midpoint - 1] + sorted[midpoint]) / 2
    : sorted[midpoint];
};

const percentile = (values, fraction) => {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)];
};

const summarizePaths = (samples) => {
  const counts = new Map();
  for (const sample of samples) counts.set(sample.path, (counts.get(sample.path) ?? 0) + 1);
  return [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, count]) => `${path}:${count}`)
    .join(",");
};

const readReadySample = async (page) =>
  page.evaluate(() => {
    const replay = [...document.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Replay",
    );
    const frame = replay?.closest("figure")?.querySelector('[data-state="ready"]');
    const renderMs = Number(frame?.getAttribute("data-render-ms"));
    const path = frame?.getAttribute("data-render-path");
    if (!frame || !Number.isFinite(renderMs) || !path) {
      throw new Error("ready rotoscope data attributes are unavailable");
    }
    return {
      renderMs,
      path,
      pipelineMs: Number(frame.getAttribute("data-pipeline-ms")),
      wasmLoadMs: Number(frame.getAttribute("data-wasm-load-ms")),
      focusMapMs: Number(frame.getAttribute("data-focus-map-ms")),
      decodeMs: Number(frame.getAttribute("data-decode-ms")),
      paintMs: Number(frame.getAttribute("data-paint-ms")),
    };
  });

const replayOnce = async (page, timeoutMs) =>
  page.evaluate(
    ({ timeout }) =>
      new Promise((resolve, reject) => {
        const replay = [...document.querySelectorAll("button")].find(
          (button) => button.textContent?.trim() === "Replay",
        );
        const frame = replay?.closest("figure")?.querySelector("[data-state]");
        if (!replay || !frame) {
          reject(new Error("Replay UI or rotoscope data-state element was not found"));
          return;
        }

        let sawProcessing = frame.getAttribute("data-state") === "processing";
        const startedAt = performance.now();
        const finish = (callback) => {
          observer.disconnect();
          clearTimeout(timer);
          callback();
        };
        const inspect = (records = []) => {
          for (const record of records) {
            if (record.attributeName === "data-state" && record.oldValue === "processing") {
              sawProcessing = true;
            }
          }
          if (frame.getAttribute("data-state") === "processing") sawProcessing = true;
          if (!sawProcessing || frame.getAttribute("data-state") !== "ready") return;
          const renderMs = Number(frame.getAttribute("data-render-ms"));
          const path = frame.getAttribute("data-render-path");
          if (!Number.isFinite(renderMs) || !path) return;
          finish(() => resolve({
            renderMs,
            wallMs: performance.now() - startedAt,
            path,
            pipelineMs: Number(frame.getAttribute("data-pipeline-ms")),
            wasmLoadMs: Number(frame.getAttribute("data-wasm-load-ms")),
            focusMapMs: Number(frame.getAttribute("data-focus-map-ms")),
            decodeMs: Number(frame.getAttribute("data-decode-ms")),
            paintMs: Number(frame.getAttribute("data-paint-ms")),
          }));
        };
        const observer = new MutationObserver(inspect);
        observer.observe(frame, {
          attributes: true,
          attributeFilter: ["data-state", "data-render-ms", "data-render-path"],
          attributeOldValue: true,
        });
        const timer = setTimeout(
          () => finish(() => reject(new Error("Replay did not return to ready state"))),
          timeout,
        );
        replay.click();
        queueMicrotask(() => inspect());
      }),
    { timeout: timeoutMs },
  );

const benchmarkMode = async (browserType, options, mode) => {
  const forceScalar = mode === "forced-scalar";
  const forceWasm = mode === "forced-wasm";
  const browser = await browserType.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    reducedMotion: "reduce",
    ...(forceWasm
      ? {
          // Preserve the Firefox engine while bypassing the production
          // user-agent gate solely for repeatable diagnostics.
          userAgent:
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:141.0) " +
            "Gecko/20100101 RotoscopeBenchmark/1.0",
        }
      : {}),
  });
  let wasmRequests = 0;
  let wasmAborts = 0;

  context.on("request", (request) => {
    if (new URL(request.url()).pathname === wasmPath) wasmRequests += 1;
  });
  if (forceScalar) {
    await context.route(`**${wasmPath}*`, async (route) => {
      wasmAborts += 1;
      await route.abort("blockedbyclient");
    });
  }

  const page = await context.newPage();
  page.setDefaultTimeout(options.timeoutMs);
  const coldStartedAt = performance.now();

  try {
    const response = await page.goto(options.url, {
      waitUntil: "domcontentloaded",
      timeout: options.timeoutMs,
    });
    if (!response?.ok()) {
      throw new Error(`navigation returned HTTP ${response?.status() ?? "unknown"}`);
    }
    await page.getByRole("button", { name: "Replay", exact: true }).waitFor({
      state: "visible",
      timeout: options.timeoutMs,
    });
    const cold = {
      ...(await readReadySample(page)),
      wallMs: performance.now() - coldStartedAt,
    };

    const warmups = [];
    for (let index = 0; index < options.warmups; index += 1) {
      warmups.push(await replayOnce(page, options.timeoutMs));
    }

    const samples = [];
    for (let index = 0; index < options.runs; index += 1) {
      samples.push(await replayOnce(page, options.timeoutMs));
    }

    const renderTimes = samples.map((sample) => sample.renderMs);
    const wallTimes = samples.map((sample) => sample.wallMs);
    const stageSummary = Object.fromEntries(
      ["pipelineMs", "wasmLoadMs", "focusMapMs", "decodeMs", "paintMs"].map(
        (name) => {
          const values = samples.map((sample) => sample[name]);
          return [name, {
            median: round(median(values)),
            p95: round(percentile(values, 0.95)),
          }];
        },
      ),
    );
    return {
      mode,
      cold: { renderMs: round(cold.renderMs), wallMs: round(cold.wallMs), path: cold.path },
      warmups: warmups.length,
      runs: samples.length,
      medianMs: round(median(renderTimes)),
      p95Ms: round(percentile(renderTimes, 0.95)),
      medianWallMs: round(median(wallTimes)),
      p95WallMs: round(percentile(wallTimes, 0.95)),
      stages: stageSummary,
      paths: summarizePaths(samples),
      wasmRequests,
      wasmAborts,
    };
  } finally {
    await context.close();
    await browser.close();
  }
};

const pad = (value, width) => String(value).padEnd(width);

const printResult = (browserName, result) => {
  console.log(
    [
      pad(browserName, 9),
      pad(result.mode, 14),
      `cold ${String(result.cold.renderMs).padStart(7)} ms/${result.cold.path}`,
      `median ${String(result.medianMs).padStart(7)} ms`,
      `p95 ${String(result.p95Ms).padStart(7)} ms`,
      `pipeline ${String(result.stages.pipelineMs.median).padStart(6)} ms`,
      `path ${result.paths}`,
      `wasm req/abort ${result.wasmRequests}/${result.wasmAborts}`,
    ].join("  "),
  );
};

let options;
try {
  options = parseArguments(process.argv.slice(2));
} catch (error) {
  failUsage(error instanceof Error ? error.message : String(error));
}

if (options?.help) {
  console.log(usage.trim());
} else if (options) {
  console.log(`Rotoscope production benchmark: ${options.url}`);
  console.log(
    `${options.warmups} warmups + ${options.runs} measured Replay runs per mode; ` +
      `render time comes from data-render-ms.`,
  );

  const report = {
    url: options.url,
    generatedAt: new Date().toISOString(),
    warmups: options.warmups,
    runs: options.runs,
    browsers: [],
    skipped: [],
  };

  for (const browserName of options.browsers) {
    const browserType = browserTypes[browserName];
    const executable = browserType.executablePath();
    if (!existsSync(executable)) {
      const reason = `Playwright executable is not installed (${executable})`;
      report.skipped.push({ browser: browserName, reason });
      console.log(`${pad(browserName, 9)}  SKIP  ${reason}`);
      continue;
    }

    const browserReport = { browser: browserName, modes: [] };
    report.browsers.push(browserReport);
    const modes = [
      "normal",
      "forced-scalar",
      ...(browserName === "firefox" ? ["forced-wasm"] : []),
    ];
    for (const mode of modes) {
      try {
        const result = await benchmarkMode(browserType, options, mode);
        browserReport.modes.push(result);
        printResult(browserName, result);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        browserReport.modes.push({ mode, error: message });
        console.log(`${pad(browserName, 9)}  ${pad(mode, 14)}  ERROR  ${message}`);
      }
    }
  }

  if (report.browsers.length === 0) {
    console.log("No installed Playwright browser executables were available; nothing ran.");
  }
  if (options.json) console.log(`\n${JSON.stringify(report, null, 2)}`);
}
