"""Read a landing page: product title, selling price, shop platform.

Plain HTTP (requests), one page per product URL, cached in `landings`.
Price sources, most reliable first:
  1. JSON-LD Product offers            (Shopify, WooCommerce, most themes)
  2. <meta product:price:amount>       (Shopify, YouCan, Open Graph shops)
  3. product JSON embedded in the page (YouCan, Converty, EasyOrders, Next.js)
  4. elements whose class says "price" (WooCommerce and everything else)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from . import db, pricing
from .media import CANDIDATE, UA
from .textutil import (clean_line, is_shop_url, normalize_url, platform_from_url,
                       tokens, url_path_is_product)

log = logging.getLogger("goldmine.landing")
MAX_BYTES = 1_500_000
PLATFORM_MARKS = [
    ("cdn.shopify.com", "Shopify"), ("woocommerce", "WooCommerce"),
    ("youcan.shop", "YouCan"), ("cdn.youcan", "YouCan"), ("converty", "Converty"),
    ("easyorders", "EasyOrders"), ("easy-orders", "EasyOrders"),
    ("lightfunnels", "Lightfunnels"), ("storeino", "Storeino"),
    ("prestashop", "PrestaShop"), ("wuilt", "Wuilt"),
]
_BAD_PRICE_CLASS = re.compile(r"old|compare|regular|was|before|barr|strike|del|shipping|delivery", re.I)
_GOOD_PRICE_CLASS = re.compile(r"sale|current|final|new|special|now|actual|product-price|price", re.I)


def _num(value) -> float | None:
    try:
        v = float(str(value).replace(",", ".").replace(" ", ""))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _in_range(value, country: dict) -> float | None:
    v = _num(value)
    if v is None:
        return None
    if country["price_min"] <= v <= country["price_max"]:
        return round(v, 3)
    return None


def _jsonld_price(soup, country):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, dict):
                offers = cur.get("offers")
                if offers is not None and str(cur.get("@type", "")).lower().find("product") >= 0:
                    for off in offers if isinstance(offers, list) else [offers]:
                        if isinstance(off, dict):
                            for key in ("price", "lowPrice"):
                                price = _in_range(off.get(key), country)
                                if price:
                                    return price
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return None


def _meta_price(soup, country):
    for prop in ("product:sale_price:amount", "product:price:amount", "og:price:amount"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            price = _in_range(tag["content"], country)
            if price:
                return price
    return None


_PRICE_KEYS = ("sale_price", "salePrice", "discounted_price", "price_after_discount",
               "final_price", "price")
_NAME_KEYS = ("name", "title", "product_name", "slug")


def _embedded_json_price(soup, country):
    """Product objects inside JSON <script> tags (Next.js, YouCan, Converty...)."""
    for tag in soup.find_all("script"):
        kind = (tag.get("type") or "").lower()
        raw = tag.string or ""
        if not raw or len(raw) > 900_000:
            continue
        if tag.get("id") == "__NEXT_DATA__" or kind == "application/json":
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            stack = [data]
            while stack:
                cur = stack.pop(0)                 # breadth first: main product first
                if isinstance(cur, list):
                    stack.extend(cur)
                elif isinstance(cur, dict):
                    if any(k in cur for k in _NAME_KEYS):
                        for key in _PRICE_KEYS:
                            if key in cur:
                                price = _in_range(cur[key], country)
                                if price:
                                    return price
                    stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return None


def _class_price(soup, country):
    best = []
    for el in soup.find_all(class_=re.compile("price", re.I))[:60]:
        classes = " ".join(el.get("class") or [])
        if _BAD_PRICE_CLASS.search(classes) or el.find_parent(["del", "s"]):
            continue
        ins = el.find("ins")
        text = (ins or el).get_text(" ", strip=True)
        if not text or len(text) > 80:
            continue
        prices = pricing.extract_prices(text, country)
        if not prices:
            continue
        rank = 0 if re.search(r"sale|current|final|special|new", classes, re.I) else 1
        best.append((rank, prices[-1] if ins is None and len(prices) > 1 else prices[0]))
        if rank == 0:
            break
    if not best:
        return None
    best.sort(key=lambda x: x[0])
    return best[0][1]


def parse(html, url: str, country: dict) -> dict:
    """`html` may be bytes: BeautifulSoup then reads the charset from the page."""
    soup = BeautifulSoup(html, "html.parser")
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string
    # "Produit | Boutique" or "TND89 | Produit": keep the part that says the most
    parts = [clean_line(x) for x in re.split(r"\s[|–—]\s", title) if clean_line(x)]
    title = max(parts, key=lambda x: len(tokens(x, drop_boilerplate=True)))[:120] if parts else ""
    low = html.lower()
    platform = platform_from_url(url) or next(
        (name for mark, name in PLATFORM_MARKS if mark in low), "")
    price = None
    for finder in (_jsonld_price, _meta_price, _embedded_json_price, _class_price):
        try:
            price = finder(soup, country)
        except Exception as exc:              # noqa: BLE001 - odd page, try next way
            log.debug("%s: %s", finder.__name__, exc)
        if price:
            break
    return {"title": title, "price": price, "platform": platform}


def fetch(url: str, country: dict) -> dict:
    try:
        resp = requests.get(url, headers={**UA, "Accept-Language": "fr,ar;q=0.8,en;q=0.6"},
                            timeout=15, stream=True)
        if resp.status_code >= 400:
            return {"title": "", "price": None, "platform": "", "status": f"http {resp.status_code}"}
        raw = resp.raw.read(MAX_BYTES, decode_content=True)
        out = parse(raw, resp.url or url, country)
        out["status"] = "ok"
        return out
    except Exception as exc:                  # noqa: BLE001
        return {"title": "", "price": None, "platform": "", "status": f"erreur: {type(exc).__name__}"}


def fetch_missing(conn, cfg: dict, limit: int) -> int:
    """Fetch landing pages we have not seen yet (ads without price first)."""
    todo = {}
    retry_after = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in conn.execute(
            f"SELECT a.landing_url, a.country FROM ads a WHERE a.landing_url<>'' AND {CANDIDATE} "
            f"ORDER BY (a.price IS NULL) DESC, a.is_active DESC, a.last_seen DESC"):
        norm = normalize_url(r["landing_url"])
        if not norm or norm in todo or not is_shop_url(r["landing_url"]):
            continue
        seen = conn.execute("SELECT status, fetched_at FROM landings WHERE url=?", (norm,)).fetchone()
        if seen and (seen["status"] == "ok" or (seen["fetched_at"] or "") > retry_after):
            continue                              # failures are retried after 3 days
        todo[norm] = (r["landing_url"], r["country"])
        if len(todo) >= limit:
            break
    for norm, (url, country) in todo.items():
        res = fetch(url, cfg["countries"][country])
        conn.execute("INSERT OR REPLACE INTO landings(url, title, price, platform, status, fetched_at) "
                     "VALUES(?,?,?,?,?,?)",
                     (norm, res["title"], res["price"], res["platform"], res["status"], db.now_iso()))
        conn.commit()
    apply_prices(conn)
    return len(todo)


def apply_prices(conn) -> None:
    """Ads with no price in their text take the price of their landing page."""
    cache = {r["url"]: r["price"] for r in conn.execute(
        "SELECT url, price FROM landings WHERE price IS NOT NULL")}
    if not cache:
        return
    for r in conn.execute("SELECT ad_id, country, landing_url FROM ads "
                          "WHERE price IS NULL AND landing_url<>''").fetchall():
        price = cache.get(normalize_url(r["landing_url"]))
        if price:
            conn.execute("UPDATE ads SET price=?, price_source='landing' WHERE ad_id=? AND country=?",
                         (price, r["ad_id"], r["country"]))
    conn.commit()


def title_for(conn, landing_url: str) -> str:
    """Title of the landing page, only when the URL is one product's page
    (a shop home page title is the shop's name, not a product)."""
    norm = normalize_url(landing_url or "")
    if not norm or not url_path_is_product(norm):
        return ""
    row = conn.execute("SELECT title FROM landings WHERE url=?", (norm,)).fetchone()
    return (row["title"] or "") if row else ""
