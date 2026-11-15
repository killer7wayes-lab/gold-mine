"""SQLite -> JSON files read by the static dashboard (docs/).

  docs/data/index.json    countries, default settings, presets, last update
  docs/data/<CC>.json     products (raw signals only) + new sellers
  docs/data/img/          one small JPEG per exported product

Scores are NOT exported: the browser computes them from the raw signals with
docs/score.js, so the sliders re-rank everything instantly.
"""
from __future__ import annotations

import json
import logging
import shutil

from . import __version__, config, db, googleads, scoring, signals, sourcing

log = logging.getLogger("goldmine.export")


def _write(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def export(conn, cfg: dict) -> dict:
    data_dir = config.DOCS_DIR / "data"
    img_dir = data_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")
    top_n = int(cfg["general"]["export_top_n"])
    now = db.now_iso()
    countries, keep_images, total = [], set(), 0

    for code in config.active_countries(cfg):
        ccfg = cfg["countries"][code]
        costs = scoring.costs_of(ccfg)
        products = signals.compute_country(conn, code)
        products.sort(key=lambda p: -scoring.score(p, cfg["scoring"], costs)["score"])
        google = googleads.by_product(conn, code)
        out = []
        for p in products[:top_n]:
            img = ""
            if p["image"]:
                src = config.THUMBS_DIR / p["image"]
                name = f"{code}_{p['id']}.jpg"
                if src.exists():
                    dst = img_dir / name
                    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                        shutil.copyfile(src, dst)
                    keep_images.add(name)
                    img = f"data/img/{name}"
            out.append({
                "id": p["id"], "gid": p["global_id"], "name": p["name"], "cat": p["category"],
                "img": img, "first_seen": p["first_seen"], "active": p["active"],
                "days": p["days"], "sellers": p["sellers"],
                "sellers_stopped": p["sellers_stopped"], "active_ads": p["active_ads"],
                "total_ads": p["total_ads"], "new7": p["new7"], "dups": p["dups"],
                "trend": p["trend"], "trend_source": p["trend_source"],
                "price": _round(p["price"]), "price_min": _round(p["price_min"]),
                "price_drop": p["price_drop"], "supplier": _round(p["supplier"]),
                "supplier_note": p["supplier_note"], "history": p["history"],
                "ads": p["ads"], "sourcing": sourcing.links_for(conn, p, ccfg),
                "google": google.get(p["id"]),
            })
        _write(data_dir / f"{code}.json", {
            "country": code, "generated_at": now, "products": out,
            "total_products": len(products),
            "new_sellers": signals.new_sellers(conn, code),
        })
        total += len(out)
        countries.append({
            "code": code, "name": ccfg["name"], "flag": ccfg.get("flag", ""),
            "currency": ccfg["currency"], "products": len(out),
            "costs": {k: ccfg[k] for k in ("delivery_cost", "return_rate", "ad_cost",
                                           "margin_target")},
        })

    for old in img_dir.glob("*.jpg"):
        if old.name not in keep_images:
            old.unlink()
    for old in data_dir.glob("??.json"):
        if old.stem not in {c["code"] for c in countries}:
            old.unlink()
    codes = [c["code"] for c in countries]
    _write(data_dir / "index.json", {
        "version": __version__, "generated_at": now, "countries": countries,
        "default_country": "TN" if "TN" in codes else (codes[0] if codes else ""),
        "settings": cfg["scoring"], "presets": cfg["presets"],
    })
    return {"countries": len(countries), "products": total, "images": len(keep_images)}
