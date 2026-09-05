import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  addDays,
  dueLabel,
  iso,
  label,
  localToday,
  monthDays,
  onDay,
  parseDay,
  scheduledMinutes,
  weekdays,
  weekLabel,
  weekStart,
} from "../../services/planner/calendar";
import type { Command, Item } from "../../services/planner/types";
import { usePlanner } from "../../services/planner/usePlanner";
import { describeChange } from "../../services/planner/proposals";
import {
  AreaManager,
  FocusEditor,
  ItemEditor,
  ReviewEditor,
  RoutineManager,
} from "./Dialogs";
import { Icon } from "./Icon";
import styles from "./Planner.module.css";

type View = "week" | "month" | "year";
type Editor = { item?: Item; initial: Partial<Item> } | null;
function validDate(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(+parseDay(value)) &&
    iso(parseDay(value)) === value
  );
}
function ItemRow({
  item,
  day,
  toggle,
  edit,
  disabled,
}: {
  item: Item;
  day?: string;
  toggle: (item: Item) => void;
  edit: (item: Item) => void;
  disabled: boolean;
}) {
  return (
    <div
      className={`${styles.item} ${item.done ? styles.done : ""}`}
      data-item-id={item.id}
    >
      {item.kind !== "note" ? (
        <input
          aria-label={`Complete ${item.text}`}
          type="checkbox"
          checked={item.done}
          disabled={disabled}
          onChange={() => toggle(item)}
        />
      ) : (
        <span className={styles.noteMark} />
      )}
      <button className={styles.itemText} onClick={() => edit(item)}>
        {item.time && item.date === day ? <small>{item.time}</small> : null}
        <span>{item.text}</span>
        {item.due_date ? (
          <small className={styles.due}>{dueLabel(item.due_date)}</small>
        ) : null}
        {item.carried_from ? (
          <small>
            Carried from{" "}
            {label(item.carried_from, { month: "short", day: "numeric" })}
          </small>
        ) : null}
      </button>
    </div>
  );
}
function MiniMonth({
  date,
  items,
  open,
  compact = false,
}: {
  date: string;
  items: Item[];
  open: (day: string) => void;
  compact?: boolean;
}) {
  return (
    <div className={compact ? styles.miniMonth : styles.month}>
      {compact ? (
        <button
          className={styles.monthTitle}
          onClick={() => open(`${date.slice(0, 7)}-01`)}
        >
          {label(date, { month: "long" })}
        </button>
      ) : null}
      <div className={styles.monthGrid}>
        {weekdays.map((day) => (
          <div className={styles.monthWeekday} key={day}>
            {compact ? day.slice(0, 1) : day.slice(0, 3)}
          </div>
        ))}
        {monthDays(date).map((day) => {
          const entries = onDay(items, day);
          const outside = day.slice(0, 7) !== date.slice(0, 7);
          return (
            <button
              key={day}
              className={`${styles.calendarDay} ${outside ? styles.outside : ""} ${day === localToday() ? styles.today : ""}`}
              aria-label={label(day, {
                month: "long",
                day: "numeric",
                year: "numeric",
              })}
              onClick={() => open(day)}
            >
              <span>{Number(day.slice(-2))}</span>
              {compact ? (
                entries.some((i) => !i.done) ? (
                  <i />
                ) : null
              ) : (
                <div>
                  {entries.slice(0, 3).map((item) => (
                    <span
                      key={item.id}
                      className={`${styles.monthItem} ${item.done ? styles.monthDone : ""}`}
                    >
                      <i />
                      {item.text}
                      {item.due_date === day ? <small>Due</small> : null}
                    </span>
                  ))}
                  {entries.length > 3 ? (
                    <small>+{entries.length - 3} more</small>
                  ) : null}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
export default function Planner() {
  const router = useRouter();
  const { data, error, saving, execute, refresh } = usePlanner();
  const [selected, setSelected] = useState("");
  const [view, setView] = useState<View>("week");
  const [grouped, setGrouped] = useState(true);
  const [editor, setEditor] = useState<Editor>(null);
  const [panel, setPanel] = useState<
    "areas" | "focus" | "review" | "routines" | null
  >(null);
  const [actionError, setActionError] = useState("");
  const [light, setLight] = useState(false);
  useEffect(() => {
    if (router.isReady) {
      setSelected(
        validDate(router.query.date) ? router.query.date : localToday(),
      );
      setView(
        ["week", "month", "year"].includes(String(router.query.view))
          ? (router.query.view as View)
          : "week",
      );
    }
  }, [router.isReady, router.query.date, router.query.view]);
  const week = weekStart(selected || localToday());
  const weekdaysDates = Array.from({ length: 5 }, (_, i) => addDays(week, i));
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const currentWeek = data?.weeks[week];
  const items = data?.items.filter((i) => !i.archived) || [];
  const navigate = (date: string, nextView = view) => {
    setSelected(date);
    setView(nextView);
    void router.replace(
      { pathname: "/planner", query: { date, view: nextView } },
      undefined,
      { shallow: true },
    );
  };
  const shift = (direction: number) => {
    if (view === "week") navigate(addDays(week, direction * 7));
    else {
      const date = parseDay(selected || week);
      date.setUTCDate(1);
      if (view === "month") date.setUTCMonth(date.getUTCMonth() + direction);
      else date.setUTCFullYear(date.getUTCFullYear() + direction);
      navigate(iso(date));
    }
  };
  const act = async (command: Command) => {
    try {
      const result = await execute(command);
      setActionError("");
      return result;
    } catch (reason) {
      setActionError((reason as Error).message);
      throw reason;
    }
  };
  const toggle = (item: Item) => {
    void act({
      action: "save_item",
      id: item.id,
      revision: item.revision,
      done: !item.done,
    }).catch(() => undefined);
  };
  const edit = (item: Item) => setEditor({ item, initial: {} });
  const add = (initial: Partial<Item> = {}) =>
    setEditor({ initial: { week, ...initial } });
  const row = (item: Item, day?: string) => (
    <ItemRow
      key={item.id}
      item={item}
      day={day}
      toggle={toggle}
      edit={edit}
      disabled={saving}
    />
  );
  const groups =
    grouped && data?.areas.length
      ? [
          ...data.areas
            .filter(
              (area) =>
                !area.archived ||
                items.some(
                  (i) =>
                    i.area_id === area.id &&
                    days.some((day) => i.date === day || i.due_date === day),
                ),
            )
            .map((area) => ({
              id: area.id as string | null,
              name: area.name,
              color: area.color,
            })),
          ...(items.some(
            (i) =>
              !i.area_id &&
              days.some((day) => i.date === day || i.due_date === day),
          )
            ? [{ id: null, name: "General", color: "#7b8178" }]
            : []),
        ]
      : [{ id: null, name: "", color: "#59715d" }];
  if (!groups.length)
    groups.push({ id: null, name: "General", color: "#7b8178" });
  const groupEnabled = grouped && Boolean(data?.areas.length);
  const title =
    view === "week"
      ? weekLabel(week)
      : view === "month"
        ? label(selected || week, { month: "long", year: "numeric" })
        : (selected || week).slice(0, 4);
  const pending = data?.proposals.filter((p) => p.status === "proposed") || [];
  const goals = items.filter((i) => i.kind === "goal" && i.week === week);
  const todos = items.filter(
    (i) =>
      ["task", "note"].includes(i.kind) &&
      !i.date &&
      (i.week === week || !i.week),
  );
  const older = items.filter(
    (i) =>
      !i.done &&
      ["task", "goal", "deadline"].includes(i.kind) &&
      ((i.due_date && i.due_date < week) || (i.week && i.week < week)),
  );
  const openDay = (day: string) => navigate(day, "week");
  return (
    <div className={styles.app} data-light={light ? "true" : undefined}>
      <div className={styles.topbar}>
        <div className={styles.brand}>
          <Icon name="book" size={44} />
          <span>Planner</span>
        </div>
        <nav aria-label="Calendar view" className={styles.tabs}>
          {(["week", "month", "year"] as const).map((mode) => (
            <button
              key={mode}
              aria-pressed={view === mode}
              className={view === mode ? styles.selectedTab : ""}
              onClick={() => navigate(selected || week, mode)}
            >
              {mode[0].toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </nav>
        <div className={styles.topActions}>
          <button onClick={() => setPanel("areas")} disabled={!data}>
            <Icon name="areas" />
            Areas
          </button>
          <button
            className={styles.primary}
            onClick={() => add()}
            disabled={!data}
          >
            <Icon name="plus" />
            Add item
          </button>
        </div>
      </div>
      <div className={styles.workspace}>
        <div className={styles.heading}>
          <div>
            <h1>{selected ? title : "Your planner"}</h1>
            <p>A little intention. Room for real life.</p>
          </div>
          <div className={styles.navigation}>
            <button aria-label={`Previous ${view}`} onClick={() => shift(-1)}>
              <Icon name="left" />
            </button>
            <button aria-label={`Next ${view}`} onClick={() => shift(1)}>
              <Icon name="right" />
            </button>
            <button onClick={() => navigate(localToday())}>Today</button>
            {view === "week" ? (
              <label className={styles.groupToggle}>
                <input
                  type="checkbox"
                  checked={grouped}
                  onChange={(e) => setGrouped(e.target.checked)}
                />
                Group by area
              </label>
            ) : null}
          </div>
        </div>
        {error || actionError ? (
          <div role="alert" className={styles.error}>
            {actionError || error}
            <button
              onClick={() => {
                setActionError("");
                void refresh().catch((e) =>
                  setActionError((e as Error).message),
                );
              }}
            >
              Refresh
            </button>
          </div>
        ) : null}
        {!data ? (
          <div className={styles.loading}>
            {error ? (
              <>
                <h2>Connect your planner</h2>
                <p>
                  Start the local planner API and DynamoDB, then refresh this
                  page.
                </p>
              </>
            ) : (
              "Opening your week…"
            )}
          </div>
        ) : (
          <>
            {view === "week" ? (
              <>
                {currentWeek?.status === "closed" ? (
                  <div className={styles.closed}>
                    <span>This week is closed. Your review is saved.</span>
                    <button
                      onClick={() =>
                        void act({
                          action: "save_week",
                          week,
                          revision: currentWeek.revision,
                          status: "active",
                        }).catch(() => undefined)
                      }
                    >
                      Reopen week
                    </button>
                  </div>
                ) : null}
                <div className={styles.spread}>
                  <div className={styles.desktopWeek}>
                    <div
                      className={`${styles.weekGrid} ${!groupEnabled ? styles.ungrouped : ""}`}
                      style={{ "--row-count": groups.length } as CSSProperties}
                    >
                      {groupEnabled ? (
                        <div className={styles.dayHeading} />
                      ) : null}
                      {weekdaysDates.map((day, i) => (
                        <div
                          className={`${styles.dayHeading} ${day === localToday() ? styles.currentHeading : ""}`}
                          key={day}
                        >
                          {weekdays[i].slice(0, 3)}{" "}
                          <span>{Number(day.slice(-2))}</span>
                          {scheduledMinutes(items, day) >
                          data.preferences.daily_minutes ? (
                            <small>Over planned time</small>
                          ) : null}
                        </div>
                      ))}
                      {groups.map((group) => (
                        <WeekGroup
                          key={group.id || "all"}
                          group={group}
                          grouped={groupEnabled}
                          days={weekdaysDates}
                          items={items}
                          row={row}
                          add={add}
                        />
                      ))}
                    </div>
                  </div>
                  <div className={styles.mobileDays}>
                    {days.map((day, index) => (
                      <section key={day} className={styles.mobileDay}>
                        <div className={styles.sectionHeading}>
                          <h3>
                            {weekdays[index]}{" "}
                            <span>
                              {label(day, { month: "short", day: "numeric" })}
                            </span>
                          </h3>
                          <button
                            aria-label={`Add item for ${day}`}
                            onClick={() => add({ date: day })}
                          >
                            <Icon name="plus" />
                          </button>
                        </div>
                        {onDay(items, day).map((i) => row(i, day))}
                        <button
                          className={styles.mobileAdd}
                          onClick={() => add({ date: day })}
                        >
                          Add to {weekdays[index]}
                        </button>
                      </section>
                    ))}
                  </div>
                  <aside className={styles.sidebar} aria-label="Weekly notes">
                    <div className={styles.sideSections}>
                      <section className={styles.focus}>
                        <h3>This week&apos;s focus</h3>
                        <button
                          className={styles.focusText}
                          onClick={() => setPanel("focus")}
                        >
                          {currentWeek?.focus || "What matters this week?"}
                          <Icon name="edit" size={16} />
                        </button>
                      </section>
                      <section>
                        <div className={styles.sectionHeading}>
                          <h3>Weekly goals</h3>
                          <button
                            aria-label="Add weekly goal"
                            onClick={() => add({ kind: "goal" })}
                          >
                            <Icon name="plus" size={15} />
                          </button>
                        </div>
                        {goals.map((i) => row(i))}
                        {!goals.length ? (
                          <button
                            className={styles.emptyAction}
                            onClick={() => add({ kind: "goal" })}
                          >
                            Set a small intention
                          </button>
                        ) : null}
                      </section>
                      <section>
                        <div className={styles.sectionHeading}>
                          <h3>Things to do</h3>
                          <button
                            aria-label="Add unscheduled task"
                            onClick={() => add()}
                          >
                            <Icon name="plus" size={15} />
                          </button>
                        </div>
                        {todos.map((i) => row(i))}
                        {!todos.length ? (
                          <button
                            className={styles.emptyAction}
                            onClick={() => add()}
                          >
                            Capture something for later
                          </button>
                        ) : null}
                      </section>
                    </div>
                    <div className={styles.desktopWeekend}>
                      {[5, 6].map((index) => (
                        <section key={index} className={styles.weekend}>
                          <div className={styles.sectionHeading}>
                            <h3>
                              {weekdays[index]}{" "}
                              <span>
                                {Number(addDays(week, index).slice(-2))}
                              </span>
                            </h3>
                            <button
                              aria-label={`Add item for ${weekdays[index]}`}
                              onClick={() =>
                                add({ date: addDays(week, index) })
                              }
                            >
                              <Icon name="plus" size={15} />
                            </button>
                          </div>
                          {onDay(items, addDays(week, index)).map((i) =>
                            row(i, addDays(week, index)),
                          )}
                        </section>
                      ))}
                    </div>
                  </aside>
                </div>
              </>
            ) : view === "month" ? (
              <MiniMonth date={selected || week} items={items} open={openDay} />
            ) : (
              <div className={styles.yearGrid}>
                {Array.from({ length: 12 }, (_, i) => (
                  <MiniMonth
                    compact
                    key={i}
                    date={`${(selected || week).slice(0, 4)}-${String(i + 1).padStart(2, "0")}-01`}
                    items={items}
                    open={(day) => navigate(day, "month")}
                  />
                ))}
              </div>
            )}
            <div className={styles.bottomBar}>
              <span className={styles.saveStatus} role="status">
                <i />
                {saving
                  ? "Saving…"
                  : error
                    ? "Connection interrupted"
                    : actionError
                      ? "Change not saved"
                      : "All changes saved"}
              </span>
              <div>
                <button
                  className={styles.quiet}
                  onClick={() => setLight((value) => !value)}
                >
                  {light ? "System theme" : "Light theme"}
                </button>
                <button
                  className={styles.quiet}
                  onClick={() => setPanel("routines")}
                >
                  Routines
                </button>
                <button
                  className={styles.quiet}
                  onClick={() => setPanel("review")}
                >
                  Review week
                  <Icon name="right" size={16} />
                </button>
              </div>
            </div>
            {pending.length ? (
              <section
                className={styles.proposals}
                aria-label="Agent suggestions"
              >
                <h2>Suggestions for your plan</h2>
                {pending.map((proposal) => (
                  <article key={proposal.id}>
                    <div>
                      <h3>{proposal.title}</h3>
                      <p>{proposal.rationale}</p>
                      <ul>
                        {proposal.changes.map((change, index) => (
                          <li key={index}>{describeChange(change, data)}</li>
                        ))}
                      </ul>
                      {proposal.base_content_version !==
                      data.content_version ? (
                        <small>
                          The planner has changed. Ask the agent for an updated
                          suggestion.
                        </small>
                      ) : null}
                    </div>
                    <div className={styles.proposalActions}>
                      <button
                        className={styles.primary}
                        disabled={
                          saving ||
                          proposal.base_content_version !== data.content_version
                        }
                        onClick={() =>
                          void act({
                            action: "resolve_proposal",
                            id: proposal.id,
                            decision: "accept",
                          }).catch(() => undefined)
                        }
                      >
                        Accept
                      </button>
                      <button
                        disabled={saving}
                        onClick={() =>
                          void act({
                            action: "resolve_proposal",
                            id: proposal.id,
                            decision: "reject",
                          }).catch(() => undefined)
                        }
                      >
                        Reject
                      </button>
                    </div>
                  </article>
                ))}
              </section>
            ) : null}
            {view === "week" && older.length ? (
              <section className={styles.older}>
                <h2>Still open</h2>
                <p>
                  Earlier commitments, here when you are ready to revisit them.
                </p>
                <div>{older.map((i) => row(i))}</div>
              </section>
            ) : null}
          </>
        )}
      </div>
      {data && editor ? (
        <ItemEditor
          key={editor.item?.id || "new"}
          item={editor.item}
          initial={editor.initial}
          areas={data.areas}
          execute={act}
          close={() => setEditor(null)}
        />
      ) : null}
      {data && panel === "areas" ? (
        <AreaManager
          areas={data.areas}
          execute={act}
          close={() => setPanel(null)}
        />
      ) : null}
      {data && panel === "focus" ? (
        <FocusEditor
          snapshot={data}
          week={week}
          execute={act}
          close={() => setPanel(null)}
        />
      ) : null}
      {data && panel === "review" ? (
        <ReviewEditor
          snapshot={data}
          week={week}
          execute={act}
          close={() => setPanel(null)}
        />
      ) : null}
      {data && panel === "routines" ? (
        <RoutineManager
          snapshot={data}
          week={week}
          execute={act}
          close={() => setPanel(null)}
        />
      ) : null}
    </div>
  );
}
function WeekGroup({
  group,
  grouped,
  days,
  items,
  row,
  add,
}: {
  group: { id: string | null; name: string; color: string };
  grouped: boolean;
  days: string[];
  items: Item[];
  row: (item: Item, day?: string) => React.ReactNode;
  add: (initial: Partial<Item>) => void;
}) {
  return (
    <>
      {grouped ? (
        <div
          className={styles.areaLabel}
          style={{ "--area-color": group.color } as CSSProperties}
        >
          {group.name}
        </div>
      ) : null}
      {days.map((day) => (
        <div key={day} className={styles.cell}>
          <button
            aria-label={`Add item for ${group.name ? `${group.name} on ` : ""}${day}`}
            className={styles.cellAdd}
            onClick={() =>
              add({ date: day, area_id: grouped ? group.id : null })
            }
          >
            <Icon name="plus" size={13} />
          </button>
          {onDay(items, day)
            .filter((i) => !grouped || i.area_id === group.id)
            .map((i) => row(i, day))}
        </div>
      ))}
    </>
  );
}
