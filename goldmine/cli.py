"""Command line:  python -m goldmine <command>

  run        full run (scrape + process + export + alert + publish)
  process    everything except scraping (re-group, re-score, re-export)
  panel      start the local control panel
  setup      create the database and the scheduled tasks
  status     last runs
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="goldmine", description="Gold Mine")
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="scan complet")
    p_run.add_argument("--trigger", default="manual")
    p_run.add_argument("--country", action="append", help="limiter à un pays (code)")
    p_run.add_argument("--no-publish", action="store_true")
    sub.add_parser("process", help="recalculer sans scanner")
    sub.add_parser("panel", help="panneau de contrôle local")
    sub.add_parser("setup", help="installation (base + tâches planifiées)")
    sub.add_parser("status", help="derniers scans")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        from . import pipeline
        return pipeline.run(trigger=args.trigger, only_countries=args.country,
                            publish=not args.no_publish)
    if args.cmd == "process":
        from . import pipeline
        return pipeline.run(trigger="process", scrape=False, publish=False)
    if args.cmd == "panel":
        from .panel.app import serve
        return serve()
    if args.cmd == "setup":
        from . import scheduler
        return scheduler.setup()
    if args.cmd == "status":
        from . import db
        conn = db.connect()
        for r in db.rows(conn, "SELECT * FROM runs ORDER BY id DESC LIMIT 10"):
            stats = json.loads(r["stats"] or "{}")
            print(f"#{r['id']}  {r['started_at']}  {r['status']:8}  "
                  f"pubs={stats.get('ads_seen', 0)} nouvelles={stats.get('ads_new', 0)}  {r['error'] or ''}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
