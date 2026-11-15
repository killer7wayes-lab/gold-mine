"""One full run: scrape -> process -> export -> alert -> publish.

Every search is a row in `tasks`, so a run that crashed (PC went to sleep,
power cut) is resumed by the next start instead of starting over.
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import config, db, pricing
from .defaults import SHOP_CTAS
from .textutil import ecom_hint_count, is_shop_url, platform_from_url

log = logging.getLogger("goldmine")
LOCK = config.DATA_DIR / "run.lock"
RESUME_HOURS = 6
MAX_BLOCKS = 3


# ------------------------------------------------------------------ lock

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid() -> int | None:
    """PID of a run in progress, or None."""
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def _take_lock() -> bool:
    if running_pid():
        return False
    config.ensure_dirs()
    LOCK.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        LOCK.unlink()
    except OSError:
        pass


def _setup_logging(run_id: int):
    config.ensure_dirs()
    path = config.LOG_DIR / f"run_{run_id:05d}.log"
    root = logging.getLogger("goldmine")
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if sys.stdout is not None:               # pythonw (scheduled task) has no console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return path


# ------------------------------------------------------------------ classify

def classify(ad: dict, country_cfg: dict, ignored_categories=()) -> tuple[float | None, int]:
    """-> (price found in the ad text, is_ecom 0/1)"""
    text = "\n".join(x for x in (ad.get("title"), ad.get("body"),
                                 ad.get("link_description")) if x)
    price = pricing.best_price(text, country_cfg)
    hints = ecom_hint_count(text)
    landing = ad.get("landing_url") or ""
    shop_cta = ad.get("cta") in SHOP_CTAS
    shop_landing = bool(platform_from_url(landing))
    is_ecom = (price is not None or hints >= 2
               or (hints >= 1 and (shop_cta or shop_landing))
               or (shop_cta and is_shop_url(landing)))
    cats = {c.lower() for c in ad.get("page_categories") or []}
    if cats & {c.lower() for c in ignored_categories}:
        is_ecom = False
    return price, int(bool(is_ecom))


def save_ads(conn, ads: list[dict], run_id: int, found_via: str, cfg: dict) -> dict:
    """Store a batch of scraped ads. Returns counters."""
    out = {"seen": 0, "new": 0, "ecom": 0}
    ignored_cats = cfg["general"].get("ignored_page_categories", [])
    for ad in ads:
        ccfg = cfg["countries"][ad["country"]]
        price, is_ecom = classify(ad, ccfg, ignored_cats)
        db.upsert_page(conn, ad)
        is_new = db.upsert_ad(conn, ad, run_id, found_via)
        conn.execute(
            "UPDATE ads SET is_ecom=MAX(is_ecom, ?), "
            "price=CASE WHEN ? IS NOT NULL AND (price_source IS NULL OR price_source='ad') THEN ? ELSE price END, "
            "price_source=CASE WHEN ? IS NOT NULL AND price_source IS NULL THEN 'ad' ELSE price_source END "
            "WHERE ad_id=? AND country=?",
            (is_ecom, price, price, price, ad["ad_id"], ad["country"]))
        if is_ecom and ad.get("page_id"):
            conn.execute(
                "UPDATE pages SET is_ecom=1, watch=MAX(watch, 1) "
                "WHERE page_id=? AND country=? AND ignored=0",
                (ad["page_id"], ad["country"]))
        out["seen"] += 1
        out["new"] += int(is_new)
        out["ecom"] += is_ecom
    conn.commit()
    return out


# ------------------------------------------------------------------ tasks

def build_tasks(conn, cfg: dict, run_id: int, countries: list[str]) -> None:
    """Queue this run's searches: keywords searched longest ago first, then
    the watched pages scanned longest ago."""
    gen = cfg["general"]
    for country in countries:
        ccfg = cfg["countries"][country]
        last = {r["value"]: r["d"] or "" for r in db.rows(
            conn, "SELECT value, MAX(done_at) d FROM tasks WHERE kind='keyword' "
                  "AND country=? AND status='done' GROUP BY value", (country,))}
        keywords = sorted(dict.fromkeys(k.strip() for k in ccfg["keywords"] if k.strip()),
                          key=lambda k: last.get(k, ""))
        for kw in keywords:
            conn.execute("INSERT INTO tasks(run_id, country, kind, value, label) VALUES(?,?,?,?,?)",
                         (run_id, country, "keyword", kw, kw))
        pages = db.rows(
            conn, "SELECT page_id, name FROM pages WHERE country=? AND watch>0 AND ignored=0 "
                  "ORDER BY watch DESC, COALESCE(last_scanned, '') ASC LIMIT ?",
            (country, int(gen["max_page_scans_per_run"])))
        for p in pages:
            conn.execute("INSERT INTO tasks(run_id, country, kind, value, label) VALUES(?,?,?,?,?)",
                         (run_id, country, "page", p["page_id"], p["name"] or p["page_id"]))
    conn.commit()


def _find_resumable(conn) -> int | None:
    limit = (datetime.now(timezone.utc) - timedelta(hours=RESUME_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        "SELECT r.id FROM runs r WHERE r.status='running' AND r.started_at>=? "
        "AND EXISTS(SELECT 1 FROM tasks t WHERE t.run_id=r.id AND t.status='pending') "
        "ORDER BY r.id DESC LIMIT 1", (limit,)).fetchone()
    return row["id"] if row else None


def _close_dead_runs(conn, keep: int | None) -> None:
    for r in db.rows(conn, "SELECT id FROM runs WHERE status='running'"):
        if r["id"] != keep:
            conn.execute("UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?",
                         (db.now_iso(), "interrompu (PC éteint ou programme fermé)", r["id"]))
            conn.execute("UPDATE tasks SET status='skipped' WHERE run_id=? AND status='pending'",
                         (r["id"],))
    conn.commit()


def _mark_stopped(conn, country: str, page_id: str, run_id: int) -> int:
    """A watched page was scanned to the end: its ads we did not see are stopped."""
    cur = conn.execute(
        "UPDATE ads SET is_active=0, stopped_on=?, end_date=COALESCE(end_date, ?) "
        "WHERE country=? AND page_id=? AND is_active=1 AND last_run_id<>?",
        (db.today(), db.today(), country, page_id, run_id))
    return cur.rowcount


def stage_scrape(conn, cfg: dict, run_id: int, stats: dict, countries: list[str]) -> None:
    from .scraper import AdLibraryScraper, Blocked, ScrapeError

    gen = cfg["general"]
    per_search = int(gen["max_ads_per_search"])
    budget = {c: max(int(gen["max_ads_per_run"]) // max(len(countries), 1), per_search)
              for c in countries}
    used = {c: int(conn.execute(
        "SELECT COALESCE(SUM(ads_found),0) FROM tasks WHERE run_id=? AND country=? AND status='done'",
        (run_id, c)).fetchone()[0]) for c in countries}
    blocks = fails = 0
    stats.setdefault("ads_seen", 0)
    stats.setdefault("ads_new", 0)
    stats.setdefault("searches", 0)

    def pending(kind, country):
        return db.rows(conn, "SELECT * FROM tasks WHERE run_id=? AND country=? AND kind=? "
                             "AND status='pending' ORDER BY id", (run_id, country, kind))

    def phases():
        # 60 % of the budget for keyword discovery, then watched pages,
        # then keywords again with whatever is left.
        for country in countries:
            for kind, share in (("keyword", 0.6), ("page", 1.0), ("keyword", 1.0)):
                for task in pending(kind, country):
                    if used[country] >= budget[country] * share:
                        break
                    yield task

    with AdLibraryScraper(gen) as scraper:
        stats["browser"] = scraper.browser_name
        for task in phases():
            country, kind, value = task["country"], task["kind"], task["value"]
            room = max(min(per_search, budget[country] - used[country]), 10)
            attempt, result = 0, None
            while attempt < 2 and result is None:
                attempt += 1
                try:
                    result = scraper.search(country, kind, value, room)
                    fails = 0
                except Blocked as exc:
                    blocks += 1
                    stats["blocks"] = blocks
                    if blocks >= MAX_BLOCKS:
                        log.warning("Facebook bloque (%s). Arrêt du scan pour cette fois.", exc)
                        conn.execute("UPDATE tasks SET status='skipped', error='bloqué' "
                                     "WHERE run_id=? AND status='pending'", (run_id,))
                        conn.commit()
                        stats["blocked"] = True
                        return
                    wait = min(900, 90 * 2 ** (blocks - 1)) + random.uniform(0, 30)
                    log.warning("Facebook bloque (%s). Pause de %d s puis nouvel essai.", exc, wait)
                    time.sleep(wait)
                except ScrapeError as exc:
                    fails += 1
                    log.warning("[%s] %s « %s »: %s", country, kind, task["label"], exc)
                    if attempt < 2:
                        time.sleep(random.uniform(10, 25))
            if result is None:
                conn.execute("UPDATE tasks SET status='failed', error='échec', done_at=? WHERE id=?",
                             (db.now_iso(), task["id"]))
                conn.commit()
                if fails >= 6:
                    log.warning("Trop d'échecs de suite: arrêt du scan pour cette fois.")
                    stats["blocked"] = True
                    return
                continue

            counters = save_ads(conn, result["ads"], run_id, f"{kind}:{value}", cfg)
            stopped = 0
            if kind == "page":
                conn.execute("UPDATE pages SET last_scanned=? WHERE page_id=? AND country=?",
                             (db.now_iso(), value, country))
                if result["complete"]:
                    stopped = _mark_stopped(conn, country, value, run_id)
            conn.execute("UPDATE tasks SET status='done', ads_found=?, done_at=? WHERE id=?",
                         (counters["seen"], db.now_iso(), task["id"]))
            conn.commit()
            used[country] += counters["seen"]
            stats["ads_seen"] += counters["seen"]
            stats["ads_new"] += counters["new"]
            stats["searches"] += 1
            db.update_run_stats(conn, run_id, stats)
            log.info("[%s] %s « %s »: %d pubs (%d nouvelles, %d e-commerce)%s",
                     country, "page" if kind == "page" else "mot-clé", task["label"],
                     counters["seen"], counters["new"], counters["ecom"],
                     f", {stopped} arrêtées" if stopped else "")
            scraper._pause("pause_search")

    conn.execute("UPDATE tasks SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))
    conn.commit()


def mark_stale(conn, cfg: dict) -> int:
    """Ads not seen for a long time are considered stopped."""
    days = int(cfg["general"].get("stale_days", 10))
    limit = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "UPDATE ads SET is_active=0, stopped_on=? WHERE is_active=1 AND last_seen<?",
        (db.today(), limit))
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------ stages after scraping

def stage_process(conn, cfg: dict, run_id: int, stats: dict, countries: list[str]) -> None:
    from . import grouping, landing, media, scoring, signals

    gen = cfg["general"]
    stats["stale"] = mark_stale(conn, cfg)
    signals.reprice(conn, cfg)

    stats["thumbs"] = media.fetch_missing(conn)
    log.info("Images: %d miniatures téléchargées", stats["thumbs"])
    stats["landings"] = landing.fetch_missing(conn, cfg, int(gen["max_landing_fetches_per_run"]))
    log.info("Pages de vente: %d lues", stats["landings"])

    all_countries = sorted({r[0] for r in conn.execute("SELECT DISTINCT country FROM ads")})
    stats["new_products"] = 0
    for country in all_countries:
        if country not in cfg["countries"]:
            continue
        res = grouping.group_country(conn, cfg, country)
        stats["new_products"] += res["new_products"]
        if res["new_ads"]:
            log.info("[%s] Regroupement: %d pubs -> %d nouveaux produits",
                     country, res["new_ads"], res["new_products"])
    stats["cross_links"] = grouping.link_countries(conn, cfg)

    # raw signals + today's snapshot (this is what trends are built from)
    stats["products"] = 0
    for country in all_countries:
        if country not in cfg["countries"]:
            continue
        products = signals.compute_country(conn, country)
        signals.write_snapshots(conn, run_id, products)
        stats["products"] += len(products)
        costs = scoring.costs_of(cfg["countries"][country])
        ranked = sorted(((scoring.score(p, cfg["scoring"], costs), p) for p in products),
                        key=lambda x: -x[0]["score"])
        for res, p in ranked[:5]:
            log.info("[%s] %3d/100  %s — %s", country, res["score"], p["name"][:50], res["reason"])

    from . import sourcing
    stats["sourcing"] = sourcing.update(conn, cfg, int(gen["max_sourcing_per_run"]))
    log.info("Sourcing: %d produits cherchés dans les magasins locaux", stats["sourcing"])

    from . import export
    stats["export"] = export.export(conn, cfg)
    log.info("Export: %(products)d produits, %(images)d images, %(countries)d pays", stats["export"])


# ------------------------------------------------------------------ entry point

def run(trigger: str = "manual", scrape: bool = True, only_countries=None,
        publish: bool = True) -> int:
    cfg = config.load()
    if not _take_lock():
        print("Un scan est déjà en cours.")
        return 2
    conn = db.connect()
    run_id, stats, status, error = None, {}, "failed", ""
    try:
        countries = [c for c in config.active_countries(cfg)
                     if not only_countries or c in only_countries]
        resumed = _find_resumable(conn) if scrape else None
        _close_dead_runs(conn, resumed)
        run_id = resumed or db.start_run(conn, trigger)
        log_path = _setup_logging(run_id)
        if resumed:
            log.info("Reprise du scan n°%d interrompu.", run_id)
            stats = json.loads(conn.execute("SELECT stats FROM runs WHERE id=?",
                                            (run_id,)).fetchone()[0] or "{}")
        else:
            log.info("Scan n°%d (%s) – pays: %s", run_id, trigger, ", ".join(countries) or "aucun")
            if scrape:
                build_tasks(conn, cfg, run_id, countries)
        stats["countries"] = countries
        stats["log"] = log_path.name
        if scrape and countries:
            stage_scrape(conn, cfg, run_id, stats, countries)
        stage_process(conn, cfg, run_id, stats, countries)

        from . import alerts, publish as publisher
        stats["alerts"] = alerts.run(conn, cfg)
        if stats["alerts"]:
            log.info("Telegram: %d alerte(s) envoyée(s)", stats["alerts"])
        if publish and cfg["general"].get("publish", True):
            ok, msg = publisher.publish()
            stats["publish"] = msg
            log.info("Publication: %s", msg)
        status = "partial" if stats.get("blocked") else "done"
        log.info("Terminé: %s", json.dumps(stats, ensure_ascii=False))
    except KeyboardInterrupt:
        status, error = "failed", "arrêté par l'utilisateur"
    except Exception as exc:                  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        log.error("ERREUR: %s\n%s", error, traceback.format_exc())
    finally:
        if run_id is not None:
            db.finish_run(conn, run_id, status, stats, error)
        conn.close()
        _release_lock()
    return 0 if status in ("done", "partial") else 1
