"""Thumbnails + perceptual hashes.

Facebook image links expire after a few days, so we keep a small local JPEG
of each e-commerce ad and its 64-bit pHash (used to recognise the same
creative / same product photo on other pages).
"""
from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

log = logging.getLogger("goldmine.media")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
THUMB_SIZE = 360

# SQL fragment: ads worth processing (e-commerce ad, or ad of an e-commerce page)
CANDIDATE = ("(a.is_ecom=1 OR EXISTS(SELECT 1 FROM pages p WHERE p.page_id=a.page_id "
             "AND p.country=a.country AND p.is_ecom=1)) "
             "AND NOT EXISTS(SELECT 1 FROM pages p WHERE p.page_id=a.page_id "
             "AND p.country=a.country AND p.ignored=1)")


def hash_is_informative(hex_hash: str | None) -> bool:
    """Flat images (black first frame of a video...) all hash alike: ignore them."""
    if not hex_hash:
        return False
    bits = int(hex_hash, 16).bit_count()
    return 8 <= bits <= 56


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _fetch_one(job):
    ad_id, country, url = job
    try:
        from PIL import Image
        import imagehash
        resp = requests.get(url, headers=UA, timeout=20)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        phash = str(imagehash.phash(img))
        img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        folder = config.THUMBS_DIR / country
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{ad_id}.jpg"
        img.save(path, "JPEG", quality=78, optimize=True)
        return ad_id, country, f"{country}/{ad_id}.jpg", phash
    except Exception as exc:                  # noqa: BLE001 - expired link, bad image...
        log.debug("image %s: %s", ad_id, exc)
        return ad_id, country, None, None


def fetch_missing(conn, limit: int = 800) -> int:
    """Download thumbnails for candidate ads that have none yet."""
    jobs = [(r["ad_id"], r["country"], r["image_url"]) for r in conn.execute(
        f"SELECT a.ad_id, a.country, a.image_url FROM ads a WHERE a.thumb IS NULL "
        f"AND a.image_url<>'' AND a.media_tries<3 AND {CANDIDATE} "
        f"ORDER BY a.last_seen DESC LIMIT ?", (limit,))]
    done = 0
    if not jobs:
        return 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for ad_id, country, thumb, phash in pool.map(_fetch_one, jobs):
            if thumb:
                conn.execute("UPDATE ads SET thumb=?, phash=? WHERE ad_id=? AND country=?",
                             (thumb, phash, ad_id, country))
                done += 1
            else:
                conn.execute("UPDATE ads SET media_tries=media_tries+1 WHERE ad_id=? AND country=?",
                             (ad_id, country))
    conn.commit()
    return done
