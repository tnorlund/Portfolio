import { dueLabel } from "./calendar";
import { weekStart, monthDays, addDays, weekLabel, onDay } from "./calendar";
import type { Item } from "./types";
test("Monday week identity survives year boundaries and daylight-saving transitions", () => {
  expect(weekStart("2027-01-01")).toBe("2026-12-28");
  expect(addDays("2026-03-08", 1)).toBe("2026-03-09");
  expect(weekLabel("2026-12-28")).toBe("December 28–Jan 3, 2027");
});
test("month includes the complete leading and trailing weeks", () => {
  expect(monthDays("2026-08-15")).toHaveLength(42);
  expect(monthDays("2026-08-15")[0]).toBe("2026-07-27");
  expect(monthDays("2026-02-01").at(-1)).toBe("2026-03-01");
});
test("a task on its deadline appears once, and on both distinct planning and due dates", () => {
  const item = {
    id: "a",
    kind: "task",
    archived: false,
    date: "2026-09-07",
    due_date: "2026-09-07",
  } as Item;
  expect(onDay([item], "2026-09-07")).toHaveLength(1);
  expect(
    onDay([{ ...item, due_date: "2026-09-09" }], "2026-09-09"),
  ).toHaveLength(1);
});

test("future calendar cells do not describe their deadline as today", () => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-09-05T18:00:00Z"));
  expect(dueLabel("2026-09-10")).toBe("Due Sep 10");
  expect(dueLabel("2026-09-05")).toBe("Due today");
  jest.useRealTimers();
});
