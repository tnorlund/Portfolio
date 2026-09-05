import { label, weekdays } from "./calendar";
import type { Command, Snapshot } from "./types";
const names: Record<string, string> = {
  text: "Title",
  kind: "Type",
  date: "Plan for",
  due_date: "Due by",
  week: "Week",
  area_id: "Area",
  notes: "Notes",
  time: "Time",
  estimate_minutes: "Estimated minutes",
  name: "Name",
  color: "Color",
  sort_order: "Position",
  weekdays: "Days",
  starts_on: "Starts",
  ends_on: "Ends",
  daily_minutes: "Available minutes per day",
  focus: "Focus",
  status: "Week status",
  wins: "What went well",
  misses: "What got in the way",
};
export function describeChange(change: Command, state: Snapshot): string {
  const entity = [...state.items, ...state.areas, ...state.routines].find(
    (e) => e.id === change.id,
  );
  const subject = entity && ("text" in entity ? entity.text : entity.name);
  const actions: Record<string, string> = {
    save_item: change.id ? `Edit “${subject || "item"}”` : "Add item",
    save_area: change.id ? `Edit area “${subject || "area"}”` : "Add area",
    save_routine: change.id
      ? `Edit routine “${subject || "routine"}”`
      : "Add routine",
    save_week: "Update week",
    preferences: "Update planning preferences",
    plan_week: "Add routine instances",
    close_week: "Save review and close week",
  };
  const details = Object.entries(change)
    .filter(([key]) => !["action", "id", "revision"].includes(key))
    .map(([key, value]) => {
      if (key === "done") return value ? "Mark complete" : "Reopen";
      if (key === "archived") return value ? "Archive" : "Restore";
      if (key === "active") return value ? "Resume routine" : "Pause routine";
      if (key === "carry_ids")
        return `Carry forward: ${(value as string[]).map((id) => state.items.find((i) => i.id === id)?.text || "item").join(", ") || "none"}`;
      if (key === "area_id")
        value = state.areas.find((a) => a.id === value)?.name || "No area";
      if (key === "weekdays")
        value = (value as number[]).map((day) => weekdays[day]).join(", ");
      if (
        ["date", "due_date", "week", "starts_on", "ends_on"].includes(key) &&
        value
      )
        value = label(String(value), {
          month: "short",
          day: "numeric",
          year: "numeric",
        });
      return `${names[key] || key}: ${value === null || value === "" ? "Clear" : String(value)}`;
    });
  return [actions[change.action] || "Update plan", ...details].join(" · ");
}
