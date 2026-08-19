import { RotoscopeWorkerClient } from "./workerClient";

test("treats a missing Worker constructor as unavailable", async () => {
  const original = global.Worker;
  // @ts-expect-error exercise the compatibility path
  global.Worker = undefined;
  const client = new RotoscopeWorkerClient();
  expect(client.available()).toBe(false);
  await expect(
    client.render({
      image: document.createElement("img"),
      width: 4,
      height: 4,
      options: {},
    }),
  ).resolves.toBeNull();
  global.Worker = original;
});
