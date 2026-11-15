"""Does the seller also advertise on Google?

Google's public Ads Transparency Center (free, no login) answers one question:
"which ads lead to this website?". It cannot be searched by keyword, so it is
not a way to discover products. We only use it as a second opinion on the shops
the Facebook scan already found: a seller who pays on both networks is selling.

Limits, by design:
- the answer is about the shop's website, not about one product
- shops without their own domain (x.converty.shop, Messenger, WhatsApp) cannot
  be looked up
- Google is quick to answer "too many requests" (about 25 lookups in three
  minutes were enough on 2026-11-15). So: few lookups per scan, a long pause
  between two, each website at most once every 10 days, and at the first
  refusal we stop and leave Google alone for 12 hours. Never work around it.
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

from . import config, db, scoring, signals
from .media import UA
from .textutil import own_site

log = logging.getLogger("goldmine.googleads")
RPC = "https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives?authuser=0"
HOME = "https://adstransparency.google.com/"
RECHECK_DAYS = 10
ACTIVE_DAYS = 30                  # last Google ad seen within N days = still advertising
PAUSE = (6.0, 14.0)               # seconds between two lookups
COOLDOWN_HOURS = 12               # after "too many requests"
BLOCK_KEY = "google_blocked_until"
# Google's region codes are 2000 + the ISO numeric country code
REGIONS = {"TN": 2788, "MA": 2504, "DZ": 2012, "LY": 2434, "EG": 2818, "FR": 2250,
           "SA": 2682, "AE": 2784, "QA": 2634, "KW": 2414, "JO": 2400, "IQ": 2368,
           "SN": 2686, "CI": 2384}


def page_url(domain: str, country: str) -> str:
    """The public page where anyone can see this website's Google ads."""
    region = f"region={country}&" if country in REGIONS else ""
    return f"https://adstransparency.google.com/?{region}domain={domain}"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _day(creative: dict, key: str) -> str | None:
    try:
        stamp = int(creative[key]["1"])
        return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
    except (KeyError, TypeError, ValueError, OSError):
        return None


def parse(data: dict) -> dict:
    """Google's answer -> {ads, more, advertiser, first_shown, last_shown}."""
    creatives = [c for c in (data.get("1") or []) if isinstance(c, dict)]
    low = max(_int(data.get("4")), len(creatives))
    firsts = [d for d in (_day(c, "6") for c in creatives) if d]
    lasts = [d for d in (_day(c, "7") for c in creatives) if d]
    names = Counter(c["12"] for c in creatives if c.get("12"))
    return {
        "ads": low,
        "more": bool(data.get("2")) or _int(data.get("5")) > low,   # "at least" this many
        "advertiser": names.most_common(1)[0][0] if names else "",
        "first_shown": min(firsts) if firsts else None,
        "last_shown": max(lasts) if lasts else None,
    }


class Blocked(Exception):
    """Google asked us to slow down (HTTP 429 / its "unusual traffic" page)."""


def open_session() -> requests.Session:
    """Visit the public page once, like a browser would, and keep its cookies."""
    session = requests.Session()
    session.headers.update({**UA, "Accept-Language": "fr,en;q=0.7"})
    resp = session.get(HOME, timeout=25)
    if resp.status_code == 429 or "/sorry/" in resp.url:
        raise Blocked(f"http {resp.status_code}")
    return session


def lookup(domain: str, country: str, session: requests.Session | None = None) -> dict:
    """Ask Google which ads lead to `domain` (shown in `country` when known)."""
    query = {"12": {"1": domain, "2": True}}
    region = REGIONS.get(country)
    if region:
        query["8"] = [region]
    body = {"2": 40, "3": query, "7": {"1": 1, "2": 0, "3": region or 2840}}
    resp = (session or requests).post(
        RPC, data={"f.req": json.dumps(body)}, timeout=25,
        headers={**UA, "Accept-Language": "fr,en;q=0.7", "Referer": HOME})
    if resp.status_code == 429 or "/sorry/" in resp.url:
        raise Blocked(f"http {resp.status_code}")
    resp.raise_for_status()
    return parse(resp.json())


def sites_by_product(conn, country: str) -> dict[int, list[str]]:
    """product id -> websites of its sellers (their own domains only)."""
    out: dict[int, list[str]] = {}
    for r in conn.execute(
            "SELECT product_id, landing_url FROM ads WHERE country=? AND product_id IS NOT NULL "
            "AND landing_url<>'' ORDER BY is_active DESC, collation_count DESC", (country,)):
        site = own_site(r["landing_url"])
        if site and site not in out.setdefault(r["product_id"], []):
            out[r["product_id"]].append(site)
    return out


def blocked_until(conn) -> str:
    """"" or the time (UTC) until which we leave Google alone."""
    row = conn.execute("SELECT value FROM state WHERE key=?", (BLOCK_KEY,)).fetchone()
    return row["value"] if row and row["value"] > db.now_iso() else ""


def _cool_down(conn) -> None:
    until = (datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?,?)", (BLOCK_KEY, until))
    conn.commit()
    log.warning("Google demande de ralentir: vérification Google Ads en pause jusqu'à %s (UTC)", until)


def update(conn, cfg: dict, limit: int) -> int:
    """Look up the websites of the best-scored products. Returns how many."""
    if limit <= 0:
        return 0
    pause = blocked_until(conn)
    if pause:
        log.info("Google Ads: en pause jusqu'à %s (UTC), Google avait demandé de ralentir", pause)
        return 0
    recent = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    checked = failures = 0
    session = None
    for country in config.active_countries(cfg):
        ccfg = cfg["countries"][country]
        sites = sites_by_product(conn, country)
        if not sites:
            continue
        seen = {r["domain"] for r in conn.execute(
            "SELECT domain FROM google_ads WHERE country=? AND checked_at>=?", (country, recent))}
        costs = scoring.costs_of(ccfg)
        products = signals.compute_country(conn, country)
        products.sort(key=lambda p: -scoring.score(p, cfg["scoring"], costs)["score"])
        for p in products:
            for site in sites.get(p["id"], []):
                if site in seen:
                    continue
                if checked >= limit:
                    return checked
                seen.add(site)
                try:
                    session = session or open_session()
                    res = lookup(site, country, session)
                except Blocked:
                    _cool_down(conn)
                    return checked
                except Exception as exc:              # noqa: BLE001 - down / changed
                    failures += 1
                    log.info("Google Ads indisponible pour %s: %s", site, str(exc)[:80])
                    if failures >= 2:                 # try again next scan
                        log.warning("Google ne répond plus: vérification reportée au prochain scan")
                        return checked
                    time.sleep(random.uniform(*PAUSE))
                    continue
                failures = 0
                conn.execute(
                    "INSERT OR REPLACE INTO google_ads(domain, country, ads, more, advertiser, "
                    "first_shown, last_shown, checked_at) VALUES(?,?,?,?,?,?,?,?)",
                    (site, country, res["ads"], int(res["more"]), res["advertiser"],
                     res["first_shown"], res["last_shown"], db.now_iso()))
                conn.commit()
                checked += 1
                time.sleep(random.uniform(*PAUSE))
    return checked


def by_product(conn, country: str, today: date | None = None) -> dict[int, dict]:
    """product id -> what the dashboard shows. Products whose sellers have no
    website of their own, or were not looked up yet, are absent."""
    today = today or date.today()
    since = (today - timedelta(days=ACTIVE_DAYS)).isoformat()
    rows = {r["domain"]: r for r in conn.execute(
        "SELECT * FROM google_ads WHERE country=?", (country,))}
    out = {}
    for pid, sites in sites_by_product(conn, country).items():
        found = [rows[s] for s in sites if s in rows]
        if not found:
            continue
        best = max(found, key=lambda r: (r["ads"] > 0, r["last_shown"] or "", r["ads"]))
        if best["ads"] <= 0:
            out[pid] = {"site": best["domain"], "ads": 0}
            continue
        out[pid] = {
            "site": best["domain"], "ads": best["ads"], "more": bool(best["more"]),
            "last": best["last_shown"], "active": (best["last_shown"] or "") >= since,
            "advertiser": best["advertiser"] or "", "url": page_url(best["domain"], country),
        }
    return out
