"""SQLite storage. One file: data/goldmine.db"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT,
  status TEXT,            -- running | done | partial | failed
  trigger TEXT,           -- schedule | manual | panel
  stats TEXT, error TEXT);

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, country TEXT,
  kind TEXT,              -- keyword | page
  value TEXT, label TEXT,
  status TEXT DEFAULT 'pending',   -- pending | done | failed | skipped
  ads_found INTEGER DEFAULT 0, error TEXT, done_at TEXT);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, status);

CREATE TABLE IF NOT EXISTS pages(
  page_id TEXT, country TEXT, name TEXT, picture TEXT, categories TEXT,
  likes INTEGER, first_seen TEXT, last_seen TEXT,
  is_ecom INTEGER DEFAULT 0,
  watch INTEGER DEFAULT 0,          -- 0 no | 1 auto (snowball) | 2 added by hand
  ignored INTEGER DEFAULT 0,
  last_scanned TEXT,
  PRIMARY KEY(page_id, country));

CREATE TABLE IF NOT EXISTS ads(
  ad_id TEXT, country TEXT, page_id TEXT, page_name TEXT,
  start_date TEXT, end_date TEXT, is_active INTEGER,
  collation_count INTEGER, collation_id TEXT,
  body TEXT, title TEXT, link_description TEXT, caption TEXT, cta TEXT,
  landing_url TEXT, landing_domain TEXT, platforms TEXT, media TEXT,
  image_url TEXT, display_format TEXT,
  thumb TEXT, phash TEXT, media_tries INTEGER DEFAULT 0,
  price REAL, price_source TEXT, is_ecom INTEGER DEFAULT 0,
  found_via TEXT, first_seen TEXT, last_seen TEXT, last_run_id INTEGER,
  stopped_on TEXT,
  product_id INTEGER, pinned INTEGER DEFAULT 0,
  PRIMARY KEY(ad_id, country));
CREATE INDEX IF NOT EXISTS idx_ads_product ON ads(product_id);
CREATE INDEX IF NOT EXISTS idx_ads_page ON ads(page_id, country);

CREATE TABLE IF NOT EXISTS ad_snapshots(
  run_id INTEGER, ad_id TEXT, country TEXT, day TEXT,
  is_active INTEGER, collation_count INTEGER, price REAL,
  PRIMARY KEY(run_id, ad_id, country));

CREATE TABLE IF NOT EXISTS landings(
  url TEXT PRIMARY KEY, title TEXT, price REAL, platform TEXT,
  status TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  country TEXT, name TEXT, manual_name TEXT, category TEXT,
  image TEXT, phash TEXT,
  supplier_price REAL, supplier_note TEXT,
  global_id INTEGER, created_at TEXT, first_seen TEXT);
CREATE INDEX IF NOT EXISTS idx_products_country ON products(country);

CREATE TABLE IF NOT EXISTS product_snapshots(
  product_id INTEGER, day TEXT, run_id INTEGER,
  sellers INTEGER, active_ads INTEGER, total_ads INTEGER,
  new7 INTEGER, dups INTEGER, price REAL, price_min REAL,
  PRIMARY KEY(product_id, day));

CREATE TABLE IF NOT EXISTS sourcing(
  product_id INTEGER, store TEXT, title TEXT, price REAL, url TEXT,
  search_url TEXT, match INTEGER, checked_at TEXT,
  PRIMARY KEY(product_id, store));

CREATE TABLE IF NOT EXISTS google_ads(      -- Google ads leading to a seller's website
  domain TEXT, country TEXT, ads INTEGER, more INTEGER DEFAULT 0, advertiser TEXT,
  first_shown TEXT, last_shown TEXT, checked_at TEXT,
  PRIMARY KEY(domain, country));

CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);   -- small notes between runs

CREATE TABLE IF NOT EXISTS alerts(
  product_id INTEGER PRIMARY KEY, score REAL, sent_at TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return date.today().isoformat()


def connect(path=None) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(path or config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------ runs

def start_run(conn, trigger: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(started_at, status, trigger, stats) VALUES(?,?,?,?)",
        (now_iso(), "running", trigger, "{}"))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, stats: dict, error: str = "") -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, stats=?, error=? WHERE id=?",
        (now_iso(), status, json.dumps(stats, ensure_ascii=False), error, run_id))
    conn.commit()


def update_run_stats(conn, run_id: int, stats: dict) -> None:
    conn.execute("UPDATE runs SET stats=? WHERE id=?",
                 (json.dumps(stats, ensure_ascii=False), run_id))
    conn.commit()


# ------------------------------------------------------------------ ads

_MUTABLE = ("page_name", "start_date", "end_date", "is_active", "collation_count",
            "collation_id", "body", "title", "link_description", "caption", "cta",
            "landing_url", "landing_domain", "platforms", "media", "image_url",
            "display_format")


def upsert_ad(conn, ad: dict, run_id: int, found_via: str) -> bool:
    """Insert or refresh one ad + write its snapshot. Returns True if new."""
    row = dict(ad)
    row["platforms"] = json.dumps(ad.get("platforms") or [])
    row["media"] = json.dumps(ad.get("media") or [])
    now, day = now_iso(), today()
    exists = conn.execute(
        "SELECT price FROM ads WHERE ad_id=? AND country=?",
        (ad["ad_id"], ad["country"])).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO ads(ad_id, country, page_id, " + ", ".join(_MUTABLE) +
            ", found_via, first_seen, last_seen, last_run_id) VALUES(" +
            ",".join("?" * (3 + len(_MUTABLE) + 4)) + ")",
            [ad["ad_id"], ad["country"], ad["page_id"]] + [row[k] for k in _MUTABLE]
            + [found_via, now, now, run_id])
        price = None
    else:
        sets = ", ".join(f"{k}=?" for k in _MUTABLE)
        conn.execute(
            f"UPDATE ads SET {sets}, last_seen=?, last_run_id=?, stopped_on=NULL "
            "WHERE ad_id=? AND country=?",
            [row[k] for k in _MUTABLE] + [now, run_id, ad["ad_id"], ad["country"]])
        price = exists["price"]
    conn.execute(
        "INSERT OR REPLACE INTO ad_snapshots(run_id, ad_id, country, day, is_active, "
        "collation_count, price) VALUES(?,?,?,?,?,?,?)",
        (run_id, ad["ad_id"], ad["country"], day, ad["is_active"],
         ad["collation_count"], price))
    return exists is None


def upsert_page(conn, ad: dict) -> None:
    if not ad.get("page_id"):
        return
    now = now_iso()
    conn.execute(
        "INSERT INTO pages(page_id, country, name, picture, categories, likes, "
        "first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(page_id, country) DO UPDATE SET "
        "name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END, "
        "picture=CASE WHEN excluded.picture<>'' THEN excluded.picture ELSE picture END, "
        "likes=COALESCE(excluded.likes, likes), last_seen=excluded.last_seen",
        (ad["page_id"], ad["country"], ad.get("page_name") or "",
         ad.get("page_picture") or "",
         json.dumps(ad.get("page_categories") or [], ensure_ascii=False),
         ad.get("page_likes"), now, now))


def add_watch_page(conn, page_id: str, country: str, name: str = "", manual: bool = True) -> None:
    now = now_iso()
    conn.execute(
        "INSERT INTO pages(page_id, country, name, first_seen, last_seen, is_ecom, watch) "
        "VALUES(?,?,?,?,?,1,?) ON CONFLICT(page_id, country) DO UPDATE SET "
        "watch=excluded.watch, ignored=0",
        (page_id, country, name, now, now, 2 if manual else 1))
    conn.commit()


def rows(conn, sql: str, params=()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()
