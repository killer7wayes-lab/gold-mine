from datetime import date

from conftest import add_ad
from goldmine import config, googleads
from goldmine.textutil import own_site

# shape of Google's answer (shortened), as seen on 2026-11-15
ANSWER = {
    "1": [
        {"1": "AR1", "2": "CR1", "4": 3, "6": {"1": "1721779200"}, "7": {"1": "1794700800"},
         "12": "KOS distributions", "14": "beautystore.tn"},
        {"1": "AR1", "2": "CR2", "4": 2, "6": {"1": "1760000000"}, "7": {"1": "1790000000"},
         "12": "KOS distributions", "14": "beautystore.tn"},
    ],
    "2": "next-page-token", "4": "100", "5": "200",
}


def test_parse_answer():
    res = googleads.parse(ANSWER)
    assert res == {"ads": 100, "more": True, "advertiser": "KOS distributions",
                   "first_shown": "2024-07-24", "last_shown": "2026-11-15"}
    assert googleads.parse({}) == {"ads": 0, "more": False, "advertiser": "",
                                   "first_shown": None, "last_shown": None}


def test_only_the_sellers_own_website_is_looked_up():
    assert own_site("https://www.beautystore.tn/produit/creme?utm_source=fb") == "beautystore.tn"
    assert own_site("https://cachecoli.converty.shop/p/1") == ""       # shared platform
    assert own_site("https://abc.myshopify.com/products/x") == ""
    assert own_site("https://www.instagram.com/shop") == ""
    assert own_site("https://play.google.com/store/apps/details?id=x") == ""
    assert own_site("") == ""


def _cfg():
    cfg = config.load()
    for code, c in cfg["countries"].items():
        c["active"] = code == "TN"
    return cfg


def _products(conn):
    conn.execute("INSERT INTO products(id, country, name, category) VALUES(1,'TN','Crème visage','Beauté')")
    conn.execute("INSERT INTO products(id, country, name, category) VALUES(2,'TN','Mixeur','Cuisine')")
    add_ad(conn, "a1", "p1", "crème", url="https://beautystore.tn/creme", product_id=1)
    add_ad(conn, "a2", "p2", "crème", url="https://x.converty.shop/creme", product_id=1)
    add_ad(conn, "a3", "p3", "mixeur", url="https://shop-b.tn/mixeur", product_id=2)


def test_update_checks_each_site_once_and_export_marks_the_product(conn, monkeypatch):
    _products(conn)
    asked = []

    def fake(domain, country, session=None):
        asked.append((domain, country))
        return googleads.parse(ANSWER if domain == "beautystore.tn" else {})

    monkeypatch.setattr(googleads, "lookup", fake)
    monkeypatch.setattr(googleads, "open_session", lambda: object())
    monkeypatch.setattr(googleads.time, "sleep", lambda s: None)
    assert googleads.update(conn, _cfg(), 10) == 2
    assert sorted(asked) == [("beautystore.tn", "TN"), ("shop-b.tn", "TN")]
    assert googleads.update(conn, _cfg(), 10) == 0          # checked this week: not asked again
    assert len(asked) == 2

    found = googleads.by_product(conn, "TN", today=date(2026, 11, 20))
    assert found[1]["site"] == "beautystore.tn" and found[1]["ads"] == 100 and found[1]["more"]
    assert found[1]["active"] and found[1]["url"].endswith("?region=TN&domain=beautystore.tn")
    assert found[2] == {"site": "shop-b.tn", "ads": 0}
    assert not googleads.by_product(conn, "TN", today=date(2027, 3, 1))[1]["active"]


def test_update_respects_the_limit_and_backs_off(conn, monkeypatch):
    _products(conn)
    monkeypatch.setattr(googleads.time, "sleep", lambda s: None)
    monkeypatch.setattr(googleads, "open_session", lambda: object())
    monkeypatch.setattr(googleads, "lookup", lambda d, c, s=None: googleads.parse({}))
    assert googleads.update(conn, _cfg(), 0) == 0            # switched off
    assert googleads.update(conn, _cfg(), 1) == 1

    conn.execute("DELETE FROM google_ads")
    calls = []

    def down(domain, country, session=None):
        calls.append(domain)
        raise RuntimeError("timeout")

    monkeypatch.setattr(googleads, "lookup", down)
    assert googleads.update(conn, _cfg(), 10) == 0
    assert len(calls) == 2                                    # two failures in a row: stop
    assert conn.execute("SELECT COUNT(*) FROM google_ads").fetchone()[0] == 0
    assert googleads.blocked_until(conn) == ""                # a failure is not a refusal

    def refused(domain, country, session=None):
        calls.append(domain)
        raise googleads.Blocked("http 429")

    monkeypatch.setattr(googleads, "lookup", refused)
    assert googleads.update(conn, _cfg(), 10) == 0
    assert len(calls) == 3                                    # "too many requests": stop at once
    assert googleads.blocked_until(conn)                      # ...and leave Google alone
    assert googleads.update(conn, _cfg(), 10) == 0
    assert len(calls) == 3                                    # the next scan does not even ask
