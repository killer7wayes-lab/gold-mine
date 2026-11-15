import sqlite3

import pytest

from goldmine import db


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    yield c
    c.close()


def add_ad(conn, ad_id, page, body, title="", country="TN", phash=None, url="",
           active=1, start="2026-10-01", collation=1, price=None, product_id=None,
           first_seen="2026-10-01T00:00:00Z"):
    conn.execute(
        "INSERT INTO ads(ad_id, country, page_id, page_name, body, title, link_description, "
        "landing_url, phash, is_active, start_date, collation_count, price, is_ecom, "
        "product_id, first_seen, last_seen, last_run_id, platforms, media, image_url) "
        "VALUES(?,?,?,?,?,?,'',?,?,?,?,?,?,1,?,?,?,1,'[]','[]','')",
        (ad_id, country, page, "Page " + page, body, title, url, phash, active, start,
         collation, price, product_id, first_seen, first_seen))
    conn.execute("INSERT OR IGNORE INTO pages(page_id, country, name, is_ecom, watch, first_seen) "
                 "VALUES(?,?,?,1,1,?)", (page, country, "Page " + page, first_seen))
    conn.commit()
