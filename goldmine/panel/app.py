"""Local control panel (Flask, http://localhost:5000).

The only place that writes config/config.json. Everything is a plain HTML
form: no file editing, ever. Bound to 127.0.0.1 and protected by a per-start
token so another website cannot post to it.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import (alerts, config, db, export, googleads, grouping, pipeline, publish, scheduler,
                scoring, signals)
from ..defaults import MAGENTO, NICHES, PRESTASHOP

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
TOKEN = secrets.token_hex(16)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
GENERIC_STORE = {
    "item": PRESTASHOP["item"] + ", " + MAGENTO["item"],
    "title": PRESTASHOP["title"] + ", " + MAGENTO["title"],
    "price": MAGENTO["price"] + ", " + PRESTASHOP["price"],
}


# ------------------------------------------------------------------ helpers

@app.before_request
def _guard():
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.method == "POST" and request.form.get("_t") != TOKEN:
        abort(403)


@app.context_processor
def _globals():
    cfg = config.load()
    return {"token": TOKEN, "cfg": cfg, "running": bool(pipeline.running_pid()),
            "countries": cfg["countries"], "cur": _country(cfg)}


def _country(cfg) -> str:
    code = (request.values.get("c") or "").upper()
    if code in cfg["countries"]:
        return code
    active = config.active_countries(cfg)
    return active[0] if active else next(iter(cfg["countries"]))


def _num(value, default=None, cast=float):
    try:
        text = str(value).replace(",", ".").strip()
        return cast(float(text)) if text != "" else default
    except (TypeError, ValueError):
        return default


def _re_export() -> None:
    """Refresh the local dashboard files after a manual change."""
    conn = db.connect()
    try:
        export.export(conn, config.load())
    finally:
        conn.close()


def _back(endpoint, **kw):
    return redirect(url_for(endpoint, **kw))


# ------------------------------------------------------------------ status / run

def _runs(conn, limit=12):
    out = []
    for r in db.rows(conn, "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)):
        stats = json.loads(r["stats"] or "{}")
        out.append({"id": r["id"], "started": r["started_at"], "finished": r["finished_at"],
                    "status": r["status"], "trigger": r["trigger"], "error": r["error"] or "",
                    "ads": stats.get("ads_seen", 0), "new": stats.get("ads_new", 0),
                    "products": stats.get("new_products", 0),
                    "publish": stats.get("publish", ""), "blocked": bool(stats.get("blocked"))})
    return out


def _log_tail(lines=60) -> str:
    logs = sorted(config.LOG_DIR.glob("run_*.log"))
    if not logs:
        return ""
    try:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


@app.route("/")
def home():
    conn = db.connect()
    cfg = config.load()
    counts = {r["country"]: dict(r) for r in db.rows(conn, """
        SELECT country, COUNT(*) ads, SUM(is_active) active,
               COUNT(DISTINCT product_id) products FROM ads GROUP BY country""")}
    data = {"runs": _runs(conn), "log": _log_tail(), "counts": counts,
            "scheduled": scheduler.is_installed(),
            "times": scheduler.local_times(cfg["general"]["run_times"]),
            "pages_url": cfg["general"].get("dashboard_url") or publish.pages_url()}
    conn.close()
    return render_template("home.html", **data)


@app.route("/api/status")
def api_status():
    conn = db.connect()
    out = {"running": bool(pipeline.running_pid()), "runs": _runs(conn, 6), "log": _log_tail()}
    conn.close()
    return jsonify(out)


@app.post("/run")
def run_now():
    if pipeline.running_pid():
        flash("Un scan est déjà en cours.")
        return _back("home")
    mode = request.form.get("mode", "scan")
    args = ["run", "--trigger", "panel"] if mode == "scan" else ["process", "--publish"]
    subprocess.Popen([sys.executable, "-m", "goldmine", *args], cwd=str(config.ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    flash("Scan lancé. Le journal ci-dessous se met à jour tout seul."
          if mode == "scan" else "Recalcul et publication lancés.")
    return _back("home")


# ------------------------------------------------------------------ countries

@app.route("/pays")
def countries():
    return render_template("countries.html")


@app.post("/pays/<code>")
def country_save(code):
    cfg = config.load()
    c = cfg["countries"].get(code)
    if c is None:
        abort(404)
    f = request.form
    c["active"] = f.get("active") == "1"
    c["name"] = f.get("name", c["name"]).strip() or c["name"]
    c["currency"] = f.get("currency", c["currency"]).strip() or c["currency"]
    tokens = [t.strip() for t in f.get("currency_tokens", "").split(",") if t.strip()]
    if tokens:
        c["currency_tokens"] = tokens
    c["decimals"] = 3 if f.get("decimals") == "3" else 2
    for key in ("delivery_cost", "ad_cost", "margin_target", "price_min", "price_max"):
        c[key] = _num(f.get(key), c[key])
    c["return_rate"] = min(max(_num(f.get("return_rate"), c["return_rate"] * 100) / 100, 0), 0.9)
    old = {s["name"]: s for s in c.get("stores", [])}
    stores = []
    for line in f.get("stores", "").splitlines():
        if "|" not in line:
            continue
        name, url = (x.strip() for x in line.split("|", 1))
        if name and "{q}" in url and url.startswith("http"):
            base = copy.deepcopy(old.get(name, GENERIC_STORE))
            base.update(name=name, search_url=url, enabled=True)
            stores.append(base)
    c["stores"] = stores
    config.save(cfg)
    _re_export()
    flash(f"{c['name']} : réglages enregistrés.")
    return _back("countries")


@app.post("/pays-ajouter")
def country_add():
    cfg = config.load()
    code = re.sub(r"[^A-Z]", "", request.form.get("code", "").upper())[:2]
    name = request.form.get("name", "").strip()
    currency = request.form.get("currency", "").strip()
    if len(code) != 2 or not name or not currency:
        flash("Indiquez un code pays à 2 lettres (ex. EG), un nom et une monnaie.")
    elif code in cfg["countries"]:
        flash("Ce pays existe déjà.")
    else:
        cfg["countries"][code] = {
            "name": name, "flag": "", "active": False, "currency": currency,
            "currency_tokens": [currency], "decimals": 2, "price_min": 1, "price_max": 100000,
            "delivery_cost": 0, "return_rate": 0.2, "ad_cost": 0, "margin_target": 1,
            "keywords": [], "stores": [], "aliexpress_lang": "fr"}
        config.save(cfg)
        flash(f"{name} ajouté. Renseignez ses coûts et ses mots-clés, puis activez-le.")
    return _back("countries")


# ------------------------------------------------------------------ keywords

@app.route("/mots-cles")
def keywords():
    conn = db.connect()
    cfg = config.load()
    code = _country(cfg)
    stats = {r["value"]: dict(r) for r in db.rows(conn, """
        SELECT value, MAX(done_at) last, SUM(ads_found) ads FROM tasks
        WHERE kind='keyword' AND country=? AND status='done' GROUP BY value""", (code,))}
    conn.close()
    return render_template("keywords.html", stats=stats, niches=NICHES,
                           scanned=config.scan_keywords(cfg["countries"][code]))


@app.post("/mots-cles")
def keywords_edit():
    cfg = config.load()
    code = _country(cfg)
    kws = cfg["countries"][code]["keywords"]
    if request.form.get("niches_form"):
        chosen = [n for n in NICHES if n in request.form.getlist("niches")]
        cfg["countries"][code]["niches"] = chosen
        cfg["countries"][code]["niche_only"] = bool(request.form.get("niche_only")) and bool(chosen)
        flash("Niches enregistrées : " + (", ".join(chosen) if chosen else "aucune")
              + ". Elles seront utilisées dès le prochain scan.")
    if request.form.get("remove"):
        kws[:] = [k for k in kws if k != request.form["remove"]]
    for line in request.form.get("add", "").splitlines():
        kw = line.strip()
        if kw and kw not in kws:
            kws.append(kw)
    config.save(cfg)
    return _back("keywords", c=code)


# ------------------------------------------------------------------ watched pages

@app.route("/pages")
def pages():
    conn = db.connect()
    cfg = config.load()
    code = _country(cfg)
    rows = db.rows(conn, """
        SELECT p.*, (SELECT COUNT(*) FROM ads a WHERE a.page_id=p.page_id AND a.country=p.country
                     AND a.is_active=1) active_ads
        FROM pages p WHERE p.country=? AND (p.watch>0 OR p.ignored=1)
        ORDER BY p.ignored, p.watch DESC, active_ads DESC LIMIT 500""", (code,))
    conn.close()
    return render_template("pages.html", pages=rows)


@app.post("/pages")
def pages_edit():
    cfg = config.load()
    code = _country(cfg)
    conn = db.connect()
    f = request.form
    if f.get("add"):
        m = re.search(r"view_all_page_id=(\d+)", f["add"]) or re.search(r"(\d{6,})", f["add"])
        if m:
            db.add_watch_page(conn, m.group(1), code, f.get("name", "").strip(), manual=True)
            flash("Page ajoutée à la surveillance. Elle sera scannée au prochain scan.")
        else:
            flash("Collez l'identifiant de la page (des chiffres) ou le lien de ses pubs "
                  "dans la Bibliothèque publicitaire.")
    elif f.get("ignore"):
        conn.execute("UPDATE pages SET ignored=1, watch=0, is_ecom=0 WHERE page_id=? AND country=?",
                     (f["ignore"], code))
        flash("Page retirée : elle ne sera plus scannée et ses pubs ne comptent plus.")
    elif f.get("restore"):
        conn.execute("UPDATE pages SET ignored=0, watch=2, is_ecom=1 WHERE page_id=? AND country=?",
                     (f["restore"], code))
    conn.commit()
    conn.close()
    return _back("pages", c=code)


# ------------------------------------------------------------------ products

def _scored(conn, cfg, code):
    costs = scoring.costs_of(cfg["countries"][code])
    items = [(scoring.score(p, cfg["scoring"], costs), p) for p in signals.compute_country(conn, code)]
    items.sort(key=lambda x: -x[0]["score"])
    return items


@app.route("/produits")
def products():
    conn = db.connect()
    cfg = config.load()
    code = _country(cfg)
    q = request.args.get("q", "").strip().lower()
    items = [(r, p) for r, p in _scored(conn, cfg, code) if not q or q in p["name"].lower()]
    conn.close()
    return render_template("products.html", items=items[:300], total=len(items), q=q)


@app.post("/produits/fusionner")
def products_merge():
    ids = [int(x) for x in request.form.getlist("ids") if x.isdigit()]
    code = request.form.get("c", "")
    if len(ids) < 2:
        flash("Cochez au moins deux produits à fusionner.")
        return _back("products", c=code)
    conn = db.connect()
    keep = ids[0]
    done = sum(1 for other in ids[1:] if grouping.merge_products(conn, keep, other))
    conn.close()
    _re_export()
    flash(f"{done + 1} produits fusionnés en un seul.")
    return _back("product", pid=keep)


@app.route("/produit/<int:pid>")
def product(pid):
    conn = db.connect()
    cfg = config.load()
    prod = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if prod is None:
        conn.close()
        abort(404)
    ads = db.rows(conn, "SELECT * FROM ads WHERE product_id=? ORDER BY is_active DESC, "
                        "collation_count DESC, start_date", (pid,))
    sourcing_rows = db.rows(conn, "SELECT * FROM sourcing WHERE product_id=? AND price IS NOT NULL", (pid,))
    sig = next((p for p in signals.compute_country(conn, prod["country"]) if p["id"] == pid), None)
    res = scoring.score(sig, cfg["scoring"], scoring.costs_of(cfg["countries"][prod["country"]])) if sig else None
    google = googleads.by_product(conn, prod["country"]).get(pid)
    conn.close()
    return render_template("product.html", p=prod, ads=ads, sourcing=sourcing_rows, sig=sig, res=res,
                           google=google,
                           currency=cfg["countries"][prod["country"]]["currency"])


@app.post("/produit/<int:pid>")
def product_save(pid):
    conn = db.connect()
    f = request.form
    name = f.get("name", "").strip()
    price = _num(f.get("supplier_price"))
    conn.execute("UPDATE products SET manual_name=?, name=CASE WHEN ?<>'' THEN ? ELSE name END, "
                 "supplier_price=?, supplier_note=? WHERE id=?",
                 (name or None, name, name, price, f.get("supplier_note", "").strip()[:80], pid))
    conn.commit()
    conn.close()
    _re_export()
    flash("Produit enregistré. La marge utilise maintenant votre prix fournisseur."
          if price is not None else "Produit enregistré.")
    return _back("product", pid=pid)


@app.post("/produit/<int:pid>/separer")
def product_split(pid):
    conn = db.connect()
    new_id = grouping.split_product(conn, pid, request.form.getlist("ads"))
    conn.close()
    if new_id is None:
        flash("Cochez une partie des pubs (pas toutes) pour les séparer.")
        return _back("product", pid=pid)
    _re_export()
    flash("Les pubs cochées forment maintenant un nouveau produit.")
    return _back("product", pid=new_id)


@app.route("/thumbs/<path:name>")
def thumbs(name):
    return send_from_directory(str(config.THUMBS_DIR), name)


# ------------------------------------------------------------------ settings

@app.route("/reglages")
def settings():
    return render_template("settings.html", remote=publish.remote_url())


@app.post("/reglages")
def settings_save():
    cfg = config.load()
    f, g = request.form, cfg["general"]
    times = sorted({f"{int(h):02d}:{int(m):02d}" for h, m in re.findall(r"(\d{1,2})[:h](\d{2})", f.get("run_times", ""))
                    if int(h) < 24 and int(m) < 60})
    times_changed = times != g["run_times"]
    g["run_times"] = times
    for key, lo, hi in (("max_ads_per_run", 20, 5000), ("max_ads_per_search", 10, 500),
                        ("max_page_scans_per_run", 0, 200), ("max_landing_fetches_per_run", 0, 500),
                        ("max_sourcing_per_run", 0, 200), ("max_google_checks_per_run", 0, 300),
                        ("export_top_n", 20, 2000)):
        g[key] = int(min(max(_num(f.get(key), g[key]), lo), hi))
    g["headless"] = f.get("headless") == "1"
    g["publish"] = f.get("publish") == "1"
    g["dashboard_url"] = f.get("dashboard_url", "").strip()
    tg = cfg["telegram"]
    tg.update(enabled=f.get("tg_enabled") == "1", token=f.get("tg_token", "").strip(),
              chat_id=f.get("tg_chat_id", "").strip(),
              min_score=_num(f.get("tg_min_score"), tg["min_score"]))
    sc = cfg["scoring"]
    for key in sc["weights"]:
        sc["weights"][key] = max(_num(f.get("w_" + key), sc["weights"][key]), 0)
    th = sc["thresholds"]
    th["min_score"] = _num(f.get("t_min_score"), 0)
    th["min_margin"] = _num(f.get("t_min_margin"))
    th["max_sellers"] = int(_num(f.get("t_max_sellers"), 8))
    th["min_days"] = int(_num(f.get("t_min_days"), 0))
    sc["est_supplier_ratio"] = min(max(_num(f.get("est_supplier_ratio"), 35) / 100, 0.01), 0.95)
    config.save(cfg)
    messages = ["Réglages enregistrés."]
    if times_changed or not scheduler.is_installed():
        messages.append(scheduler.install(cfg)[1].capitalize() + ".")
    remote = f.get("remote", "").strip()
    if remote and remote != publish.remote_url():
        ok, out = publish.set_remote(remote)
        messages.append("Dépôt GitHub relié." if ok else f"Dépôt GitHub non relié : {out}")
    _re_export()
    flash(" ".join(messages))
    return _back("settings")


@app.post("/telegram-test")
def telegram_test():
    tg = config.load()["telegram"]
    if not (tg.get("token") and tg.get("chat_id")):
        flash("Enregistrez d'abord le jeton du bot et l'identifiant de discussion.")
    else:
        ok, info = alerts.send(tg["token"], tg["chat_id"], "⛏️ Gold Mine : test réussi, les alertes arriveront ici.")
        flash("Message de test envoyé sur Telegram." if ok else f"Échec de l'envoi : {info}")
    return _back("settings")


# ------------------------------------------------------------------ local dashboard preview

@app.route("/dashboard/")
@app.route("/dashboard/<path:name>")
def dashboard(name="index.html"):
    return send_from_directory(str(config.DOCS_DIR), name)


def serve(open_browser: bool = True) -> int:
    config.ensure_dirs()
    port = int(config.load()["general"].get("panel_port", 5000))
    url = f"http://localhost:{port}/"
    print(f"Panneau de contrôle Gold Mine : {url}   (Ctrl+C pour arrêter)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    return 0
