"""Turn whatever JSON the Ad Library page loads into clean ad dicts.

Pure functions, no network: easy to unit-test and to fix when Facebook
changes something. We never rely on the exact path of the data; we walk the
whole JSON and pick every object that looks like an ad
(has "ad_archive_id" and "snapshot").
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .textutil import domain_of, unwrap_url

_JSON_SCRIPT = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)


def parse_payload(text: str) -> list:
    """A GraphQL response body -> list of JSON documents.
    Bodies can start with 'for (;;);' and can hold several JSON lines."""
    if not text:
        return []
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[9:]
    docs = []
    try:
        docs.append(json.loads(text))
        return docs
    except ValueError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            docs.append(json.loads(line))
        except ValueError:
            continue
    return docs


def parse_html(html: str) -> list:
    """JSON blobs embedded in the first page load (first batch of ads)."""
    docs = []
    for blob in _JSON_SCRIPT.findall(html or ""):
        if "ad_archive_id" not in blob:
            continue
        try:
            docs.append(json.loads(blob))
        except ValueError:
            continue
    return docs


def iter_ad_nodes(obj):
    """Yield every dict that looks like an ad, wherever it is nested."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "ad_archive_id" in cur and isinstance(cur.get("snapshot"), dict):
                yield cur
                continue
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def find_page_info(obj) -> dict | None:
    """Return the pagination info ({has_next_page, end_cursor}) if present."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            info = cur.get("page_info")
            if isinstance(info, dict) and "has_next_page" in info:
                return info
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def find_error(obj) -> str:
    """GraphQL-level error message (rate limit, etc.), or ''."""
    if isinstance(obj, dict):
        errs = obj.get("errors")
        if isinstance(errs, list) and errs:
            first = errs[0] if isinstance(errs[0], dict) else {}
            return str(first.get("message") or first.get("summary") or "error")
        if obj.get("error") and obj.get("errorSummary"):
            return str(obj.get("errorSummary"))
    return ""


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        markup = value.get("markup")
        if isinstance(markup, dict) and isinstance(markup.get("__html"), str):
            return re.sub(r"<[^>]+>", " ", markup["__html"])
    return ""


def _is_template(text: str) -> bool:
    return not text or "{{" in text


def _date(value) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def parse_ad(node: dict, country: str) -> dict | None:
    """One raw Ad Library node -> our flat ad dict. None if unusable."""
    ad_id = str(node.get("ad_archive_id") or "").strip()
    snap = node.get("snapshot") or {}
    if not ad_id:
        return None
    cards = [c for c in (snap.get("cards") or []) if isinstance(c, dict)]
    first = cards[0] if cards else {}

    body = _text(snap.get("body"))
    if _is_template(body):                      # catalogue / dynamic ads
        body = _text(first.get("body")) or body
    title = _text(snap.get("title"))
    if _is_template(title):
        title = _text(first.get("title")) or ""
    link_desc = _text(snap.get("link_description"))
    if _is_template(link_desc):
        link_desc = _text(first.get("link_description")) or ""
    link = snap.get("link_url") or ""
    if first.get("link_url") and (not link or _is_template(_text(snap.get("title")))):
        link = first["link_url"]
    link = unwrap_url(link or "")

    media = []
    for img in snap.get("images") or []:
        if isinstance(img, dict):
            url = img.get("resized_image_url") or img.get("original_image_url")
            if url:
                media.append({"type": "image", "url": url})
    for vid in snap.get("videos") or []:
        if isinstance(vid, dict):
            if vid.get("video_preview_image_url"):
                media.append({"type": "video_preview", "url": vid["video_preview_image_url"]})
            video = vid.get("video_sd_url") or vid.get("video_hd_url")
            if video:
                media.append({"type": "video", "url": video})
    for card in cards:
        url = (card.get("resized_image_url") or card.get("original_image_url")
               or card.get("video_preview_image_url"))
        if url:
            media.append({"type": "image", "url": url})
    image_url = next((m["url"] for m in media if m["type"] != "video"), "")

    platforms = node.get("publisher_platform") or []
    if not isinstance(platforms, list):
        platforms = [str(platforms)]
    collation = node.get("collation_count")
    try:
        collation = int(collation) if collation is not None else 1
    except (TypeError, ValueError):
        collation = 1

    return {
        "ad_id": ad_id,
        "country": country,
        "page_id": str(node.get("page_id") or snap.get("page_id") or ""),
        "page_name": node.get("page_name") or snap.get("page_name") or "",
        "page_picture": snap.get("page_profile_picture_url") or "",
        "page_categories": [c for c in (snap.get("page_categories") or []) if isinstance(c, str)],
        "page_likes": snap.get("page_like_count") if isinstance(snap.get("page_like_count"), int) else None,
        "start_date": _date(node.get("start_date")),
        "end_date": _date(node.get("end_date")),
        "is_active": 1 if node.get("is_active") else 0,
        "collation_count": max(collation, 1),
        "collation_id": str(node.get("collation_id") or ""),
        "body": body.strip(),
        "title": title.strip(),
        "link_description": link_desc.strip(),
        "caption": _text(snap.get("caption")) or _text(first.get("caption")),
        "cta": (snap.get("cta_type") or first.get("cta_type") or "").upper(),
        "landing_url": link,
        "landing_domain": domain_of(link),
        "platforms": [str(p).lower() for p in platforms],
        "media": media[:12],
        "image_url": image_url,
        "display_format": snap.get("display_format") or "",
    }


def ads_from_documents(docs: list, country: str) -> tuple[list[dict], dict | None, str]:
    """-> (ads, page_info, error message)"""
    ads, page_info, error = [], None, ""
    for doc in docs:
        error = error or find_error(doc)
        info = find_page_info(doc)
        if info is not None:
            page_info = info
        for node in iter_ad_nodes(doc):
            ad = parse_ad(node, country)
            if ad:
                ads.append(ad)
    return ads, page_info, error


# ---------------------------------------------------------------- DOM fallback

_LIB_ID = re.compile(r"Library ID:\s*(\d{6,})")
_STARTED = re.compile(r"Started running on ([A-Z][a-z]{2} \d{1,2}, \d{4})")
_USES = re.compile(r"(\d+) ads use this creative")


def parse_dom_cards(cards: list[dict], country: str) -> list[dict]:
    """Fallback when no JSON could be read. `cards` comes from scraper.DOM_JS:
    [{text, links:[...], images:[...]}]. Gives less detail but keeps us alive."""
    ads = []
    for card in cards:
        text = card.get("text") or ""
        m = _LIB_ID.search(text)
        if not m:
            continue
        start = None
        sm = _STARTED.search(text)
        if sm:
            try:
                start = datetime.strptime(sm.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                start = None
        um = _USES.search(text)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        page_name, body = "", ""
        if "Sponsored" in lines:
            idx = lines.index("Sponsored")
            page_name = lines[idx - 1] if idx > 0 else ""
            body = "\n".join(lines[idx + 1: idx + 12])
        page_id = ""
        landing = ""
        for href in card.get("links") or []:
            pm = re.search(r"view_all_page_id=(\d+)", href) or re.search(r"facebook\.com/(\d{6,})", href)
            if pm and not page_id:
                page_id = pm.group(1)
            real = unwrap_url(href)
            if real != href and not landing:
                landing = real
        images = [u for u in (card.get("images") or []) if "fbcdn" in u]
        image = images[-1] if images else ""
        ads.append({
            "ad_id": m.group(1), "country": country, "page_id": page_id,
            "page_name": page_name, "page_picture": "", "page_categories": [],
            "page_likes": None, "start_date": start, "end_date": None,
            "is_active": 1 if "Active" in lines[:3] else 0,
            "collation_count": int(um.group(1)) if um else 1, "collation_id": "",
            "body": body, "title": "", "link_description": "", "caption": "",
            "cta": "", "landing_url": landing, "landing_domain": domain_of(landing),
            "platforms": [], "media": [{"type": "image", "url": image}] if image else [],
            "image_url": image, "display_format": "",
        })
    return ads
