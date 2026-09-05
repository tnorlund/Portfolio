"""Explicit opt-in fictional data, never personal records."""

from datetime import date, timedelta

from planner.service import week_start


def seed_demo(planner, selected_week):
    week = week_start(selected_week)
    base = date.fromisoformat(week)
    areas = {}
    for name, color in [
        ("Work", "#59715d"),
        ("Personal", "#9b7b4e"),
        ("Home", "#637d97"),
    ]:
        areas[name] = planner.execute(
            {"action": "save_area", "name": name, "color": color},
            "demo-area-" + name,
        )["result"]["id"]
    planner.execute(
        {
            "action": "save_week",
            "week": week,
            "focus": "Make space for what matters.",
        },
        "demo-focus-" + week,
    )
    examples = [
        (0, "Work", "Outline project proposal"),
        (1, "Work", "Review design notes"),
        (3, "Work", "Send project proposal"),
        (0, "Personal", "Morning walk"),
        (2, "Personal", "Read a chapter"),
        (4, "Personal", "Make weekend plans"),
        (1, "Home", "Book bike repair"),
        (4, "Home", "Pick up groceries"),
    ]
    for index, (offset, area, label) in enumerate(examples):
        scheduled = (base + timedelta(days=offset)).isoformat()
        command = {
            "action": "save_item",
            "text": label,
            "date": scheduled,
            "area_id": areas[area],
        }
        if index == 2:
            command["due_date"] = scheduled
        planner.execute(command, f"demo-item-{week}-{index}")
    for index, (kind, label) in enumerate(
        [
            ("goal", "Finish a first draft"),
            ("goal", "Get outside three times"),
            ("task", "Choose a new book"),
            ("task", "Sort the hallway shelf"),
        ]
    ):
        planner.execute(
            {"action": "save_item", "kind": kind, "text": label, "week": week},
            f"demo-sidebar-{week}-{index}",
        )
