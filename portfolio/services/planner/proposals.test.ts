import { describeChange } from "./proposals";
import type { Snapshot } from "./types";
test("shows consequential changes including clearing dates, reopening and archiving", () => {
  const state = {
    items: [{ id: "task", text: "Workshop" }],
    areas: [{ id: "area", name: "Personal" }],
    routines: [],
  } as unknown as Snapshot;
  const description = describeChange(
    {
      action: "save_item",
      id: "task",
      revision: 2,
      date: null,
      due_date: "2026-09-11",
      area_id: "area",
      done: false,
      archived: true,
    },
    state,
  );
  expect(description).toContain("Workshop");
  expect(description).toContain("Plan for: Clear");
  expect(description).toContain("Due by: Sep 11, 2026");
  expect(description).toContain("Area: Personal");
  expect(description).toContain("Reopen");
  expect(description).toContain("Archive");
  expect(description).not.toContain("revision");
});
