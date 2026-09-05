import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type {
  Area,
  Command,
  Item,
  ItemKind,
  Routine,
  Snapshot,
} from "../../services/planner/types";
import { weekdays } from "../../services/planner/calendar";
import { Icon } from "./Icon";
import styles from "./Planner.module.css";

export type Execute = (command: Command) => Promise<unknown>;
export function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    dialog
      ?.querySelector<HTMLInputElement>(
        'input:not([type="checkbox"]), textarea',
      )
      ?.focus();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      onCancel={close}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className={styles.dialogHeading}>
        <h2>{title}</h2>
        <button type="button" onClick={close} aria-label="Close dialog">
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
function ErrorText({ error }: { error: string }) {
  return error ? (
    <p className={styles.formError} role="alert">
      {error}
    </p>
  ) : null;
}

export function ItemEditor({
  item,
  initial,
  areas,
  execute,
  close,
}: {
  item?: Item;
  initial: Partial<Item>;
  areas: Area[];
  execute: Execute;
  close: () => void;
}) {
  const [draft, setDraft] = useState({
    text: item?.text || "",
    kind: item?.kind || initial.kind || "task",
    date: item?.date || initial.date || "",
    due_date: item?.due_date || "",
    area_id: item?.area_id || initial.area_id || "",
    notes: item?.notes || "",
    estimate_minutes: item?.estimate_minutes ?? 30,
    time: item?.time || "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const field = (key: string, value: string | number) =>
    setDraft((d) => ({ ...d, [key]: value }));
  async function save(archive = false) {
    setBusy(true);
    setError("");
    try {
      await execute({
        action: "save_item",
        ...draft,
        date: draft.date || null,
        due_date: draft.due_date || null,
        area_id: draft.area_id || null,
        week: item?.week || initial.week || null,
        ...(item ? { id: item.id, revision: item.revision } : {}),
        ...(archive ? { archived: true } : {}),
      });
      close();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={item ? "Edit item" : "Add an item"} close={close}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label>
          What would you like to do?
          <input
            autoFocus
            required
            maxLength={1000}
            value={draft.text}
            onChange={(e) => field("text", e.target.value)}
            placeholder="Give it a little space in your week"
          />
        </label>
        <div className={styles.formColumns}>
          <label>
            Type
            <select
              value={draft.kind}
              onChange={(e) => field("kind", e.target.value as ItemKind)}
            >
              <option value="task">Task</option>
              <option value="goal">Weekly goal</option>
              <option value="event">Appointment</option>
              <option value="deadline">Deadline</option>
              <option value="note">Note</option>
            </select>
          </label>
          <label>
            Focus area
            <select
              value={draft.area_id}
              onChange={(e) => field("area_id", e.target.value)}
            >
              <option value="">No area</option>
              {areas
                .filter((a) => !a.archived || a.id === draft.area_id)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className={styles.formColumns}>
          <label>
            Plan for
            <input
              type="date"
              value={draft.date}
              onInput={(e) => field("date", e.currentTarget.value)}
            />
          </label>
          <label>
            Due by
            <input
              type="date"
              value={draft.due_date}
              onInput={(e) => field("due_date", e.currentTarget.value)}
            />
          </label>
        </div>
        <div className={styles.formColumns}>
          <label>
            Time (optional)
            <input
              type="time"
              value={draft.time}
              onInput={(e) => field("time", e.currentTarget.value)}
            />
          </label>
          <label>
            Estimated minutes
            <input
              type="number"
              min="0"
              max="1440"
              value={draft.estimate_minutes}
              onChange={(e) =>
                field("estimate_minutes", Number(e.target.value))
              }
            />
          </label>
        </div>
        <label>
          Notes
          <textarea
            rows={3}
            maxLength={4000}
            value={draft.notes}
            onChange={(e) => field("notes", e.target.value)}
          />
        </label>
        <ErrorText error={error} />
        <div className={styles.dialogActions}>
          {item ? (
            <button
              type="button"
              className={styles.quiet}
              disabled={busy}
              onClick={() => void save(true)}
            >
              Archive item
            </button>
          ) : (
            <span />
          )}
          <button type="submit" className={styles.primary} disabled={busy}>
            {busy ? "Saving…" : "Save item"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AreaRow({ area, execute }: { area: Area; execute: Execute }) {
  const [name, setName] = useState(area.name);
  const [color, setColor] = useState(area.color);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(archive = false) {
    setBusy(true);
    try {
      await execute({
        action: "save_area",
        id: area.id,
        revision: area.revision,
        name,
        color,
        archived: archive ? !area.archived : area.archived,
      });
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={styles.areaRow}>
      <div>
        <input
          type="color"
          aria-label={`Color for ${area.name}`}
          value={color}
          onChange={(e) => setColor(e.target.value)}
        />
        <input
          aria-label={`Name for ${area.name}`}
          maxLength={40}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button disabled={busy} onClick={() => void save()}>
          Save
        </button>
        <button
          className={styles.quiet}
          disabled={busy}
          onClick={() => void save(true)}
        >
          {area.archived ? "Restore" : "Archive"}
        </button>
      </div>
      <ErrorText error={error} />
    </div>
  );
}
export function AreaManager({
  areas,
  execute,
  close,
}: {
  areas: Area[];
  execute: Execute;
  close: () => void;
}) {
  const [name, setName] = useState("");
  const [color, setColor] = useState("#59715d");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="Your focus areas" close={close}>
      <p>
        Make room for the parts of your life you want to organize. Rename or
        archive them as things change.
      </p>
      {areas.map((a) => (
        <AreaRow key={a.id} area={a} execute={execute} />
      ))}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await execute({ action: "save_area", name, color });
            setName("");
            setError("");
          } catch (reason) {
            setError((reason as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          New area
          <div className={styles.inlineForm}>
            <input
              type="color"
              aria-label="New area color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
            />
            <input
              required
              maxLength={40}
              placeholder="e.g. Projects, Health, Home"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button className={styles.primary} disabled={busy}>
              Add area
            </button>
          </div>
        </label>
        <ErrorText error={error} />
      </form>
    </Modal>
  );
}
export function FocusEditor({
  snapshot,
  week,
  execute,
  close,
}: {
  snapshot: Snapshot;
  week: string;
  execute: Execute;
  close: () => void;
}) {
  const [current] = useState(() => snapshot.weeks[week]);
  const [focus, setFocus] = useState(current?.focus || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="This week’s focus" close={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await execute({
              action: "save_week",
              week,
              focus,
              ...(current ? { revision: current.revision } : {}),
            });
            close();
          } catch (reason) {
            setError((reason as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          What matters most this week?
          <textarea
            autoFocus
            rows={4}
            value={focus}
            maxLength={1000}
            onChange={(e) => setFocus(e.target.value)}
          />
        </label>
        <ErrorText error={error} />
        <div className={styles.dialogActions}>
          <span />
          <button disabled={busy} className={styles.primary}>
            Save focus
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function ReviewEditor({
  snapshot,
  week,
  execute,
  close,
}: {
  snapshot: Snapshot;
  week: string;
  execute: Execute;
  close: () => void;
}) {
  const [current] = useState(() => snapshot.weeks[week]);
  const [wins, setWins] = useState(current?.review?.wins || "");
  const [misses, setMisses] = useState(current?.review?.misses || "");
  const [carry, setCarry] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const unfinished = snapshot.items.filter(
    (i) =>
      !i.archived &&
      !i.done &&
      i.week === week &&
      ["task", "goal"].includes(i.kind),
  );
  return (
    <Modal title="A moment to reflect" close={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await execute({
              action: "close_week",
              week,
              wins,
              misses,
              carry_ids: carry,
              ...(current ? { revision: current.revision } : {}),
            });
            close();
          } catch (reason) {
            setError((reason as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          What went well?
          <textarea
            rows={3}
            maxLength={4000}
            value={wins}
            onChange={(e) => setWins(e.target.value)}
          />
        </label>
        <label>
          What needs a little more room?
          <textarea
            rows={3}
            maxLength={4000}
            value={misses}
            onChange={(e) => setMisses(e.target.value)}
          />
        </label>
        <h3>Carry to next week</h3>
        <p>
          Selected items move to next week’s list without a scheduled day.
          Everything else stays where you left it.
        </p>
        <div className={styles.carryList}>
          {unfinished.length ? (
            unfinished.map((item) => (
              <label key={item.id} className={styles.checkLabel}>
                <input
                  type="checkbox"
                  checked={carry.includes(item.id)}
                  onChange={(e) =>
                    setCarry((ids) =>
                      e.target.checked
                        ? [...ids, item.id]
                        : ids.filter((id) => id !== item.id),
                    )
                  }
                />
                {item.text}
              </label>
            ))
          ) : (
            <p>No unfinished tasks for this week.</p>
          )}
        </div>
        <ErrorText error={error} />
        <div className={styles.dialogActions}>
          {current?.status === "closed" ? (
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await execute({
                    action: "save_week",
                    week,
                    revision: current.revision,
                    status: "active",
                  });
                  close();
                } catch (reason) {
                  setError((reason as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Reopen week
            </button>
          ) : (
            <span />
          )}
          <button disabled={busy} className={styles.primary}>
            Save review &amp; close week
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function RoutineManager({
  snapshot,
  week,
  execute,
  close,
}: {
  snapshot: Snapshot;
  week: string;
  execute: Execute;
  close: () => void;
}) {
  const [editing, setEditing] = useState<Routine | null>(null);
  const [draft, setDraft] = useState({
    text: "",
    weekdays: [0],
    area_id: "",
    starts_on: "",
    ends_on: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const perform = async (command: Command) => {
    setBusy(true);
    try {
      await execute(command);
      setError("");
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Recurring routines" close={close}>
      <p>
        Give regular commitments a place. Existing occurrences keep their edits
        and completion state.
      </p>
      <div className={styles.routineList}>
        {snapshot.routines.map((r) => (
          <div key={r.id}>
            <button
              className={styles.quiet}
              onClick={() => {
                setEditing(r);
                setDraft({
                  text: r.text,
                  weekdays: r.weekdays,
                  area_id: r.area_id || "",
                  starts_on: r.starts_on || "",
                  ends_on: r.ends_on || "",
                });
              }}
            >
              {r.text}
              <small>
                {r.weekdays.map((d) => weekdays[d].slice(0, 3)).join(", ")}
              </small>
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void perform({
                  action: "save_routine",
                  id: r.id,
                  revision: r.revision,
                  active: !r.active,
                })
              }
            >
              {r.active ? "Pause" : "Resume"}
            </button>
          </div>
        ))}
      </div>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (
            await perform({
              action: "save_routine",
              ...draft,
              starts_on: draft.starts_on || null,
              ends_on: draft.ends_on || null,
              area_id: draft.area_id || null,
              ...(editing
                ? { id: editing.id, revision: editing.revision }
                : {}),
            })
          ) {
            setEditing(null);
            setDraft({
              text: "",
              weekdays: [0],
              area_id: "",
              starts_on: "",
              ends_on: "",
            });
          }
        }}
      >
        <label>
          {editing ? "Edit routine" : "New routine"}
          <input
            required
            value={draft.text}
            maxLength={1000}
            onChange={(e) => setDraft((d) => ({ ...d, text: e.target.value }))}
          />
        </label>
        <div className={styles.weekdayChoices}>
          {weekdays.map((name, day) => (
            <label key={name}>
              <input
                type="checkbox"
                checked={draft.weekdays.includes(day)}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    weekdays: e.target.checked
                      ? [...d.weekdays, day]
                      : d.weekdays.filter((x) => x !== day),
                  }))
                }
              />
              {name.slice(0, 3)}
            </label>
          ))}
        </div>
        <label>
          Focus area
          <select
            value={draft.area_id}
            onChange={(e) =>
              setDraft((d) => ({ ...d, area_id: e.target.value }))
            }
          >
            <option value="">No area</option>
            {snapshot.areas
              .filter((a) => !a.archived)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
        <div className={styles.formColumns}>
          <label>
            Starts on
            <input
              type="date"
              value={draft.starts_on}
              onInput={(e) => {
                const value = e.currentTarget.value;
                setDraft((d) => ({ ...d, starts_on: value }));
              }}
            />
          </label>
          <label>
            Ends on
            <input
              type="date"
              value={draft.ends_on}
              onInput={(e) => {
                const value = e.currentTarget.value;
                setDraft((d) => ({ ...d, ends_on: value }));
              }}
            />
          </label>
        </div>
        <ErrorText error={error} />
        <div className={styles.dialogActions}>
          <button
            type="button"
            disabled={busy}
            onClick={() => void perform({ action: "plan_week", week })}
          >
            Add routines to this week
          </button>
          <button className={styles.primary} disabled={busy}>
            {editing ? "Save routine" : "Create routine"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
