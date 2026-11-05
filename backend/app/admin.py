"""Concept test housekeeping, run from the backend folder.

    .venv/bin/python -m app.admin export results.csv   Save each trip's choice and answer as a CSV
    .venv/bin/python -m app.admin reset                Permanently delete all trips and results
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import TextIO

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import concept
from .db import Base, SessionLocal, Trip, engine, init_db

COLUMNS = [
    "tester", "trip", "plan", "plan_title", "plan_total", "recommended", "cheapest_flights",
    "would_change", "note", "chosen_at", "answered_at", "trip_id", "search_id",
]


def _safe_cell(value: object) -> object:
    """Testers write the notes, and a spreadsheet would run one starting with = as a formula."""
    if isinstance(value, str) and value.lstrip()[:1] in ("=", "+", "-", "@"):
        return f"'{value}"
    return value


def write_csv(session: Session, out: TextIO) -> int:
    rows = concept.trip_rows(session)
    writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _safe_cell(v) for k, v in row.items()})
    return len(rows)


def delete_everything(bind: Engine) -> None:
    with bind.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(delete(table))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description="Concept test housekeeping.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="save each trip's choice and answer as a CSV")
    export.add_argument("path", help="CSV file to write")
    reset = commands.add_parser("reset", help="permanently delete all trips and results")
    reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        if args.command == "export":
            with open(args.path, "w", newline="", encoding="utf-8") as out:
                count = write_csv(session, out)
            print(f"Saved {count} trips to {args.path}")
            return 0
        trips = session.scalar(select(func.count()).select_from(Trip))

    target = engine.url.render_as_string(hide_password=True)
    if not args.yes:
        print(f"This permanently deletes {trips} trips and all results from {target}.")
        if input('Type "reset" to continue: ').strip() != "reset":
            print("Nothing was deleted.")
            return 1
    delete_everything(engine)
    print(f"Deleted {trips} trips and all results from {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
