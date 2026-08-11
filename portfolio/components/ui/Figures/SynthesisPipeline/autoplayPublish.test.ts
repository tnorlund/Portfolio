import { shouldPublishAutoplay } from "./autoplayPublish";

test("always publishes on act change", () => {
  expect(
    shouldPublishAutoplay(1, 0.01, 0, 0.99, 0, 1, 33, 0.01),
  ).toEqual({ publishAct: true, publishProgress: true });
});

test("throttles progress within the interval and delta", () => {
  expect(
    shouldPublishAutoplay(0, 0.005, 0, 0, 100, 120, 33, 0.01),
  ).toEqual({ publishAct: false, publishProgress: false });
});

test("publishes progress when interval elapses", () => {
  expect(
    shouldPublishAutoplay(0, 0.02, 0, 0.01, 100, 140, 33, 0.01),
  ).toEqual({ publishAct: false, publishProgress: true });
});

test("publishes progress when delta is large even inside the interval", () => {
  expect(
    shouldPublishAutoplay(0, 0.2, 0, 0.05, 100, 110, 33, 0.01),
  ).toEqual({ publishAct: false, publishProgress: true });
});
