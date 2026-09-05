"""Validated commands with stable identity and atomic proposal semantics."""

import copy
import re
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from planner.errors import Conflict, ValidationError
from planner.store import Store, encode


def today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def day(value: object, optional: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value
    ):
        raise ValidationError("Use a calendar date in YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("That calendar date does not exist.") from exc
    return value


def week_start(value: str) -> str:
    parsed = date.fromisoformat(day(value))
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def text(value: object, name: str = "Text", limit: int = 1000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > limit
    ):
        raise ValidationError(f"{name} must have 1 to {limit} characters.")
    return value.strip()


def boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("Expected true or false.")
    return value


def number(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(
            f"Expected a whole number between {low} and {high}."
        )
    return value


FIELDS = {
    "save_item": {
        "id",
        "revision",
        "text",
        "kind",
        "date",
        "due_date",
        "week",
        "area_id",
        "done",
        "archived",
        "notes",
        "estimate_minutes",
        "time",
    },
    "save_area": {"id", "revision", "name", "color", "archived", "sort_order"},
    "save_routine": {
        "id",
        "revision",
        "text",
        "weekdays",
        "area_id",
        "active",
        "starts_on",
        "ends_on",
        "estimate_minutes",
    },
    "save_week": {"week", "revision", "focus", "status"},
    "preferences": {"daily_minutes"},
    "plan_week": {"week"},
    "close_week": {"week", "revision", "wins", "misses", "carry_ids"},
    "batch": {"changes"},
    "propose": {"title", "rationale", "changes"},
    "resolve_proposal": {"id", "decision"},
}


class Planner:
    def __init__(self, store: Store):
        self.store = store

    def snapshot(self) -> dict:
        return self.store.read()

    def execute(self, command: dict, request_id: str | None = None) -> dict:
        if not isinstance(command, dict):
            raise ValidationError("A change must be an object.")
        key = text(
            str(uuid4()) if request_id is None else request_id,
            "Request id",
            200,
        )
        return self.store.execute(
            key, command, lambda state: self._apply(state, command)
        )

    def _apply(self, state: dict, command: dict) -> object:
        action = command.get("action")
        if not isinstance(action, str) or action not in FIELDS:
            raise ValidationError("Unknown planner action.")
        unknown = set(command) - FIELDS[action] - {"action"}
        if unknown:
            raise ValidationError(
                "Unknown fields: " + ", ".join(sorted(unknown))
            )
        args = {
            key: value for key, value in command.items() if key != "action"
        }
        before = encode(state)
        result = getattr(self, "_" + action)(state, args)
        if (
            action not in {"propose", "resolve_proposal", "batch"}
            and encode(state) != before
        ):
            state["content_version"] += 1
        return copy.deepcopy(result)

    @staticmethod
    def _existing(state: dict, collection: str, args: dict) -> dict | None:
        if args.get("id") is None:
            return None
        if not isinstance(args["id"], str) or not args["id"]:
            raise ValidationError("Use the record's existing id to edit it.")
        item = next(
            (x for x in state[collection] if x["id"] == args["id"]), None
        )
        if item is None:
            raise ValidationError("That record no longer exists.")
        if (
            type(args.get("revision")) is not int
            or args["revision"] != item["revision"]
        ):
            raise Conflict(
                "This item changed. Refresh it before saving your edit."
            )
        return item

    @staticmethod
    def _area(state: dict, value: object) -> str | None:
        if value in (None, ""):
            return None
        if not any(x["id"] == value for x in state["areas"]):
            raise ValidationError("Choose an existing focus area.")
        return value

    @staticmethod
    def _persist(
        state: dict, collection: str, old: dict | None, new: dict
    ) -> dict:
        if old == new:
            return old
        new["revision"] = old["revision"] + 1 if old else 1
        if old:
            state[collection][state[collection].index(old)] = new
        else:
            state[collection].append(new)
        return new

    def _save_item(self, state: dict, args: dict) -> dict:
        old = self._existing(state, "items", args)
        new = (
            copy.deepcopy(old)
            if old
            else {
                "id": str(uuid4()),
                "revision": 0,
                "text": "",
                "kind": "task",
                "date": None,
                "due_date": None,
                "week": None,
                "area_id": None,
                "done": False,
                "archived": False,
                "notes": "",
                "time": "",
                "estimate_minutes": 30,
                "routine_id": None,
                "carried_from": None,
            }
        )
        for key, value in args.items():
            if key in {"id", "revision"}:
                continue
            if key == "text":
                value = text(value)
            elif key in {"date", "due_date", "week"}:
                value = day(value, optional=True)
                if key == "week" and value:
                    value = week_start(value)
            elif key == "area_id":
                value = self._area(state, value)
            elif key in {"done", "archived"}:
                value = boolean(value)
            elif key == "estimate_minutes":
                value = number(value, 0, 1440)
            elif key == "kind" and (
                not isinstance(value, str)
                or value
                not in {
                    "task",
                    "goal",
                    "note",
                    "deadline",
                    "event",
                }
            ):
                raise ValidationError(
                    "Choose task, goal, note, deadline, or event."
                )
            elif key == "notes":
                if not isinstance(value, str) or len(value) > 4000:
                    raise ValidationError(
                        "Notes must be at most 4000 characters."
                    )
            elif key == "time":
                if not isinstance(value, str) or (
                    value
                    and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value)
                ):
                    raise ValidationError("Use a 24-hour time, such as 14:30.")
            new[key] = value
        text(new["text"])
        if new["date"]:
            new["week"] = week_start(new["date"])
        if new["kind"] == "event" and not new["date"]:
            raise ValidationError("An appointment needs a scheduled date.")
        if new["kind"] == "deadline" and not new["due_date"]:
            raise ValidationError("A deadline needs a due date.")
        return self._persist(state, "items", old, new)

    def _save_area(self, state: dict, args: dict) -> dict:
        old = self._existing(state, "areas", args)
        new = (
            dict(old)
            if old
            else {
                "id": str(uuid4()),
                "revision": 0,
                "name": "",
                "color": "#65816a",
                "archived": False,
            }
        )
        new.setdefault("sort_order", len(state["areas"]))
        if "sort_order" in args:
            new["sort_order"] = number(args["sort_order"], 0, 10000)
        if "name" in args:
            new["name"] = text(args["name"], "Area name", 40)
        if "color" in args:
            if not isinstance(args["color"], str) or not re.fullmatch(
                r"#[0-9a-fA-F]{6}", args["color"]
            ):
                raise ValidationError("Use a six-digit hex color.")
            new["color"] = args["color"]
        if "archived" in args:
            new["archived"] = boolean(args["archived"])
        text(new["name"], "Area name", 40)
        return self._persist(state, "areas", old, new)

    def _save_routine(self, state: dict, args: dict) -> dict:
        old = self._existing(state, "routines", args)
        new = (
            dict(old)
            if old
            else {
                "id": str(uuid4()),
                "revision": 0,
                "text": "",
                "weekdays": [],
                "active": True,
                "area_id": None,
                "starts_on": None,
                "ends_on": None,
                "estimate_minutes": 30,
            }
        )
        for key, value in args.items():
            if key in {"id", "revision"}:
                continue
            if key == "text":
                value = text(value)
            elif key == "weekdays":
                if not isinstance(value, list) or not value:
                    raise ValidationError("Choose at least one weekday.")
                value = sorted(set(number(x, 0, 6) for x in value))
            elif key == "active":
                value = boolean(value)
            elif key in {"starts_on", "ends_on"}:
                value = day(value, optional=True)
            elif key == "area_id":
                value = self._area(state, value)
            elif key == "estimate_minutes":
                value = number(value, 0, 1440)
            new[key] = value
        text(new["text"])
        if not new["weekdays"]:
            raise ValidationError("Choose at least one weekday.")
        if (
            new["starts_on"]
            and new["ends_on"]
            and new["starts_on"] > new["ends_on"]
        ):
            raise ValidationError("The routine ends before it starts.")
        return self._persist(state, "routines", old, new)

    def _save_week(self, state: dict, args: dict) -> dict:
        week = week_start(args.get("week", today()))
        old = state["weeks"].get(week)
        if old and args.get("revision") != old["revision"]:
            raise Conflict("This week changed. Refresh before editing it.")
        new = (
            dict(old)
            if old
            else {
                "week": week,
                "revision": 0,
                "focus": "",
                "status": "active",
                "review": None,
            }
        )
        if "focus" in args:
            value = args["focus"]
            if not isinstance(value, str) or len(value) > 1000:
                raise ValidationError("Focus must be at most 1000 characters.")
            new["focus"] = value.strip()
        if "status" in args:
            if not isinstance(args["status"], str) or args["status"] not in {
                "active",
                "closed",
            }:
                raise ValidationError("Choose active or closed.")
            new["status"] = args["status"]
        if new != old:
            new["revision"] += 1
            state["weeks"][week] = new
        return new

    def _preferences(self, state: dict, args: dict) -> dict:
        state["preferences"]["daily_minutes"] = number(
            args.get("daily_minutes"), 15, 1440
        )
        return state["preferences"]

    def _plan_week(self, state: dict, args: dict) -> dict:
        week = week_start(args.get("week", today()))
        if state["weeks"].get(week, {}).get("status") == "closed":
            raise Conflict("Reopen this week before adding routines.")
        created = []
        ids = {x["id"] for x in state["items"]}
        for routine in state["routines"]:
            if not routine["active"]:
                continue
            for weekday in routine["weekdays"]:
                scheduled = (
                    date.fromisoformat(week) + timedelta(days=weekday)
                ).isoformat()
                if routine["starts_on"] and scheduled < routine["starts_on"]:
                    continue
                if routine["ends_on"] and scheduled > routine["ends_on"]:
                    continue
                identity = str(
                    uuid5(NAMESPACE_URL, routine["id"] + "/" + scheduled)
                )
                if identity in ids:
                    continue
                item = self._save_item(
                    state,
                    {
                        "text": routine["text"],
                        "date": scheduled,
                        "area_id": routine["area_id"],
                        "estimate_minutes": routine["estimate_minutes"],
                    },
                )
                item["id"], item["routine_id"] = identity, routine["id"]
                created.append(identity)
                ids.add(identity)
        return {"week": week, "created": created}

    def _close_week(self, state: dict, args: dict) -> dict:
        week = week_start(args.get("week", today()))
        old = state["weeks"].get(week)
        if old and args.get("revision") != old["revision"]:
            raise Conflict("This week changed. Refresh before reviewing it.")
        wins, misses = args.get("wins", ""), args.get("misses", "")
        if not all(
            isinstance(x, str) and len(x) <= 4000 for x in [wins, misses]
        ):
            raise ValidationError(
                "Review fields must be at most 4000 characters."
            )
        carry = args.get("carry_ids", [])
        if not isinstance(carry, list) or not all(
            isinstance(x, str) for x in carry
        ):
            raise ValidationError("Choose the tasks to carry.")
        next_week = (date.fromisoformat(week) + timedelta(days=7)).isoformat()
        for identity in set(carry):
            item = next(
                (x for x in state["items"] if x["id"] == identity), None
            )
            if (
                not item
                or item["week"] != week
                or item["done"]
                or item["archived"]
                or item["kind"] not in {"task", "goal"}
            ):
                raise ValidationError(
                    "Only unfinished tasks or goals from this week can carry forward."
                )
            item.update(
                date=None,
                week=next_week,
                carried_from=week,
                revision=item["revision"] + 1,
            )
        new = dict(old) if old else {"week": week, "revision": 0, "focus": ""}
        new.update(
            status="closed",
            revision=new["revision"] + 1,
            review={"wins": wins, "misses": misses, "carried_ids": carry},
        )
        state["weeks"][week] = new
        return new

    def _batch(self, state: dict, args: dict) -> list:
        changes = args.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
            raise ValidationError("Provide between 1 and 100 changes.")
        results = []
        for change in changes:
            if not isinstance(change, dict) or change.get("action") in {
                "batch",
                "propose",
                "resolve_proposal",
            }:
                raise ValidationError(
                    "Nested batches and proposals are not supported."
                )
            results.append(self._apply(state, change))
        return results

    def _propose(self, state: dict, args: dict) -> dict:
        title = text(args.get("title"), "Proposal title", 120)
        rationale = text(args.get("rationale"), "Reason", 2000)
        self._batch(copy.deepcopy(state), {"changes": args.get("changes")})
        proposal = {
            "id": str(uuid4()),
            "title": title,
            "rationale": rationale,
            "changes": args["changes"],
            "status": "proposed",
            "base_content_version": state["content_version"],
        }
        state["proposals"].append(proposal)
        return proposal

    def _resolve_proposal(self, state: dict, args: dict) -> dict:
        if args.get("decision") not in {"accept", "reject"}:
            raise ValidationError("Choose accept or reject.")
        proposal = next(
            (x for x in state["proposals"] if x["id"] == args.get("id")), None
        )
        if not proposal:
            raise ValidationError("That proposal does not exist.")
        if proposal["status"] != "proposed":
            return proposal
        if args["decision"] == "accept":
            if proposal["base_content_version"] != state["content_version"]:
                raise Conflict(
                    "The planner changed since this suggestion. Ask for an updated proposal."
                )
            self._batch(state, {"changes": proposal["changes"]})
        proposal["status"] = (
            "accepted" if args["decision"] == "accept" else "rejected"
        )
        return proposal
