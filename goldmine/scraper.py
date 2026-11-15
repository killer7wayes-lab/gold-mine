"""Playwright driver for the public Meta Ad Library website.

- never logs in
- one browser, one tab, human-like pauses
- reads the JSON the page itself loads (see adparse.py), DOM text as fallback
- raises Blocked when Facebook shows a login wall / rate limit, so the
  pipeline can back off
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import quote

from . import adparse

log = logging.getLogger("goldmine.scraper")

BASE = "https://www.facebook.com/ads/library/"
BLOCK_PHRASES = (
    "temporarily blocked", "you’re temporarily blocked", "you're temporarily blocked",
    "misusing this feature", "you must log in", "log in to continue",
    "something went wrong", "rate limit",
)
NO_RESULT_PHRASES = ("no ads match", "0 results", "didn't find any results",
                     "we couldn't find any results")
COOKIE_BUTTONS = ("Decline optional cookies", "Refuser les cookies optionnels",
                  "Only allow essential cookies", "Autoriser uniquement les cookies essentiels")

# Fallback: read ad cards straight from the rendered page.
DOM_JS = """
() => {
  const re = /Library ID:\\s*\\d+/g;
  const one = el => ((el.textContent || '').match(re) || []).length === 1;
  const cards = [];
  for (const el of document.querySelectorAll('div')) {
    if (!one(el)) continue;
    const parent = el.parentElement;
    if (parent && one(parent)) continue;          // keep the outermost card only
    cards.push({
      text: el.innerText || '',
      links: Array.from(el.querySelectorAll('a[href]')).map(a => a.href).slice(0, 12),
      images: Array.from(el.querySelectorAll('img[src]')).map(i => i.src).slice(0, 6),
    });
  }
  return cards;
}
"""


class Blocked(Exception):
    """Facebook refused to serve us (login wall, rate limit, error page)."""


class ScrapeError(Exception):
    """Something else went wrong for this one search (timeout, empty page)."""


def build_url(country: str, kind: str, value: str) -> str:
    common = f"?active_status=active&ad_type=all&country={country}&is_targeted_country=false&media_type=all"
    if kind == "page":
        return f"{BASE}{common}&search_type=page&view_all_page_id={quote(str(value))}"
    return f"{BASE}{common}&q={quote(value)}&search_type=keyword_unordered"


class AdLibraryScraper:
    def __init__(self, general: dict):
        self.general = general
        self._pw = self._browser = self._context = self.page = None
        self._responses = []
        self.browser_name = ""

    # -------------------------------------------------------------- lifecycle
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        headless = bool(self.general.get("headless", True))
        errors = []
        # Installed, signed browsers first (Windows may block the bundled one).
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = {"headless": headless}
                if channel:
                    kwargs["channel"] = channel
                self._browser = self._pw.chromium.launch(**kwargs)
                self.browser_name = channel or "chromium"
                break
            except Exception as exc:          # noqa: BLE001 - try the next browser
                errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
        if self._browser is None:
            self._pw.stop()
            raise RuntimeError("Aucun navigateur utilisable (Chrome, Edge ou Chromium). "
                               + " | ".join(errors))
        self._context = self._browser.new_context(
            locale="en-US", viewport={"width": 1280, "height": 900})
        self._context.set_default_timeout(45000)
        self.page = self._context.new_page()
        self.page.on("response", self._on_response)
        log.info("Navigateur: %s (headless=%s)", self.browser_name, headless)
        return self

    def __exit__(self, *exc):
        for closer in (getattr(self._context, "close", None),
                       getattr(self._browser, "close", None),
                       getattr(self._pw, "stop", None)):
            try:
                if closer:
                    closer()
            except Exception:                 # noqa: BLE001
                pass

    # -------------------------------------------------------------- helpers
    def _on_response(self, response):
        if "/api/graphql" in response.url:
            self._responses.append(response)

    def _pause(self, key: str):
        lo, hi = self.general.get(key, [2.0, 6.0])
        time.sleep(random.uniform(float(lo), float(hi)))

    def _drain(self, country: str):
        """Parse the GraphQL responses received since the last call."""
        ads, page_info = [], None
        pending, self._responses = self._responses, []
        for resp in pending:
            try:
                if resp.status == 429:
                    raise Blocked("HTTP 429 (trop de requêtes)")
                text = resp.text()
            except Blocked:
                raise
            except Exception:                 # noqa: BLE001 - body gone, skip
                continue
            if "ad_archive_id" not in text and "errors" not in text[:300]:
                continue
            found, info, error = adparse.ads_from_documents(adparse.parse_payload(text), country)
            if error and not found:
                low = error.lower()
                if "rate" in low or "limit" in low or "block" in low or "login" in low:
                    raise Blocked(f"GraphQL: {error}")
                log.warning("Erreur GraphQL ignorée: %s", error)
            ads.extend(found)
            if info is not None:
                page_info = info
        return ads, page_info

    def _body_text(self) -> str:
        try:
            return self.page.inner_text("body", timeout=10000)
        except Exception:                     # noqa: BLE001
            return ""

    def _check_block(self, body: str | None = None):
        url = self.page.url
        if "/login" in url or "checkpoint" in url:
            raise Blocked("redirigé vers la page de connexion")
        low = (body if body is not None else self._body_text()).lower()
        for phrase in BLOCK_PHRASES:
            if phrase in low and "library id" not in low:
                raise Blocked(f"message Facebook: « {phrase} »")

    def _dismiss_cookies(self):
        for label in COOKIE_BUTTONS:
            try:
                btn = self.page.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    time.sleep(1)
                    return
            except Exception:                 # noqa: BLE001
                continue

    def _scroll(self):
        try:
            self.page.mouse.wheel(0, random.randint(3500, 6000))
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:                     # noqa: BLE001
            pass

    # -------------------------------------------------------------- main
    def search(self, country: str, kind: str, value: str, max_ads: int) -> dict:
        """Run one search. Returns {"ads": [...], "complete": bool}.
        complete=True means we reached the end of the results."""
        url = build_url(country, kind, value)
        self._responses = []
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:              # noqa: BLE001
            raise ScrapeError(f"page non chargée: {str(exc).splitlines()[0]}") from exc
        time.sleep(random.uniform(3.0, 5.0))
        self._dismiss_cookies()
        self._check_block()

        ads: dict[str, dict] = {}
        page_info = None

        def add(found, info):
            nonlocal page_info
            before = len(ads)
            for ad in found:
                ads.setdefault(ad["ad_id"], ad)
            if info is not None:
                page_info = info
            return len(ads) - before

        try:
            found, info, _ = adparse.ads_from_documents(
                adparse.parse_html(self.page.content()), country)
            add(found, info)
        except Exception as exc:              # noqa: BLE001
            log.debug("HTML initial illisible: %s", exc)
        add(*self._drain(country))

        idle, complete = 0, False
        while len(ads) < max_ads:
            if page_info is not None and not page_info.get("has_next_page"):
                complete = True
                break
            self._scroll()
            self._pause("pause_scroll")
            gained = add(*self._drain(country))
            idle = 0 if gained else idle + 1
            if idle >= 3:
                # nothing new after 3 scrolls: end of list or silent throttling
                complete = page_info is None and bool(ads)
                break

        body = self._body_text()
        if not ads:
            self._check_block(body)
            try:
                dom_ads = adparse.parse_dom_cards(self.page.evaluate(DOM_JS), country)
            except Exception:                 # noqa: BLE001
                dom_ads = []
            if dom_ads:
                log.warning("JSON introuvable, lecture du texte de la page (%d pubs)", len(dom_ads))
                add(dom_ads, None)
                complete = False
            elif any(p in body.lower() for p in NO_RESULT_PHRASES):
                complete = True
            else:
                raise ScrapeError("aucune pub lue et aucun message « pas de résultat »")
        return {"ads": list(ads.values())[:max_ads], "complete": complete and len(ads) <= max_ads}
