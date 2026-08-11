import {
  __resetWorkerClientForTests,
  knockOutInWorker,
  stampThermalInWorker,
} from "./workerClient";

afterEach(() => {
  __resetWorkerClientForTests();
});

test("knockOutInWorker returns null when Worker is unavailable", async () => {
  const original = global.Worker;
  // @ts-expect-error force unavailable in jsdom-like envs
  global.Worker = undefined;

  const result = await knockOutInWorker(new Uint8ClampedArray([0, 0, 0, 255]));
  expect(result).toBeNull();

  global.Worker = original;
});

test("stampThermalInWorker returns null when Worker is unavailable", async () => {
  const original = global.Worker;
  // @ts-expect-error force unavailable
  global.Worker = undefined;

  const result = await stampThermalInWorker({
    width: 4,
    height: 4,
    points: new Float32Array([1, 1]),
    count: 1,
    radius: 1,
    red: 0,
    green: 0,
    blue: 0,
  });
  expect(result).toBeNull();

  global.Worker = original;
});
