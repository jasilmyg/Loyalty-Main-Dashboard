"""
Regression check for Jazz AI: re-runs every approved golden query against ClickHouse and reports queries that now
fail, return no rows or run slowly. Run after schema changes, prompt changes or data migrations:

    python manage.py jazz_eval            # all approved golden queries
    python manage.py jazz_eval --slow-ms 5000
"""
from django.core.management.base import BaseCommand

from ai_agent.models import JazzGoldenQuery
from ai_agent.services import ch_assistant


class Command(BaseCommand):
    help = "Run all approved Jazz golden queries and report failures / empty results / slow queries."

    def add_arguments(self, parser):
        parser.add_argument("--slow-ms", type=int, default=8000, help="Flag queries slower than this (default 8000 ms).")

    def handle(self, *args, **opts):
        items = list(JazzGoldenQuery.objects.filter(approved=True))
        if not items:
            self.stdout.write("No approved golden queries yet (approve some in the admin or via 👍 from a staff account).")
            return
        bad = 0
        for g in items:
            res = ch_assistant.run_readonly_query(g.sql)
            if "error" in res:
                status, detail = "FAIL ", res["error"][:140]
            elif res["row_count"] == 0:
                status, detail = "EMPTY", "returned 0 rows"
            elif res["elapsed_ms"] > opts["slow_ms"]:
                status, detail = "SLOW ", f"{res['elapsed_ms']} ms"
            else:
                status, detail = "OK   ", f"{res['row_count']} rows, {res['elapsed_ms']} ms"
            bad += status.strip() != "OK"
            self.stdout.write(f"[{status}] #{g.id} {g.question[:70]} — {detail}")
        self.stdout.write(f"\n{len(items) - bad}/{len(items)} golden queries healthy.")
