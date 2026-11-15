import copy
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from goldmine import scoring
from goldmine.defaults import DEFAULT_CONFIG as CFG

ST = CFG["scoring"]
TN = scoring.costs_of(CFG["countries"]["TN"])


def sig(**over):
    s = {"active": True, "days": 52, "sellers": 4, "sellers_stopped": 1, "active_ads": 9,
         "total_ads": 12, "new7": 4, "dups": 3, "trend": 0.3, "price": 89.0,
         "price_min": 79.0, "price_drop": 0.0, "supplier": None}
    s.update(over)
    return s


def test_money_formula():
    m = scoring.money(sig(), ST, TN)
    # 4 sellers -> medium tier: ad 10 ; returns 0.25/0.75*(8+10)=6 ; supplier 35% of 89
    assert m["tier"] == "medium" and m["ad_cost"] == 10
    assert m["returns"] == pytest.approx(6.0)
    assert m["margin"] == pytest.approx(89 - 31.15 - 8 - 10 - 6)
    m = scoring.money(sig(supplier=20), ST, TN)
    assert m["margin"] == pytest.approx(45.0) and not m["supplier_estimated"]
    assert scoring.money(sig(price=None), ST, TN)["margin"] is None


def test_reason_and_score_shape():
    r = scoring.score(sig(supplier=20), ST, TN)
    assert r["reason"] == ("Actif depuis 52 jours, 4 vendeurs, 4 nouvelles pubs cette semaine, "
                           "créatif dupliqué 3×, en hausse, marge 45 DT")
    assert 80 <= r["score"] <= 100 and r["passes"]
    est = scoring.score(sig(), ST, TN)
    assert "marge ~34 DT" in est["reason"]


def test_ordering_makes_sense():
    winner = scoring.score(sig(), ST, TN)["score"]
    assert winner > scoring.score(sig(days=3), ST, TN)["score"]
    assert winner > scoring.score(sig(sellers=1), ST, TN)["score"]
    assert winner > scoring.score(sig(sellers=14), ST, TN)["score"]          # saturated
    assert winner > scoring.score(sig(trend=-0.6), ST, TN)["score"]
    assert winner > scoring.score(sig(price_drop=0.3), ST, TN)["score"]      # price war
    assert winner > scoring.score(sig(price=25, price_min=25), ST, TN)["score"]   # no margin
    dead = scoring.score(sig(active=False, sellers=0, new7=0, active_ads=0), ST, TN)
    assert dead["score"] < 30 and dead["trend_label"] == "dying"
    assert "Plus aucune pub active" in dead["reason"]


def test_tiers_and_thresholds():
    assert scoring.cost_tier(sig(sellers=1, days=5), ST) == "low"
    assert scoring.cost_tier(sig(sellers=12), ST) == "high"
    assert scoring.cost_tier(sig(sellers=4, days=90, new7=0), ST) == "high"   # tired creatives
    st = copy.deepcopy(ST)
    st["thresholds"].update(min_margin=50, max_sellers=3, min_days=60)
    assert not scoring.score(sig(), st, TN)["passes"]
    st["weights"] = {k: 0 for k in st["weights"]}
    assert scoring.score(sig(), st, TN)["score"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_python_and_browser_scores_are_identical():
    rnd = random.Random(7)
    cases = []
    for _ in range(400):
        price = rnd.choice([None, round(rnd.uniform(9, 400), 3)])
        st = copy.deepcopy(ST)
        st["weights"] = {k: rnd.choice([0, 5, 10, 25, 40]) for k in st["weights"]}
        st["thresholds"] = {"min_score": rnd.choice([0, 40, 70]),
                            "min_margin": rnd.choice([None, 0, 20]),
                            "max_sellers": rnd.choice([3, 5, 8, 12]),
                            "min_days": rnd.choice([0, 7])}
        s = sig(active=rnd.random() > 0.15, days=rnd.randint(0, 200), sellers=rnd.randint(0, 20),
                sellers_stopped=rnd.randint(0, 8), new7=rnd.randint(0, 9), dups=rnd.randint(1, 30),
                trend=round(rnd.uniform(-1, 1), 3), price=price,
                price_min=None if price is None else round(price * rnd.uniform(0.4, 1), 3),
                price_drop=rnd.choice([0, 0, 0.1, 0.2]),
                supplier=rnd.choice([None, None, round(rnd.uniform(2, 60), 2)]))
        costs = dict(TN, return_rate=rnd.choice([0, 0.1, 0.25, 0.4]),
                     ad_cost=rnd.choice([5, 10, 18]), delivery_cost=rnd.choice([0, 7, 8]))
        cases.append({"s": s, "settings": st, "costs": costs})
    score_js = Path(__file__).resolve().parent.parent / "docs" / "score.js"
    script = ("const G=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c);"
              "process.stdin.on('end',()=>{const out=JSON.parse(d).map(c=>{const r=G.score(c.s,c.settings,c.costs);"
              "return [r.score,r.reason,r.passes,r.tier,r.trend_label,r.money.margin]});"
              "process.stdout.write(JSON.stringify(out))})")
    res = subprocess.run(["node", "-e", script, str(score_js)], input=json.dumps(cases),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    for case, js in zip(cases, json.loads(res.stdout)):
        py = scoring.score(case["s"], case["settings"], case["costs"])
        assert [py["score"], py["reason"], py["passes"], py["tier"], py["trend_label"]] == js[:5], case
        assert py["money"]["margin"] == pytest.approx(js[5]) if js[5] is not None else py["money"]["margin"] is None
