"""Raw signals per product (no opinion, just counts) + daily snapshots.

The dashboard receives exactly these numbers and turns them into a score in
the browser, so every weight can be changed live without re-scraping.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

from . import db, landing, pricing


def _d(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def reprice(conn, cfg: dict) -> None:
    """Re-read prices from ad text (cheap; picks up new currency settings)."""
    for r in conn.execute("SELECT ad_id, country, title, body, link_description, price "
                          "FROM ads WHERE price_source IS NULL OR price_source='ad'").fetchall():
        ccfg = cfg["countries"].get(r["country"])
        if not ccfg:
            continue
        text = "\n".join(x for x in (r["title"], r["body"], r["link_description"]) if x)
        price = pricing.best_price(text, ccfg)
        if price != r["price"]:
            conn.execute("UPDATE ads SET price=?, price_source=? WHERE ad_id=? AND country=?",
                         (price, "ad" if price is not None else None, r["ad_id"], r["country"]))
    conn.commit()
    landing.apply_prices(conn)


def _estimated_trend(active_ads: int, new7: int, stopped7: int) -> float:
    """No history yet: compare with what was probably active 7 days ago."""
    before = active_ads - new7 + stopped7
    trend = (new7 - stopped7) / max(before, 3)
    if active_ads < 2 and trend > 0.1:
        trend = 0.1                      # one fresh ad is "new", not "rising"
    return max(-1.0, min(1.0, trend))


def compute_country(conn, country: str, today: date | None = None) -> list[dict]:
    """All products of a country with their raw signals."""
    today = today or date.today()
    week_ago = today - timedelta(days=7)
    ads_by_product = defaultdict(list)
    for a in conn.execute(
            "SELECT ad_id, product_id, page_id, page_name, start_date, first_seen, is_active, "
            "collation_count, price, stopped_on, end_date, landing_url, platforms "
            "FROM ads WHERE country=? AND product_id IS NOT NULL", (country,)):
        ads_by_product[a["product_id"]].append(a)
    history = defaultdict(list)
    for s in conn.execute(
            "SELECT s.* FROM product_snapshots s JOIN products p ON p.id=s.product_id "
            "WHERE p.country=? AND s.day>=? ORDER BY s.day",
            (country, (today - timedelta(days=45)).isoformat())):
        history[s["product_id"]].append(s)

    out = []
    for p in conn.execute("SELECT * FROM products WHERE country=?", (country,)):
        ads = ads_by_product.get(p["id"])
        if not ads:
            continue
        active = [a for a in ads if a["is_active"]]
        starts = lambda rows: [d for d in (_d(a["start_date"]) or _d(a["first_seen"])
                                           for a in rows) if d]
        if active:
            st = starts(active)
            days = (today - min(st)).days if st else 0
        else:                                   # how long the longest ad lived
            days = 0
            for a in ads:
                s0 = _d(a["start_date"]) or _d(a["first_seen"])
                e0 = _d(a["stopped_on"]) or _d(a["end_date"]) or today
                if s0:
                    days = max(days, (min(e0, today) - s0).days)
        sellers = {a["page_id"] for a in active}
        sellers_total = {a["page_id"] for a in ads}
        new7 = sum(1 for a in active if (_d(a["start_date"]) or today) >= week_ago)
        stopped7 = sum(1 for a in ads if not a["is_active"]
                       and (_d(a["stopped_on"]) or date.min) >= week_ago)
        prices = [a["price"] for a in (active or ads) if a["price"]]
        price = round(statistics.median(prices), 3) if prices else None
        price_min = min(prices) if prices else None

        hist = history.get(p["id"], [])
        past = [h for h in hist if 4 <= (today - _d(h["day"])).days <= 14]
        if past:
            ref = min(past, key=lambda h: abs((today - _d(h["day"])).days - 7))
            then = ref["active_ads"] + 2 * ref["sellers"]
            now = len(active) + 2 * len(sellers)
            trend = max(-1.0, min(1.0, (now - then) / max(then, 3)))
            trend_source = "history"
        else:
            trend = _estimated_trend(len(active), new7, stopped7)
            trend_source = "estimate"
        price_drop = 0.0
        priced = [h["price"] for h in hist if h["price"]]
        if priced and price:
            price_drop = round(max(0.0, (priced[0] - price) / priced[0]), 3)

        best = sorted(active or ads, key=lambda a: (-a["collation_count"],
                                                    a["start_date"] or "9999"))
        out.append({
            "id": p["id"], "country": country, "global_id": p["global_id"] or p["id"],
            "name": p["name"] or "Produit sans nom", "category": p["category"] or "Autre",
            "image": p["image"], "first_seen": p["first_seen"],
            "supplier": p["supplier_price"], "supplier_note": p["supplier_note"] or "",
            "active": bool(active), "days": days,
            "sellers": len(sellers), "sellers_stopped": len(sellers_total - sellers),
            "active_ads": len(active), "total_ads": len(ads),
            "new7": new7, "dups": max((a["collation_count"] or 1 for a in (active or ads)), default=1),
            "trend": round(trend, 3), "trend_source": trend_source,
            "price": price, "price_min": price_min, "price_drop": price_drop,
            "history": [[h["day"], h["sellers"], h["active_ads"]] for h in hist[-14:]],
            "ads": [{"id": a["ad_id"], "page": a["page_name"] or "", "page_id": a["page_id"],
                     "start": a["start_date"], "active": bool(a["is_active"]),
                     "copies": a["collation_count"], "price": a["price"],
                     "url": a["landing_url"] or ""} for a in best[:8]],
        })
    return out


def write_snapshots(conn, run_id: int, products: list[dict]) -> None:
    """One row per product per day (the last run of the day wins)."""
    day = db.today()
    conn.executemany(
        "INSERT OR REPLACE INTO product_snapshots(product_id, day, run_id, sellers, "
        "active_ads, total_ads, new7, dups, price, price_min) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(p["id"], day, run_id, p["sellers"], p["active_ads"], p["total_ads"], p["new7"],
          p["dups"], p["price"], p["price_min"]) for p in products])
    conn.commit()


def new_sellers(conn, country: str, days: int = 14, today: date | None = None) -> list[dict]:
    """E-commerce pages that entered the market recently: first seen by us
    AND their oldest ad started within the last `days` days."""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    out = []
    for r in conn.execute("""
            SELECT p.page_id, p.name, p.first_seen, p.likes,
                   MIN(a.start_date) first_ad, SUM(a.is_active) active_ads, COUNT(*) total_ads
            FROM pages p JOIN ads a ON a.page_id=p.page_id AND a.country=p.country
            WHERE p.country=? AND p.is_ecom=1 AND p.ignored=0 AND substr(p.first_seen,1,10)>=?
            GROUP BY p.page_id HAVING first_ad>=? AND active_ads>0
            ORDER BY first_ad DESC, active_ads DESC LIMIT 80""", (country, since, since)):
        prods = [dict(x) for x in conn.execute("""
            SELECT DISTINCT pr.id, pr.name FROM ads a JOIN products pr ON pr.id=a.product_id
            WHERE a.page_id=? AND a.country=? LIMIT 4""", (r["page_id"], country))]
        out.append({"page_id": r["page_id"], "name": r["name"] or r["page_id"],
                    "first_ad": r["first_ad"], "first_seen": (r["first_seen"] or "")[:10],
                    "likes": r["likes"], "active_ads": r["active_ads"],
                    "total_ads": r["total_ads"], "products": prods})
    return out
