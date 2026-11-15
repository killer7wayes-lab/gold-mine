"""Score /100 + money estimate + French reason.

THIS FILE AND docs/score.js MUST STAY IDENTICAL IN LOGIC.
The browser uses score.js (live sliders); Python uses this file for Telegram
alerts and the control panel. tests/test_parity.py runs both on the same
products and fails if they disagree.

Inputs
  s         raw signals of one product (signals.compute_country)
  settings  cfg["scoring"]  (weights, thresholds, curves)
  costs     country block   (delivery_cost, return_rate, ad_cost, margin_target, currency)
"""
from __future__ import annotations

import math

TIERS = ("low", "medium", "high")
TIER_FR = {"low": "faible", "medium": "moyen", "high": "élevé"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def cost_tier(s: dict, settings: dict) -> str:
    """Estimated cost-per-sale level. An estimate from competition and
    creative fatigue, never real data."""
    max_sellers = settings["thresholds"].get("max_sellers") or 8
    level = 0 if s["sellers"] <= 2 else (1 if s["sellers"] <= max_sellers else 2)
    if s["days"] > 60 and s["new7"] == 0:
        level += 1                      # same old creatives: tired audience
    if s["days"] < 14 and s["sellers"] <= 3:
        level -= 1                      # fresh product, cheap attention
    return TIERS[int(_clamp(level, 0, 2))]


def money(s: dict, settings: dict, costs: dict) -> dict:
    tier = cost_tier(s, settings)
    ad_cost = costs["ad_cost"] * settings["tier_multipliers"][tier]
    r = _clamp(costs["return_rate"], 0, 0.9)
    returns = r / (1 - r) * (costs["delivery_cost"] + ad_cost)
    out = {"tier": tier, "ad_cost": ad_cost, "returns": returns,
           "delivery": costs["delivery_cost"], "supplier": None,
           "supplier_estimated": True, "margin": None}
    price = s.get("price")
    if price is None:
        return out
    if s.get("supplier") is not None:
        out["supplier"], out["supplier_estimated"] = s["supplier"], False
    else:
        out["supplier"] = price * settings["est_supplier_ratio"]
    out["margin"] = price - out["supplier"] - costs["delivery_cost"] - ad_cost - returns
    return out


def sellers_curve(n: int, max_sellers: float) -> float:
    """1 seller = unproven, 3+ = validated, close to the limit = crowded."""
    if n <= 0:
        return 0.0
    if n == 1:
        return 0.4
    if n == 2:
        return 0.7
    sweet_end = max(3, round(max_sellers * 0.6))
    if n <= sweet_end:
        return 1.0
    return max(0.15, 1 - 0.6 * (n - sweet_end) / max(max_sellers - sweet_end, 1))


def trend_label(s: dict) -> str:
    if not s["active"] or s["trend"] <= -0.15:
        return "dying"
    return "rising" if s["trend"] >= 0.15 else "stable"


def price_war(s: dict) -> bool:
    if s.get("price_drop", 0) >= 0.15:
        return True
    return bool(s["sellers"] >= 3 and s.get("price") and s.get("price_min")
                and s["price_min"] <= s["price"] * 0.65)


def _fmt(value: float) -> str:
    return str(math.floor(value + 0.5))          # half up, like the JS twin


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


def reason(s: dict, m: dict, costs: dict) -> str:
    parts = []
    if s["active"]:
        parts.append(f"Actif depuis {_plural(s['days'], 'jour', 'jours')}")
    else:
        parts.append("Plus aucune pub active")
    parts.append(_plural(s["sellers"], "vendeur", "vendeurs"))
    if s["new7"] > 0:
        parts.append(f"{_plural(s['new7'], 'nouvelle pub', 'nouvelles pubs')} cette semaine")
    if s["dups"] >= 3:
        parts.append(f"créatif dupliqué {s['dups']}×")
    label = trend_label(s)
    if label == "rising":
        parts.append("en hausse")
    elif label == "dying" and s["active"]:
        parts.append("en baisse")
    if price_war(s):
        parts.append("guerre des prix")
    if m["margin"] is None:
        parts.append("prix inconnu")
    else:
        approx = "~" if m["supplier_estimated"] else ""
        parts.append(f"marge {approx}{_fmt(m['margin'])} {costs['currency']}")
    return ", ".join(parts)


def score(s: dict, settings: dict, costs: dict) -> dict:
    w = settings["weights"]
    th = settings["thresholds"]
    max_sellers = th.get("max_sellers") or 8
    m = money(s, settings, costs)
    parts = {
        "days": _clamp(s["days"] / max(settings["days_full"], 1)),
        "sellers": sellers_curve(s["sellers"], max_sellers),
        "new7": _clamp(s["new7"] / max(settings["new7_full"], 1)),
        "dups": _clamp(math.log(max(s["dups"], 1)) / math.log(max(settings["dups_full"], 2))),
        "trend": _clamp((s["trend"] + 1) / 2),
        "margin": 0.0 if m["margin"] is None
        else _clamp(m["margin"] / max(costs["margin_target"], 1)),
    }
    total = sum(max(float(w.get(k, 0)), 0) for k in parts)
    value = 0.0
    if total > 0:
        value = 100 * sum(max(float(w.get(k, 0)), 0) * v for k, v in parts.items()) / total
    war = price_war(s)
    if not s["active"]:
        value *= 0.35
    if war:
        value -= 8
    if s["sellers_stopped"] > s["sellers"] and s["sellers"] + s["sellers_stopped"] >= 3:
        value -= 8
    value = math.floor(_clamp(value, 0, 100) + 0.5)

    passes = value >= (th.get("min_score") or 0)
    if th.get("min_margin") is not None:
        passes = passes and m["margin"] is not None and m["margin"] >= th["min_margin"]
    if th.get("max_sellers"):
        passes = passes and s["sellers"] <= th["max_sellers"]
    passes = passes and s["days"] >= (th.get("min_days") or 0)
    return {"score": value, "parts": parts, "money": m, "tier": m["tier"],
            "trend_label": trend_label(s), "price_war": war,
            "reason": reason(s, m, costs), "passes": bool(passes)}


def costs_of(country_cfg: dict) -> dict:
    return {k: country_cfg[k] for k in
            ("delivery_cost", "return_rate", "ad_cost", "margin_target", "currency")}
