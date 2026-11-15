"""Find the selling price in ad text, using the country's currency words.

Rules of thumb (COD ads):
- a number glued to a currency word is a price:  89dt, 89,900 DT, DT 89, 49 د.ت
- several prices -> the lowest plausible one is the unit price
  ("au lieu de 120 DT, seulement 79 DT", "1 pièce 49 DT, 2 pièces 89 DT")
- numbers that are a shipping fee or a discount amount are not prices
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .defaults import DELIVERY_WORDS
from .textutil import fix_digits

NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,3})?|\d+(?:[.,]\d{3})*(?:[.,]\d{1,3})?"
PRICE_WORDS = ["prix", "price", "seulement", "only", "السعر", "سعر", "الثمن", "ثمن",
               "بسوم", "السوم", "سوم", "السومه", "فقط", "ب "]
FREE_WORDS = ["gratuit", "free", "مجان", "بلاش", "offert"]
DISCOUNT_WORDS = ["remise", "reduction", "réduction", "economisez", "économisez",
                  "economie", "خصم", "تخفيض ب", "وفر", "save", "-"]
_ARABIC = "؀-ۿ"


def _token_regex(token: str) -> str:
    esc = re.escape(token)
    if re.search(f"[{_ARABIC}]", token):
        return f"(?<![{_ARABIC}]){esc}(?![{_ARABIC}])"
    if token.isalpha() or "." in token:
        return f"(?<![A-Za-zÀ-ÿ]){esc}(?![A-Za-zÀ-ÿ])"
    return esc


@lru_cache(maxsize=64)
def _patterns(tokens: tuple[str, ...]):
    alts = "|".join(_token_regex(t) for t in sorted(set(tokens), key=len, reverse=True))
    with_currency = re.compile(
        rf"(?P<a>{NUM})\s?(?:{alts})|(?:{alts})\s?[:=]?\s?(?P<b>{NUM})", re.I)
    words = "|".join(re.escape(w.strip()) for w in PRICE_WORDS if w.strip() != "ب")
    with_word = re.compile(rf"(?:{words})\s*(?:est|de|:|=|-)?\s*(?P<a>{NUM})(?![\d%])", re.I)
    return with_currency, with_word


def parse_number(raw: str, decimals: int = 2, price_min: float = 0, price_max: float = 1e12) -> float | None:
    """'89,900' -> 89.9 (millimes) ; '1.200' -> 1200 ; '1 299,50' -> 1299.5"""
    s = raw.strip()
    groups = re.split(r"[   ]", s)
    s = "".join(groups)
    seps = [c for c in s if c in ".,"]
    try:
        if not seps:
            value = float(s)
        elif "." in s and "," in s:
            last = max(s.rfind("."), s.rfind(","))
            value = float(re.sub(r"[.,]", "", s[:last]) + "." + s[last + 1:])
        elif len(seps) > 1:
            value = float(re.sub(r"[.,]", "", s))
        else:
            head, tail = re.split(r"[.,]", s)
            if len(tail) == 3:
                as_decimal = float(head + "." + tail)
                as_thousand = float(head + tail)
                if decimals == 3 and as_decimal >= price_min:
                    value = as_decimal
                else:
                    value = as_thousand
            else:
                value = float(head + "." + tail)
    except ValueError:
        return None
    if value > price_max and len(groups) > 1:      # "pack 2 199 dt" -> 199
        return parse_number(groups[-1], decimals, price_min, price_max)
    return value


def _is_fee_or_discount(text: str, start: int) -> bool:
    window = text[max(0, start - 32):start].lower()
    pos = max((window.rfind(w) for w in DELIVERY_WORDS), default=-1)
    if pos >= 0:
        tail = window[pos:]
        if not any(w in tail for w in PRICE_WORDS + FREE_WORDS + ["\n"]):
            return True
    near = window[-14:]
    if any(w in near for w in DISCOUNT_WORDS):
        return True
    # "livraison gratuite dès 100 DT" is a free-shipping threshold
    return bool(re.search(r"(dès|des|à partir de|a partir de|من|فوق)\s*$", near)
                and any(w in window for w in FREE_WORDS))


def extract_prices(text: str, country: dict) -> list[float]:
    """All plausible prices, in order of appearance."""
    if not text:
        return []
    # NFKC turns fancy letters (𝐃𝐓, ＤＴ) back into plain ones
    text = fix_digits(unicodedata.normalize("NFKC", text))
    with_currency, with_word = _patterns(tuple(country["currency_tokens"]))
    lo, hi, dec = country["price_min"], country["price_max"], country.get("decimals", 2)
    found = []
    for pattern in (with_currency, with_word):
        for m in pattern.finditer(text):
            group = "a" if m.group("a") else "b"
            raw = m.group(group)
            if not raw or _is_fee_or_discount(text, m.start(group)):
                continue
            value = parse_number(raw, dec, lo, hi)
            if value is not None and lo <= value <= hi:
                found.append(round(value, 3))
        if found:                    # currency matches beat bare "prix 89"
            break
    return found


def best_price(text: str, country: dict) -> float | None:
    prices = extract_prices(text, country)
    return min(prices) if prices else None


def fmt(value: float | None, currency: str) -> str:
    if value is None:
        return "?"
    shown = f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"
    return f"{shown} {currency}"
