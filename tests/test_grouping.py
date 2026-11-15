from conftest import add_ad

from goldmine import grouping
from goldmine.defaults import DEFAULT_CONFIG as CFG
from goldmine.textutil import categorize, product_name


def products(conn):
    out = {}
    for r in conn.execute("SELECT ad_id, product_id FROM ads"):
        out.setdefault(r["product_id"], set()).add(r["ad_id"])
    return sorted(out.values(), key=lambda s: sorted(s))


def test_same_product_different_sellers(conn):
    # same product, three sellers: similar copy / same photo / same wording in Arabic
    add_ad(conn, "a1", "p1", "Mini mixeur portable rechargeable USB 380ml\nLivraison gratuite 59 DT",
           "Mixeur portable USB", phash="c3a1f0e87b3c1d2e")
    add_ad(conn, "a2", "p2", "Mixeur portable rechargeable USB 380 ml, jus frais partout !\nPaiement à la livraison",
           "Mini mixeur portable")
    add_ad(conn, "a3", "p3", "خلاط محمول يشحن USB\nالتوصيل مجاني", "خلاط محمول",
           phash="c3a1f0e87b3c1d2f")                      # 1 bit away from a1
    # another product with the same promo boilerplate
    add_ad(conn, "b1", "p1", "Brosse lissante céramique cheveux\nLivraison gratuite 59 DT",
           "Brosse lissante", phash="0f0f33cc55aa9911")
    add_ad(conn, "b2", "p4", "Livraison gratuite, paiement à la livraison, commandez maintenant", "")
    res = grouping.group_country(conn, CFG, "TN")
    assert res["new_ads"] == 5
    assert products(conn) == [{"a1", "a2", "a3"}, {"b1"}, {"b2"}]
    name = conn.execute("SELECT name FROM products p JOIN ads a ON a.product_id=p.id "
                        "WHERE a.ad_id='a1'").fetchone()[0]
    assert "ixeur" in name


def test_incremental_and_landing(conn):
    add_ad(conn, "a1", "p1", "Lampe LED solaire jardin étanche", "Lampe solaire",
           url="https://shop.tn/products/lampe-solaire?utm_source=fb")
    grouping.group_country(conn, CFG, "TN")
    first = conn.execute("SELECT product_id FROM ads WHERE ad_id='a1'").fetchone()[0]
    # new run: same product URL (other tracking), plus an unrelated ad of the same shop
    add_ad(conn, "a2", "p1", "Éclairez votre extérieur sans électricité", "Promo",
           url="https://shop.tn/products/lampe-solaire?fbclid=zzz")
    add_ad(conn, "c1", "p1", "Tapis de prière épais et confortable", "Tapis de prière",
           url="https://shop.tn/products/tapis-priere")
    res = grouping.group_country(conn, CFG, "TN")
    assert res == {"new_ads": 2, "new_products": 1}
    rows = dict(conn.execute("SELECT ad_id, product_id FROM ads").fetchall())
    assert rows["a2"] == first and rows["c1"] != first


def test_merge_and_split(conn):
    add_ad(conn, "a1", "p1", "Montre connectée sport étanche", "Montre connectée")
    add_ad(conn, "a2", "p2", "Ceinture lombaire chauffante dos", "Ceinture lombaire")
    grouping.group_country(conn, CFG, "TN")
    ids = [r[0] for r in conn.execute("SELECT id FROM products ORDER BY id")]
    assert len(ids) == 2
    conn.execute("UPDATE products SET supplier_price=12 WHERE id=?", (ids[1],))
    assert grouping.merge_products(conn, ids[0], ids[1])
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
    assert conn.execute("SELECT supplier_price FROM products").fetchone()[0] == 12
    new_id = grouping.split_product(conn, ids[0], ["a2"])
    assert new_id and conn.execute("SELECT product_id, pinned FROM ads WHERE ad_id='a2'").fetchone()[:] == (new_id, 1)
    assert grouping.split_product(conn, ids[0], ["a1"]) is None      # cannot empty a product
    # split ads stay where they were put on the next run
    grouping.group_country(conn, CFG, "TN")
    assert conn.execute("SELECT product_id FROM ads WHERE ad_id='a2'").fetchone()[0] == new_id


def test_cross_country_link(conn):
    add_ad(conn, "t1", "p1", "Epilateur laser IPL indolore", "Epilateur IPL", phash="c3a1f0e87b3c1d2e")
    add_ad(conn, "m1", "p9", "جهاز إزالة الشعر بالليزر", "جهاز الليزر", country="MA", phash="c3a1f0e87b3c1d2c")
    add_ad(conn, "m2", "p9", "Organisateur de chaussures", "Range chaussures", country="MA")
    for c in ("TN", "MA"):
        grouping.group_country(conn, CFG, c)
    assert grouping.link_countries(conn, CFG) == 1
    g = dict(conn.execute("SELECT a.ad_id, p.global_id FROM ads a JOIN products p ON p.id=a.product_id").fetchall())
    assert g["t1"] == g["m1"] != g["m2"]


def test_names_and_categories():
    assert product_name("Livraison gratuite 🔥", "Offre spéciale\nAspirateur voiture sans fil 12V\nPrix 45 DT") \
        == "Aspirateur voiture sans fil 12V"
    assert product_name("Ma Boutique", "x", "Friteuse sans huile 5L", "Ma Boutique") == "Friteuse sans huile 5L"
    assert categorize("تلميع وتجديد البلاستيك للسيارة") == "Auto"
    assert categorize("Sérum anti rides visage") == "Beauté"
    assert categorize("Dossier de chaise") == "Autre"
