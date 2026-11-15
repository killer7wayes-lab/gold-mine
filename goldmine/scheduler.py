"""Windows scheduled task (twice a day by default) + first-time setup.

Run times are given in Tunis time (UTC+1, no summer time) and converted to
this PC's clock, so the scans happen at 07:00 and 14:00 Tunis time wherever
the PC is. No administrator rights needed: the task belongs to the current
user and runs only while that user is logged in.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from . import config

TASK_NAME = "GoldMine"
TUNIS = timezone(timedelta(hours=1))
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def local_times(run_times: list[str]) -> list[str]:
    """['07:00'] in Tunis time -> same instants on this PC's clock."""
    out = []
    today = datetime.now(TUNIS).date()
    for text in run_times:
        try:
            hh, mm = (int(x) for x in text.strip().split(":"))
            moment = datetime(today.year, today.month, today.day, hh, mm, tzinfo=TUNIS)
        except (ValueError, TypeError):
            continue
        out.append(moment.astimezone().strftime("%H:%M"))
    return sorted(set(out))


def _pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def task_xml(times: list[str]) -> str:
    triggers = "".join(
        f"<CalendarTrigger><StartBoundary>2025-01-01T{t}:00</StartBoundary><Enabled>true</Enabled>"
        f"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        for t in times)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Gold Mine - scan automatique des pubs</Description></RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{escape(_pythonw())}</Command>
    <Arguments>-m goldmine run --trigger schedule</Arguments>
    <WorkingDirectory>{escape(str(config.ROOT))}</WorkingDirectory>
  </Exec></Actions>
</Task>"""


def _schtasks(*args) -> tuple[int, str]:
    try:
        res = subprocess.run(["schtasks", *args], capture_output=True, text=True,
                             errors="replace", creationflags=_NO_WINDOW, timeout=60)
        return res.returncode, (res.stdout + res.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def install(cfg: dict) -> tuple[bool, str]:
    """Create or replace the scheduled task from cfg["general"]["run_times"]."""
    if os.name != "nt":
        return False, "planification automatique disponible sous Windows uniquement"
    times = local_times(cfg["general"].get("run_times") or [])
    if not times:
        remove()
        return True, "aucune heure choisie: planification désactivée"
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(task_xml(times))
        path = fh.name
    try:
        code, out = _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if code:
        return False, f"tâche planifiée non créée: {out[-200:]}"
    return True, "scans planifiés à " + " et ".join(times) + " (heure de ce PC)"


def remove() -> bool:
    return _schtasks("/Delete", "/TN", TASK_NAME, "/F")[0] == 0


def is_installed() -> bool:
    return _schtasks("/Query", "/TN", TASK_NAME)[0] == 0


def _browser_ok() -> str:
    """Name of a browser Playwright can drive, or ''."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            for channel in ("chrome", "msedge", None):
                try:
                    kwargs = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    pw.chromium.launch(**kwargs).close()
                    return channel or "chromium"
                except Exception:             # noqa: BLE001 - try the next one
                    continue
    except Exception:                         # noqa: BLE001
        pass
    return ""


def setup() -> int:
    """First-time setup, called by setup.bat. Safe to run again."""
    from . import db, export, publish
    print("\n=== Gold Mine : installation ===\n")
    config.ensure_dirs()
    cfg = config.load()
    if not config.CONFIG_PATH.exists():
        config.save(cfg)
    conn = db.connect()
    export.export(conn, cfg)                  # so the dashboard opens even before the first scan
    conn.close()
    print("[1/4] Base de données prête :", config.DB_PATH)

    browser = _browser_ok()
    if not browser:
        print("      Chrome/Edge introuvable, téléchargement de Chromium...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        browser = _browser_ok()
    print("[2/4] Navigateur :", browser or "AUCUN (installez Google Chrome puis relancez setup.bat)")

    ok, msg = install(cfg)
    print("[3/4] Planification :", msg)

    url = publish.pages_url()
    if url:
        print("[4/4] GitHub Pages :", url)
    else:
        print("[4/4] GitHub Pages : pas encore relié. Voir GUIDE_INSTALLATION.md, étape 4.")
    print("\nTerminé. Double-cliquez sur panel.bat pour ouvrir le panneau de contrôle,")
    print("puis cliquez sur « Lancer un scan ».\n")
    return 0 if browser else 1
