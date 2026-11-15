import json

import pytest
from conftest import add_ad

from goldmine import config, db, export, grouping, scheduler
from goldmine.defaults import DEFAULT_CONFIG


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "goldmine.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "data" / "thumbs")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "data" / "logs")
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config" / "config.json")
    calls = []
    monkeypatch.setattr(scheduler, "install", lambda cfg: (calls.append(cfg["general"]["run_times"]) or True, "ok"))
    monkeypatch.setattr(scheduler, "is_installed", lambda: True)
    conn = db.connect()
    add_ad(conn, "a1", "p1", "Mixeur portable rechargeable USB 59 DT", "Mixeur portable USB", price=59)
    add_ad(conn, "a2", "p2", "Mixeur portable rechargeable USB, jus frais", "Mini mixeur portable", price=65)
    add_ad(conn, "b1", "p3", "Ceinture lombaire chauffante pour le dos 79 DT", "Ceinture lombaire", price=79)
    grouping.group_country(conn, DEFAULT_CONFIG, "TN")
    export.export(conn, DEFAULT_CONFIG)
    conn.close()
    from goldmine.panel import app as panel_app
    client = panel_app.app.test_client()

    def post(path, **data):
        return client.post(path, data={"_t": panel_app.TOKEN, **data}, base_url="http://localhost:5000")

    def get(path):
        return client.get(path, base_url="http://localhost:5000")
    return type("P", (), {"post": staticmethod(post), "get": staticmethod(get),
                          "tmp": tmp_path, "sched": calls})


def exported(panel):
    return json.loads((panel.tmp / "docs" / "data" / "TN.json").read_text(encoding="utf-8"))["products"]


def test_pages_render(panel):
    for path in ("/", "/pays", "/mots-cles", "/pages", "/produits", "/reglages", "/dashboard/data/index.json"):
        assert panel.get(path).status_code == 200, path


def test_supplier_price_reaches_dashboard(panel):
    pid = next(p["id"] for p in exported(panel) if "ixeur" in p["name"])
    assert panel.post(f"/produit/{pid}", name="", supplier_price="14,5", supplier_note="AliExpress").status_code == 302
    prod = next(p for p in exported(panel) if p["id"] == pid)
    assert prod["supplier"] == 14.5 and prod["supplier_note"] == "AliExpress"
    panel.post(f"/produit/{pid}", name="Blender USB", supplier_price="")
    prod = next(p for p in exported(panel) if p["id"] == pid)
    assert prod["supplier"] is None and prod["name"] == "Blender USB"


def test_merge_then_split(panel):
    ids = [p["id"] for p in exported(panel)]
    assert len(ids) == 2
    panel.post("/produits/fusionner", ids=ids, c="TN")
    merged = exported(panel)
    assert len(merged) == 1 and merged[0]["total_ads"] == 3
    panel.post(f"/produit/{merged[0]['id']}/separer", ads=["b1"])
    assert sorted(p["total_ads"] for p in exported(panel)) == [1, 2]


def test_config_is_written_by_the_panel(panel):
    panel.post("/mots-cles?c=MA", add="توصيل سريع\nlivraison rapide")
    panel.post("/mots-cles?c=MA", remove="DH")
    panel.post("/pays/MA", active="1", name="Maroc", currency="DH", currency_tokens="DH, MAD, درهم",
               decimals="2", delivery_cost="30", return_rate="28", ad_cost="45", margin_target="100",
               price_min="29", price_max="20000", stores="Jumia | https://www.jumia.ma/catalog/?q={q}")
    panel.post("/reglages", run_times="6:30, 13h00, 99:99", max_ads_per_run="900", max_ads_per_search="50",
               max_page_scans_per_run="10", max_landing_fetches_per_run="40", max_sourcing_per_run="5",
               export_top_n="200", headless="1", tg_min_score="80", w_days="30", w_sellers="20", w_new7="10",
               w_dups="10", w_trend="10", w_margin="20", t_min_score="40", t_min_margin="",
               t_max_sellers="6", t_min_days="3", est_supplier_ratio="30")
    cfg = json.loads((panel.tmp / "config" / "config.json").read_text(encoding="utf-8"))
    ma = cfg["countries"]["MA"]
    assert ma["active"] and ma["delivery_cost"] == 30 and ma["return_rate"] == pytest.approx(0.28)
    assert "توصيل سريع" in ma["keywords"] and "DH" not in ma["keywords"]
    assert ma["stores"][0]["name"] == "Jumia" and ma["stores"][0]["item"]
    g = cfg["general"]
    assert g["run_times"] == ["06:30", "13:00"] and g["max_ads_per_run"] == 900 and g["publish"] is False
    assert panel.sched[-1] == ["06:30", "13:00"]
    assert cfg["scoring"]["weights"]["days"] == 30 and cfg["scoring"]["thresholds"]["max_sellers"] == 6
    assert cfg["scoring"]["thresholds"]["min_margin"] is None
    assert cfg["scoring"]["est_supplier_ratio"] == pytest.approx(0.30)
    index = json.loads((panel.tmp / "docs" / "data" / "index.json").read_text(encoding="utf-8"))
    assert {c["code"] for c in index["countries"]} == {"TN", "MA"}


def test_watch_page_and_guards(panel):
    panel.post("/pages?c=TN", add="https://www.facebook.com/ads/library/?view_all_page_id=123456789", name="Ma page")
    conn = db.connect()
    assert conn.execute("SELECT watch FROM pages WHERE page_id='123456789'").fetchone()[0] == 2
    panel.post("/pages?c=TN", ignore="123456789")
    assert conn.execute("SELECT ignored, watch FROM pages WHERE page_id='123456789'").fetchone()[:] == (1, 0)
    conn.close()
    from goldmine.panel import app as panel_app
    c = panel_app.app.test_client()
    assert c.post("/mots-cles", data={"add": "x"}, base_url="http://localhost:5000").status_code == 403
    assert c.get("/", base_url="http://attacker.example").status_code == 403
