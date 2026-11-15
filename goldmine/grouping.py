"""Group ads that sell the same product into one "product".

Three kinds of evidence, each 0..1:
  text     TF-IDF cosine on the ad copy (promo boilerplate weighs little)
           and fuzzy match on the product name
  image    perceptual hash distance between thumbnails
  landing  same product URL / same shop

Different creatives or different shops are NOT evidence against (ten sellers
of one product all use their own shop and often their own video), so image
and landing only count when they agree.

Grouping is incremental: ads that already have a product never move by
themselves. Only new ads are clustered, and each cluster joins the existing
product it matches best, or becomes a new product. Manual merge / split from
the control panel always wins.
"""
from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from . import db
from .landing import title_for
from .media import CANDIDATE, hamming, hash_is_informative
from .textutil import (categorize, domain_of, normalize, normalize_url, product_name,
                       tokens, url_path_is_product)

log = logging.getLogger("goldmine.grouping")


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class _Doc:
    __slots__ = ("idx", "ad_id", "product_id", "name", "norm_name", "name_tokens",
                 "counts", "vec", "phash", "nurl", "domain", "is_product_url", "path")

    def __init__(self, idx, row, landing_title):
        self.idx = idx
        self.ad_id = row["ad_id"]
        self.product_id = row["product_id"]
        self.name = product_name(row["title"], row["body"], landing_title, row["page_name"])
        self.norm_name = " ".join(tokens(self.name, drop_boilerplate=True))
        self.name_tokens = len(self.norm_name.split())
        body = (row["body"] or "")[:600]
        counts = Counter(tokens(f"{row['title'] or ''} {body} {row['link_description'] or ''}",
                                drop_boilerplate=True))
        for tok in self.norm_name.split():          # the name counts double
            counts[tok] += 1
        self.counts = counts
        self.vec = {}
        self.phash = row["phash"] if hash_is_informative(row["phash"]) else None
        self.nurl = normalize_url(row["landing_url"] or "")
        self.domain = domain_of(self.nurl) if self.nurl else ""
        self.is_product_url = url_path_is_product(self.nurl) if self.nurl else False
        self.path = self.nurl.split("/", 1)[1] if "/" in self.nurl else ""


def _build_vectors(docs: list[_Doc]):
    """TF-IDF vectors + inverted index. Returns (index, common_tokens)."""
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(d.counts.keys())
    index = defaultdict(list)
    for d in docs:
        vec = {}
        for tok, c in d.counts.items():
            vec[tok] = (1 + math.log(c)) * (math.log((n + 1) / (df[tok] + 1)) + 1)
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        d.vec = {t: w / norm for t, w in vec.items()}
        for tok, w in d.vec.items():
            index[tok].append((d.idx, w))
    cap = max(60, int(0.25 * n))
    common = {t for t, c in df.items() if c > cap}
    return index, common


def pair_score(a: _Doc, b: _Doc, cos: float, g: dict) -> tuple[float, bool]:
    """-> (similarity 0..1, strong link?)"""
    strong = False
    text = None
    if len(a.counts) >= 2 and len(b.counts) >= 2:
        name_sim = 0.0
        if a.name_tokens >= 2 and b.name_tokens >= 2:
            name_sim = fuzz.token_sort_ratio(a.norm_name, b.norm_name) / 100.0
        # the same headline helps, but a shared slogan ("on est de retour !")
        # must not be enough on its own: it is averaged with the full copy
        text = max(cos, 0.5 * cos + 0.5 * name_sim)
        if cos >= 0.9 and min(len(a.counts), len(b.counts)) >= 4:
            strong = True
    image = None
    images_differ = same_picture = False
    if a.phash and b.phash:
        dist = hamming(a.phash, b.phash)
        if dist <= g["image_distance"]:
            strong = same_picture = True
        sim = 1 - dist / 32.0
        if sim >= 0.6:
            image = sim
        else:
            images_differ = True
    landing = None
    if a.nurl and b.nurl and a.domain == b.domain:
        if a.nurl == b.nurl:
            landing = 1.0 if a.is_product_url else 0.5
            strong = strong or a.is_product_url
        elif a.is_product_url and b.is_product_url and fuzz.ratio(a.path, b.path) >= 85:
            landing = 0.7
        else:
            landing = 0.25               # same shop, another product page
            if a.is_product_url and b.is_product_url and not same_picture:
                # one shop, two different product pages: two products, even
                # when the shop pastes the same text on every ad
                return 0.0, False
    parts = [(g["w_text"], text), (g["w_image"], image), (g["w_landing"], landing)]
    parts = [(w, v) for w, v in parts if v is not None]
    if not parts:
        return 0.0, strong
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    # Text is the only clue and the pictures are clearly different: two shops
    # using the same ad template for different products look exactly like this.
    # Ask for near-identical copy before linking.
    if images_differ and landing is None and cos < g.get("text_only_min", 0.75):
        score = 0.0
    return score, strong


def group_country(conn, cfg: dict, country: str) -> dict:
    """Attach every un-grouped candidate ad of this country to a product."""
    g = cfg["grouping"]
    rows = db.rows(conn, f"""
        SELECT a.ad_id, a.product_id, a.title, a.body, a.link_description,
               a.landing_url, a.phash, a.page_name
        FROM ads a WHERE a.country=? AND {CANDIDATE}
        ORDER BY (a.product_id IS NULL), a.first_seen, a.ad_id""", (country,))
    docs = [_Doc(i, r, title_for(conn, r["landing_url"])) for i, r in enumerate(rows)]
    new = [d for d in docs if d.product_id is None]
    if not new:
        return {"new_ads": 0, "new_products": 0}
    index, common = _build_vectors(docs)
    by_url, by_domain = defaultdict(list), defaultdict(list)
    hashed = [d for d in docs if d.phash]
    for d in docs:
        if d.nurl:
            by_url[d.nurl].append(d.idx)
            by_domain[d.domain].append(d.idx)

    uf = _UnionFind()
    votes = defaultdict(dict)                     # new ad idx -> {product_id: score}
    threshold = float(g["link_threshold"])
    for a in new:
        uf.find(a.idx)
        cos = defaultdict(float)
        for tok, w in a.vec.items():
            if tok in common:
                continue
            for j, wj in index[tok]:
                if j != a.idx:
                    cos[j] += w * wj
        cands = {j for j, v in cos.items() if v >= 0.25}
        if a.phash:
            cands.update(d.idx for d in hashed
                         if d.idx != a.idx and hamming(a.phash, d.phash) <= 12)
        if a.nurl:
            cands.update(j for j in by_url[a.nurl] if j != a.idx)
            if len(by_domain[a.domain]) <= 400:
                cands.update(j for j in by_domain[a.domain] if j != a.idx)
        for j in cands:
            b = docs[j]
            if b.product_id is None and b.idx < a.idx:
                continue                           # pair already seen from the other side
            score, strong = pair_score(a, b, cos.get(j, 0.0), g)
            if not strong and score < threshold:
                continue
            if b.product_id is None:
                uf.union(a.idx, b.idx)
            else:
                rank = score + (1.0 if strong else 0.0)
                if rank > votes[a.idx].get(b.product_id, 0):
                    votes[a.idx][b.product_id] = rank

    clusters = defaultdict(list)
    for a in new:
        clusters[uf.find(a.idx)].append(a)
    created = 0
    for members in clusters.values():
        best = {}
        for m in members:
            for pid, rank in votes[m.idx].items():
                best[pid] = max(best.get(pid, 0), rank)
        if best:
            pid = max(best.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        else:
            now = db.now_iso()
            pid = conn.execute(
                "INSERT INTO products(country, created_at, first_seen) VALUES(?,?,?)",
                (country, now, db.today())).lastrowid
            created += 1
        conn.executemany("UPDATE ads SET product_id=? WHERE ad_id=? AND country=?",
                         [(pid, m.ad_id, country) for m in members])
    conn.commit()
    refresh_products(conn, country)
    return {"new_ads": len(new), "new_products": created}


def refresh_products(conn, country: str | None = None) -> None:
    """Recompute name, category, image of products; drop empty ones."""
    where = "WHERE p.country=?" if country else ""
    params = (country,) if country else ()
    for p in db.rows(conn, f"SELECT p.id, p.manual_name FROM products p {where}", params):
        ads = db.rows(conn, """
            SELECT title, body, landing_url, thumb, phash, is_active, collation_count,
                   start_date, page_name
            FROM ads WHERE product_id=?
            ORDER BY is_active DESC, collation_count DESC, COALESCE(start_date, '9999')""",
                      (p["id"],))
        if not ads:
            delete_product(conn, p["id"])
            continue
        scores = Counter()
        original = {}
        for ad in ads[:40]:
            name = product_name(ad["title"], ad["body"], title_for(conn, ad["landing_url"]),
                                ad["page_name"])
            key = normalize(name)
            if not key:
                continue
            scores[key] += 10
            if ad["title"] and normalize(ad["title"]) == key:
                scores[key] += 5
            original.setdefault(key, name)
        name = ""
        if scores:
            key = max(scores, key=lambda k: (scores[k] - abs(len(k) - 35) / 10.0))
            name = original[key]
        text = " ".join([name] + [(a["body"] or "")[:300] for a in ads[:5]])
        pic = next((a for a in ads if a["thumb"]), None)
        hashed = pic if pic and pic["phash"] else next((a for a in ads if a["phash"]), None)
        conn.execute("UPDATE products SET name=?, category=?, image=?, phash=? WHERE id=?",
                     (p["manual_name"] or name, categorize(text),
                      pic["thumb"] if pic else None, hashed["phash"] if hashed else None, p["id"]))
    conn.commit()


def regroup(conn, cfg: dict, country: str) -> dict:
    """Forget the automatic grouping of a country and redo it with the current
    rules. Manual work is kept: pinned ads (merged / split by hand) stay, and
    so do products that have a manual name or a supplier price."""
    conn.execute("""
        UPDATE ads SET product_id=NULL WHERE country=? AND pinned=0 AND product_id IN (
            SELECT id FROM products WHERE manual_name IS NULL AND supplier_price IS NULL)""",
                 (country,))
    conn.commit()
    refresh_products(conn, country)              # drops the products left empty
    return group_country(conn, cfg, country)


def delete_product(conn, product_id: int) -> None:
    for table in ("product_snapshots", "sourcing", "alerts"):
        conn.execute(f"DELETE FROM {table} WHERE product_id=?", (product_id,))
    conn.execute("DELETE FROM products WHERE id=?", (product_id,))


# ------------------------------------------------------------------ cross-country

def link_countries(conn, cfg: dict) -> int:
    """Give the same global_id to the same product seen in several countries."""
    g = cfg["grouping"]
    prods = db.rows(conn, """
        SELECT p.id, p.country, p.name, p.phash FROM products p
        WHERE EXISTS(SELECT 1 FROM ads a WHERE a.product_id=p.id AND a.is_active=1)""")
    uf = _UnionFind()
    feats = []
    for p in prods:
        uf.find(p["id"])
        norm = " ".join(tokens(p["name"] or "", drop_boilerplate=True))
        feats.append((p["id"], p["country"], norm, len(norm.split()),
                      p["phash"] if hash_is_informative(p["phash"]) else None))
    if len({f[1] for f in feats}) > 1:
        dist_max = int(g["cross_country_image_distance"])
        for i, a in enumerate(feats):
            for b in feats[i + 1:]:
                if a[1] == b[1]:
                    continue
                same = bool(a[4] and b[4] and hamming(a[4], b[4]) <= dist_max)
                if not same and a[3] >= 2 and b[3] >= 2:
                    same = fuzz.token_sort_ratio(a[2], b[2]) >= 90
                if same:
                    uf.union(a[0], b[0])
    linked = 0
    conn.execute("UPDATE products SET global_id=id")
    for pid, *_ in feats:
        root = uf.find(pid)
        if root != pid:
            linked += 1
        conn.execute("UPDATE products SET global_id=? WHERE id=?", (root, pid))
    conn.commit()
    return linked


# ------------------------------------------------------------------ manual overrides

def merge_products(conn, keep_id: int, other_id: int) -> bool:
    """Move every ad of `other_id` into `keep_id`. Kept forever."""
    keep = conn.execute("SELECT * FROM products WHERE id=?", (keep_id,)).fetchone()
    other = conn.execute("SELECT * FROM products WHERE id=?", (other_id,)).fetchone()
    if not keep or not other or keep_id == other_id or keep["country"] != other["country"]:
        return False
    conn.execute("UPDATE ads SET product_id=? WHERE product_id=?", (keep_id, other_id))
    conn.execute("UPDATE ads SET pinned=1 WHERE product_id=?", (keep_id,))   # decided by hand
    if keep["supplier_price"] is None and other["supplier_price"] is not None:
        conn.execute("UPDATE products SET supplier_price=?, supplier_note=? WHERE id=?",
                     (other["supplier_price"], other["supplier_note"], keep_id))
    delete_product(conn, other_id)
    conn.commit()
    refresh_products(conn, keep["country"])
    return True


def split_product(conn, product_id: int, ad_ids: list[str]) -> int | None:
    """Move the chosen ads to a brand-new product. They are pinned there."""
    prod = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not prod or not ad_ids:
        return None
    total = conn.execute("SELECT COUNT(*) FROM ads WHERE product_id=?", (product_id,)).fetchone()[0]
    marks = ",".join("?" * len(ad_ids))
    moving = conn.execute(
        f"SELECT COUNT(*) FROM ads WHERE product_id=? AND ad_id IN ({marks})",
        [product_id] + list(ad_ids)).fetchone()[0]
    if moving == 0 or moving >= total:
        return None
    new_id = conn.execute(
        "INSERT INTO products(country, created_at, first_seen) VALUES(?,?,?)",
        (prod["country"], db.now_iso(), db.today())).lastrowid
    conn.execute(
        f"UPDATE ads SET product_id=?, pinned=1 WHERE product_id=? AND ad_id IN ({marks})",
        [new_id, product_id] + list(ad_ids))
    conn.commit()
    refresh_products(conn, prod["country"])
    return new_id
