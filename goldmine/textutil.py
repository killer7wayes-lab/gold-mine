"""Text normalisation shared by pricing, grouping and categorisation.
Handles French, Arabic and darija written in either script."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse

from .defaults import CATEGORIES, ECOM_HINTS

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_AR_MARKS = re.compile(r"[ً-ٰٟـ]")          # tashkeel + tatweel
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه",
                         "ى": "ي", "ؤ": "و", "ئ": "ي"})
_NON_WORD = re.compile(r"[^\w؀-ۿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")

STOPWORDS = set("""
le la les un une des de du et en a au aux pour avec sur dans par ce cette ces
est sont vous nous votre vos notre nos plus tout tous toute toutes ne pas que
qui the and for with your you our من في على الى عن مع هذا هذه كل او و يا ما
لا ان هو هي تم بـ ب ل""".split())

# Promo words that say nothing about WHICH product is sold.
BOILERPLATE = set("""
livraison gratuite paiement commandez commander commande maintenant prix promo
promotion offre stock limite limitee disponible domicile tunisie maroc algerie
dt tnd dh mad da dzd nouveau nouvelle top qualite original garantie contactez
appelez whatsapp message inbox express free shipping shop now order click cliquez ici اضغط هنا توصيل التوصيل مجاني مجانا الدفع عند الاستلام
اطلب الان الخلاص السعر سعر عرض خاص تخفيض الكميه محدوده لكامل الولايات لكل
متوفر جديد اصلي ضمان للطلب تونس المغرب الجزائر دينار درهم دج""".split())


def fix_digits(text: str) -> str:
    return (text or "").translate(_AR_DIGITS)


def normalize(text: str) -> str:
    """Lowercase, no accents, no emoji/punctuation, unified Arabic letters."""
    if not text:
        return ""
    text = fix_digits(unicodedata.normalize("NFKC", text)).lower()
    text = _AR_MARKS.sub("", text).translate(_AR_MAP)
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    text = _NON_WORD.sub(" ", text).replace("_", " ")
    return _SPACES.sub(" ", text).strip()


def tokens(text: str, drop_boilerplate: bool = False) -> list[str]:
    out = []
    for tok in normalize(text).split():
        if len(tok) < 2 or tok.isdigit() or tok in STOPWORDS:
            continue
        if drop_boilerplate and tok in BOILERPLATE:
            continue
        out.append(tok)
    return out


def ecom_hint_count(text: str) -> int:
    norm = normalize(text)
    return sum(1 for hint in ECOM_HINTS if normalize(hint) in norm)


def _kw_match(word: str, toks: set, norm: str) -> bool:
    if " " in word:
        return word in norm
    if word in toks:
        return True
    if len(word) >= 5:                       # stems: "nettoy" -> nettoyage, nettoyant
        return any(t.startswith(word) for t in toks)
    return False


_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")


def _with_bare_arabic(toks: set) -> set:
    """Add Arabic words without their article: للسياره / السياره -> سياره"""
    out = set(toks)
    for t in toks:
        for pre in _AR_PREFIXES:
            if t.startswith(pre) and len(t) - len(pre) >= 3:
                out.add(t[len(pre):])
                break
    return out


def categorize(text: str) -> str:
    norm = normalize(text)
    toks = _with_bare_arabic(set(norm.split()))
    best, best_hits = "Autre", 0
    for cat, words in CATEGORIES.items():
        hits = sum(1 for w in words if _kw_match(w, toks, norm))
        if hits > best_hits:
            best, best_hits = cat, hits
    return best


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]")


def clean_line(text: str) -> str:
    return _SPACES.sub(" ", _EMOJI.sub(" ", text or "")).strip(" -–—:|•*.!،")


def _informative(cand: str, page_name: str = "") -> bool:
    """Does this line name a product (and not 'Livraison gratuite 🔥')?"""
    if not cand or "{{" in cand or len(cand) < 4:
        return False
    if page_name and normalize(cand) == normalize(page_name):
        return False
    return len(tokens(cand, drop_boilerplate=True)) >= 2


def product_name(title: str, body: str, landing_title: str = "", page_name: str = "") -> str:
    """Best human-readable product name we can get for one ad: the title of
    its product page, else the ad headline, else the first meaningful line."""
    for cand in (landing_title, title):
        cand = clean_line(cand)
        if _informative(cand, page_name):
            return cand[:90]
    lines = [clean_line(ln) for ln in (body or "").splitlines()]
    for line in lines:
        if _informative(line, page_name):
            return line[:90]
    for cand in [clean_line(title)] + lines:
        if len(cand) >= 4 and "{{" not in cand:
            return cand[:90]
    return clean_line(body)[:90]


def search_terms(name: str, max_words: int = 5) -> str:
    """Short keyword string for store / AliExpress searches."""
    toks = tokens(clean_line(name), drop_boilerplate=True)
    return " ".join(toks[:max_words])


# ---------------------------------------------------------------- URLs

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|ttclid|ref$|campaign|ad_?id|adset|"
                       r"pixel|source$|medium$|h_ad_id)", re.I)
_SHOP_PLATFORMS = {
    "myshopify.com": "Shopify", "youcan.shop": "YouCan", "converty.shop": "Converty",
    "easyorders": "EasyOrders", "easy-orders": "EasyOrders", "lightfunnels": "Lightfunnels",
    "storeino": "Storeino", "shopyan": "Shopyan", "ecwid": "Ecwid", "wuilt": "Wuilt",
    "salla.sa": "Salla", "zid.store": "Zid", "expandcart": "ExpandCart",
}
_NOT_A_SHOP = ("facebook.com", "fb.com", "fb.me", "instagram.com", "messenger.com",
               "m.me", "wa.me", "whatsapp.com", "t.me", "youtube.com", "youtu.be",
               "tiktok.com", "google.com", "apple.com", "linktr.ee", "bit.ly")


def unwrap_url(url: str) -> str:
    """l.facebook.com/l.php?u=<real url>  ->  real url"""
    if not url:
        return ""
    try:
        p = urlparse(url)
        if p.netloc.endswith("facebook.com") and p.path.startswith("/l.php"):
            real = parse_qs(p.query).get("u", [""])[0]
            return unquote(real) or url
    except ValueError:
        pass
    return url


def domain_of(url: str) -> str:
    try:
        host = urlparse(url if "//" in url else "//" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_shop_url(url: str) -> bool:
    dom = domain_of(url)
    return bool(dom) and not any(dom == d or dom.endswith("." + d) for d in _NOT_A_SHOP)


def own_site(url: str) -> str:
    """Domain of the seller's own website. "" for social links, app stores and
    shops hosted on a shared platform (x.converty.shop, x.myshopify.com)."""
    dom = domain_of(url)
    if "." not in dom or not is_shop_url(url):
        return ""
    return "" if any(needle in dom for needle in _SHOP_PLATFORMS) else dom


def platform_from_url(url: str) -> str:
    low = (url or "").lower()
    for needle, name in _SHOP_PLATFORMS.items():
        if needle in low:
            return name
    return ""


def normalize_url(url: str) -> str:
    """Canonical landing URL for comparison: host + path, no tracking params."""
    url = unwrap_url(url)
    if not url or not is_shop_url(url):
        return ""
    try:
        p = urlparse(url if "//" in url else "https://" + url)
    except ValueError:
        return ""
    keep = []
    for part in p.query.split("&"):
        key = part.split("=")[0]
        if part and not _TRACKING.match(key):
            keep.append(part)
    path = re.sub(r"/+$", "", p.path)
    out = domain_of(url) + path
    if keep:
        out += "?" + "&".join(sorted(keep))
    return out.lower()


def url_path_is_product(norm_url: str) -> bool:
    """True when the URL points at one product, not at a shop home page."""
    if "/" not in norm_url:
        return False
    path = norm_url.split("/", 1)[1]
    return len(path) >= 4 and path not in ("collections", "shop", "products", "home")
