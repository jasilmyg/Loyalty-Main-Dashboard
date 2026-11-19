"""
Invalidate portal caches, refresh ClickHouse forecast snapshots, warm local caches,
and/or notify the remote Render web portal to refresh itself automatically.

Usage:
    python manage.py refresh_portal                  # local invalidate + warm + notify remote
    python manage.py refresh_portal --snapshots      # also update demand_forecast_log in ClickHouse
    python manage.py refresh_portal --remote-only    # only trigger Render webhook
    python manage.py refresh_portal --invalidate-only
"""
import json
from django.core.management.base import BaseCommand

from analytics.portal_refresh import (
    invalidate_all_caches,
    run_warm_tasks,
    trigger_remote_portal_refresh,
)


class Command(BaseCommand):
    help = "Refresh all portal caches, optional ClickHouse snapshots, and live Render portal."

    def add_arguments(self, parser):
        parser.add_argument("--invalidate-only", action="store_true", help="Only clear caches and bump epoch.")
        parser.add_argument("--remote-only", action="store_true", help="Only ping remote live portal webhook.")
        parser.add_argument("--no-remote", action="store_true", help="Skip pinging remote live portal.")
        parser.add_argument("--snapshots", action="store_true", help="Also run snapshot_demand_forecasts.")
        parser.add_argument("--snapshots-top", type=int, default=1000, help="Top SKUs for demand snapshots.")
        parser.add_argument("--no-warm", action="store_true", help="Skip synchronous local warm-up tasks.")
        parser.add_argument("--portal-url", type=str, default="", help="Override live portal base URL.")

    def handle(self, *args, **opts):
        if opts["snapshots"]:
            from django.core.management import call_command

            self.stdout.write(f"Updating demand_forecast_log (top {opts['snapshots_top']} SKUs)...")
            call_command("snapshot_demand_forecasts", top=opts["snapshots_top"])

        if not opts["remote_only"]:
            inv = invalidate_all_caches()
            self.stdout.write(f"Invalidated caches cleanly: epoch={inv['epoch']} removed={len(inv['removed_files'])}")
            if not opts["invalidate_only"] and not opts["no_warm"]:
                warm = run_warm_tasks()
                self.stdout.write("Local warm-up: " + json.dumps(warm, default=str))

        if not opts["no_remote"]:
            res = trigger_remote_portal_refresh(
                base_url=opts["portal_url"] or None,
                stock_updated=True,
                sales_updated=True,
                prewarm=True,
                source="manage-refresh-portal",
            )
            if res.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"Remote portal refresh OK: HTTP {res.get('status')}"))
            else:
                self.stdout.write(self.style.WARNING(f"Remote portal refresh skipped/failed: {res}"))
