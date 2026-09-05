import type { Item } from "./types";
export const weekdays = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
export function parseDay(value: string): Date {
  return new Date(`${value}T12:00:00Z`);
}
export function iso(value: Date): string {
  return value.toISOString().slice(0, 10);
}
export function addDays(value: string, count: number): string {
  const date = parseDay(value);
  date.setUTCDate(date.getUTCDate() + count);
  return iso(date);
}
export function weekStart(value: string): string {
  return addDays(value, -((parseDay(value).getUTCDay() + 6) % 7));
}
export function localToday(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}
export function label(
  value: string,
  options: Intl.DateTimeFormatOptions,
): string {
  return new Intl.DateTimeFormat("en-US", {
    ...options,
    timeZone: "UTC",
  }).format(parseDay(value));
}
export function weekLabel(week: string): string {
  const end = addDays(week, 6);
  return `${label(week, { month: "long", day: "numeric" })}–${label(end, { ...(week.slice(0, 7) !== end.slice(0, 7) ? { month: "short" } : {}), day: "numeric" })}, ${end.slice(0, 4)}`;
}
export function dueLabel(date: string): string {
  return date === localToday()
    ? "Due today"
    : `Due ${label(date, { month: "short", day: "numeric" })}`;
}
export function monthDays(value: string): string[] {
  const start = weekStart(`${value.slice(0, 7)}-01`);
  const monthEnd = new Date(
    Date.UTC(Number(value.slice(0, 4)), Number(value.slice(5, 7)), 0, 12),
  );
  const count =
    Math.ceil(((+monthEnd - +parseDay(start)) / 86400000 + 1) / 7) * 7;
  return Array.from({ length: count }, (_, i) => addDays(start, i));
}
export function onDay(items: Item[], day: string): Item[] {
  return items.filter(
    (item) =>
      !item.archived &&
      item.kind !== "goal" &&
      (item.date === day || item.due_date === day),
  );
}
export function scheduledMinutes(items: Item[], day: string): number {
  return items
    .filter(
      (i) =>
        !i.archived &&
        !i.done &&
        i.date === day &&
        ["task", "event"].includes(i.kind),
    )
    .reduce((sum, i) => sum + i.estimate_minutes, 0);
}
