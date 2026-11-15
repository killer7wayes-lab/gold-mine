"""Optional Telegram alert when a product crosses the chosen score.

Free: uses your own bot (created with @BotFather). One message per product,
sent once when it goes above the threshold; if it falls below and climbs
back later, you are told again.
"""
from __future__ import annotations

import html
import logging

import requests

from . import db, publish, scoring, signals

log = logging.getLogger("goldmine.alerts")
MAX_PER_RUN = 10


def send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"}, timeout=20)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "ok"
        try:
            return False, resp.json().get("description", f"http {resp.status_code}")
        except ValueError:
            return False, f"http {resp.status_code}"
    except requests.RequestException as exc:
        return False, type(exc).__name__


def message(p: dict, res: dict, ccfg: dict, dashboard_url: str = "") -> str:
    lines = [f"⛏️ <b>Gold Mine</b> – {ccfg.get('flag', '')} {html.escape(ccfg['name'])}",
             f"<b>{html.escape(p['name'])}</b>",
             f"Score <b>{res['score']}/100</b>",
             html.escape(res["reason"])]
    links = []
    if p.get("ads"):
        links.append(f'<a href="https://www.facebook.com/ads/library/?id={p["ads"][0]["id"]}">Voir la pub</a>')
    if dashboard_url:
        links.append(f'<a href="{html.escape(dashboard_url, quote=True)}">Tableau de bord</a>')
    if links:
        lines.append(" · ".join(links))
    return "\n".join(lines)


def run(conn, cfg: dict) -> int:
    """Send alerts for products that just crossed the threshold. Returns count."""
    tg = cfg["telegram"]
    if not (tg.get("enabled") and tg.get("token") and tg.get("chat_id")):
        return 0
    threshold = float(tg.get("min_score") or 75)
    dashboard = cfg["general"].get("dashboard_url") or publish.pages_url()
    already = {r["product_id"] for r in conn.execute("SELECT product_id FROM alerts")}
    sent, above = 0, set()
    pending = []
    for code, ccfg in cfg["countries"].items():
        if not ccfg.get("active"):
            continue
        costs = scoring.costs_of(ccfg)
        for p in signals.compute_country(conn, code):
            res = scoring.score(p, cfg["scoring"], costs)
            if p["active"] and res["score"] >= threshold:
                above.add(p["id"])
                if p["id"] not in already:
                    pending.append((res["score"], p, res, ccfg))
    pending.sort(key=lambda x: -x[0])
    for score, p, res, ccfg in pending[:MAX_PER_RUN]:
        ok, info = send(tg["token"], tg["chat_id"], message(p, res, ccfg, dashboard))
        if not ok:
            log.warning("Telegram: envoi impossible (%s)", info)
            break
        conn.execute("INSERT OR REPLACE INTO alerts(product_id, score, sent_at) VALUES(?,?,?)",
                     (p["id"], score, db.now_iso()))
        sent += 1
    if len(pending) > MAX_PER_RUN and sent == MAX_PER_RUN:
        rest = pending[MAX_PER_RUN:]
        send(tg["token"], tg["chat_id"],
             f"… et {len(rest)} autres produits au-dessus de {threshold:.0f}/100. "
             "Ouvrez le tableau de bord pour les voir.")
        conn.executemany("INSERT OR REPLACE INTO alerts(product_id, score, sent_at) VALUES(?,?,?)",
                         [(p["id"], sc, db.now_iso()) for sc, p, _, _ in rest])
    # products that dropped below the line can alert again later
    for pid in already - above:
        conn.execute("DELETE FROM alerts WHERE product_id=?", (pid,))
    conn.commit()
    return sent
