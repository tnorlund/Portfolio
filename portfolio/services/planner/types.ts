export type ItemKind = "task" | "goal" | "note" | "deadline" | "event";
export interface Item {
  id: string;
  revision: number;
  text: string;
  kind: ItemKind;
  date: string | null;
  due_date: string | null;
  week: string | null;
  area_id: string | null;
  done: boolean;
  archived: boolean;
  notes: string;
  time: string;
  estimate_minutes: number;
  routine_id: string | null;
  carried_from: string | null;
}
export interface Area {
  id: string;
  revision: number;
  name: string;
  color: string;
  archived: boolean;
  sort_order: number;
}
export interface Routine {
  id: string;
  revision: number;
  text: string;
  weekdays: number[];
  active: boolean;
  area_id: string | null;
  starts_on: string | null;
  ends_on: string | null;
  estimate_minutes: number;
}
export interface Week {
  week: string;
  revision: number;
  focus: string;
  status: "active" | "closed";
  review: { wins: string; misses: string; carried_ids: string[] } | null;
}
export type Command = { action: string; [key: string]: unknown };
export interface Proposal {
  id: string;
  title: string;
  rationale: string;
  changes: Command[];
  status: "proposed" | "accepted" | "rejected";
  base_content_version: number;
}
export interface Snapshot {
  version: number;
  content_version: number;
  items: Item[];
  areas: Area[];
  routines: Routine[];
  weeks: Record<string, Week>;
  proposals: Proposal[];
  preferences: { daily_minutes: number; timezone: string };
  env: string;
  table: string;
}
