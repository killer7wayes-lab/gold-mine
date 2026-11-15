"""Where to buy the product, and for how much.

- local stores of the country (config: search URL + CSS selectors): we run the
  store's own search with the product keywords and keep the closest result
- AliExpress: we only build a search link (never scraped)
- the manual supplier price entered in the control panel always wins in the
  margin; store prices are shown as a reference
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from . import db, pricing, scoring, signals
from .media import UA
from .textutil import normalize, search_terms

log = logging.getLogger("goldmine.sourcing")
RECHECK_DAYS = 7
MIN_MATCH = 60
_ARABIC = re.compile(r"[؀-ۿ]")


def aliexpress_link(name: str) -> str:
    terms = search_terms(name, max_words=6)
    if not terms:
        return ""
    host = "ar.aliexpress.com" if _ARABIC.search(terms) else "fr.aliexpress.com"
    return f"https://{host}/wholesale?SearchText={quote_plus(terms)}"


def store_search_url(store: dict, name: str) -> str:
    terms = search_terms(name, max_words=4)
    if not terms or "{q}" not in store.get("search_url", ""):
        return ""
    return store["search_url"].replace("{q}", quote_plus(terms))


def parse_results(html, store: dict, country_cfg: dict, base_url: str) -> list[dict]:
    """Search result page -> [{title, price, url}] (first 12 items)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select(store.get("item") or ".product-miniature")[:12]:
        link = item.select_one(store.get("title") or "a")
        if link is None:
            continue
        if link.name != "a":
            link = link.find("a") or link.find_parent("a") or link
        title = link.get_text(" ", strip=True) or link.get("title") or ""
        href = link.get("href") or ""
        price = None
        for el in item.select(store.get("price") or ".price"):
            if el.find_parent(["del", "s"]) or re.search(
                    r"old|regular|barr", " ".join(el.get("class") or []), re.I):
                continue
            text = el.get_text(" ", strip=True)
            found = pricing.extract_prices(text, {**country_cfg, "price_min": 0.5})
            if found:
                price = found[0]
                break
        if title and href:
            out.append({"title": title[:140], "price": price, "url": urljoin(base_url, href)})
    return out


def search_store(store: dict, name: str, country_cfg: dict) -> list[dict]:
    url = store_search_url(store, name)
    if not url:
        return []
    resp = requests.get(url, headers={**UA, "Accept-Language": "fr,en;q=0.7"}, timeout=20)
    resp.raise_for_status()
    return parse_results(resp.content, store, country_cfg, url)


def best_match(results: list[dict], name: str) -> tuple[dict | None, int]:
    """Closest result to the product name (0-100). None if nothing is close."""
    query = normalize(search_terms(name, max_words=6))
    best, best_score = None, 0
    for res in results:
        if res["price"] is None:
            continue
        sc = int(fuzz.token_set_ratio(query, normalize(res["title"])))
        if sc > best_score:
            best, best_score = res, sc
    if best_score < MIN_MATCH:
        return None, best_score
    return best, best_score


def update(conn, cfg: dict, limit: int) -> int:
    """Look up the best-scored products of each country in its local stores."""
    checked = 0
    recent = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for country, ccfg in cfg["countries"].items():
        stores = [s for s in ccfg.get("stores", []) if s.get("enabled", True)]
        if not stores or not ccfg.get("active"):
            continue
        costs = scoring.costs_of(ccfg)
        products = signals.compute_country(conn, country)
        products.sort(key=lambda p: -scoring.score(p, cfg["scoring"], costs)["score"])
        for p in products:
            if checked >= limit:
                return checked
            done = {r["store"] for r in conn.execute(
                "SELECT store FROM sourcing WHERE product_id=? AND checked_at>=?", (p["id"], recent))}
            todo = [s for s in stores if s["name"] not in done]
            if not todo or not search_terms(p["name"], 4):
                continue
            for store in todo:
                title = price = url = None
                match = 0
                try:
                    res, match = best_match(search_store(store, p["name"], ccfg), p["name"])
                    if res:
                        title, price, url = res["title"], res["price"], res["url"]
                except Exception as exc:              # noqa: BLE001 - store down / changed
                    log.info("%s indisponible: %s", store["name"], str(exc)[:80])
                conn.execute(
                    "INSERT OR REPLACE INTO sourcing(product_id, store, title, price, url, "
                    "search_url, match, checked_at) VALUES(?,?,?,?,?,?,?,?)",
                    (p["id"], store["name"], title, price, url,
                     store_search_url(store, p["name"]), match, db.now_iso()))
                conn.commit()
                time.sleep(random.uniform(1.0, 2.5))
            checked += 1
    return checked


def links_for(conn, product: dict, country_cfg: dict) -> dict:
    """Everything the dashboard shows under "Sourcing" for one product."""
    found = {r["store"]: r for r in conn.execute(
        "SELECT * FROM sourcing WHERE product_id=?", (product["id"],))}
    stores = []
    for store in country_cfg.get("stores", []):
        if not store.get("enabled", True):
            continue
        row = found.get(store["name"])
        entry = {"store": store["name"], "search": store_search_url(store, product["name"])}
        if row is not None and row["price"] is not None:
            entry.update(title=row["title"], price=row["price"], url=row["url"], match=row["match"])
        if entry["search"]:
            stores.append(entry)
    return {"aliexpress": aliexpress_link(product["name"]), "stores": stores}
