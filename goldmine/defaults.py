"""Default configuration. Nothing here is edited by hand after install:
the control panel saves the user's changes in config/config.json and they are
merged over these defaults at load time.

Everything country-specific lives in one block per country under "countries".
"""

PRESTASHOP = {
    "item": ".product-miniature, .ajax_block_product, .tvproduct-wrapper",
    "title": ".product-title a, .product-name, h2 a, h3 a",
    "price": ".price, .product-price",
}
MAGENTO = {
    "item": ".product-item, .product-container, li.item.product",
    "title": ".product-item-link, .product-name a, h1 a, h2 a, h3 a",
    "price": ".final-price, .special-price .price, .price",
}

# what the Tunisian tech stores sell (names from CATEGORIES)
TECH_STORE = ["Électronique", "Cuisine", "Maison", "Bricolage & jardin"]


def _country(name, flag, currency, tokens, decimals, price_min, price_max,
             delivery, return_rate, ad_cost, margin_target, keywords,
             stores=None, active=False, aliexpress_lang="fr"):
    return {
        "name": name,
        "flag": flag,
        "active": active,
        "currency": currency,              # label shown on the dashboard
        "currency_tokens": tokens,         # how prices are written in ads
        "decimals": decimals,              # 3 = millimes (89,900 DT = 89.9)
        "price_min": price_min,            # ignore "prices" outside this range
        "price_max": price_max,
        "delivery_cost": delivery,
        "return_rate": return_rate,        # 0.25 = 25 % of parcels come back
        "ad_cost": ad_cost,                # typical ad cost per order (medium tier)
        "margin_target": margin_target,    # margin that gets the full "margin" points
        "keywords": keywords,
        "niches": [],                      # names from NICHES, ticked in the panel
        "niche_only": False,               # True = skip the general keywords
        "stores": stores or [],
        "aliexpress_lang": aliexpress_lang,
    }


DEFAULT_CONFIG = {
    "general": {
        "run_times": ["07:00", "14:00"],     # Tunis time (UTC+1)
        "max_ads_per_run": 600,
        "max_ads_per_search": 60,
        "max_page_scans_per_run": 15,
        "max_landing_fetches_per_run": 60,
        "max_sourcing_per_run": 15,
        "export_top_n": 300,
        "headless": True,
        "publish": True,                     # git push docs/ after each run
        "panel_port": 5000,
        "pause_scroll": [2.0, 6.0],          # seconds, random between both
        "pause_search": [8.0, 20.0],
        "stale_days": 10,                    # ad not seen for N days = stopped
        "ignored_page_categories": [         # pages that are never e-commerce
            "Restaurant", "Food delivery service", "Bank", "Real Estate",
            "Telecommunication company", "Insurance company", "Travel Company",
            "Hotel", "School", "Government organization", "Political Party",
        ],
    },
    "telegram": {"enabled": False, "token": "", "chat_id": "", "min_score": 75},
    "grouping": {
        "link_threshold": 0.62,
        "image_distance": 6,                 # pHash bits that may differ
        "cross_country_image_distance": 8,
        "w_text": 0.50, "w_image": 0.30, "w_landing": 0.20,
        "text_only_min": 0.75,               # copy similarity needed when pictures differ
    },
    # Default scoring settings. The dashboard starts from these and lets the
    # user move every one of them live (stored in the browser).
    "scoring": {
        "weights": {"days": 25, "sellers": 20, "new7": 15, "dups": 10,
                    "trend": 15, "margin": 15},
        "thresholds": {"min_score": 0, "min_margin": None, "max_sellers": 8,
                       "min_days": 0},
        "days_full": 45,
        "new7_full": 5,
        "dups_full": 10,
        "est_supplier_ratio": 0.35,
        "tier_multipliers": {"low": 0.7, "medium": 1.0, "high": 1.5},
    },
    "presets": {
        "Faible concurrence": {
            "weights": {"days": 25, "sellers": 35, "new7": 10, "dups": 5,
                        "trend": 10, "margin": 15},
            "thresholds": {"min_score": 0, "min_margin": None, "max_sellers": 3,
                           "min_days": 7},
        },
        "Grosse marge": {
            "weights": {"days": 20, "sellers": 10, "new7": 10, "dups": 5,
                        "trend": 10, "margin": 45},
            "thresholds": {"min_score": 0, "min_margin": 30, "max_sellers": 10,
                           "min_days": 5},
        },
        "En pleine croissance": {
            "weights": {"days": 5, "sellers": 15, "new7": 35, "dups": 10,
                        "trend": 30, "margin": 5},
            "thresholds": {"min_score": 0, "min_margin": None, "max_sellers": 12,
                           "min_days": 0},
        },
    },
    "countries": {
        "TN": _country(
            "Tunisie", "🇹🇳", "DT",
            ["DT", "TND", "D.T", "dt", "d", "dinars", "dinar", "د.ت", "دت", "دينار", "د"],
            3, 9, 3000, delivery=8, return_rate=0.25, ad_cost=10, margin_target=40,
            keywords=[
                "livraison gratuite", "paiement à la livraison",
                "livraison à domicile", "livraison sur toute la tunisie",
                "commandez maintenant", "commander maintenant",
                "الدفع عند الاستلام", "التوصيل مجاني", "توصيل لكامل الولايات",
                "اطلب الآن", "الخلاص عند الاستلام", "توصيل مجاني",
                "التوصيل لكل الولايات", "أطلب توا", "بسوم", "تخفيض",
                "الكمية محدودة", "عرض خاص", "stock limité", "promo dt",
                "prix", "DT", "TND",
            ],
            # "categories": these are tech / appliance stores, so they are only
            # looked up for products of those categories (never for shoes).
            stores=[
                {"name": "Tunisianet", "enabled": True, "categories": TECH_STORE,
                 "search_url": "https://www.tunisianet.com.tn/recherche?controller=search&s={q}",
                 **PRESTASHOP},
                # Mytek refuses automatic reading (HTTP 403): search link only
                {"name": "Mytek", "enabled": True, "link_only": True, "categories": TECH_STORE,
                 "search_url": "https://www.mytek.tn/catalogsearch/result/?q={q}",
                 **MAGENTO},
                {"name": "Spacenet", "enabled": True, "categories": TECH_STORE,
                 "search_url": "https://spacenet.tn/recherche?controller=search&s={q}",
                 **PRESTASHOP},
                {"name": "SBS Informatique", "enabled": True, "categories": ["Électronique"],
                 "search_url": "https://www.sbsinformatique.com/recherche?controller=search&s={q}",
                 "item": ".tvproduct-wrapper", "title": ".tvproduct-name a, .product-title a",
                 "price": ".price"},
                # general marketplace (everything, shoes included): search link only
                {"name": "Jumia", "enabled": True, "link_only": True,
                 "search_url": "https://www.jumia.com.tn/catalog/?q={q}"},
            ],
            active=True,
        ),
        "MA": _country(
            "Maroc", "🇲🇦", "DH",
            ["DH", "MAD", "Dhs", "dhs", "dh", "درهم", "د.م", "دراهم"],
            2, 29, 20000, delivery=35, return_rate=0.30, ad_cost=40, margin_target=120,
            keywords=[
                "livraison gratuite", "paiement à la livraison",
                "livraison partout au maroc", "commandez maintenant",
                "الدفع عند الاستلام", "التوصيل بالمجان",
                "التوصيل مجاني لجميع المدن", "التوصيل لجميع المدن المغربية",
                "اطلب الآن", "طلب دابا", "الثمن", "درهم", "DH", "تخفيض",
                "العرض محدود", "الخلاص عند الاستلام",
            ],
        ),
        "DZ": _country(
            "Algérie", "🇩🇿", "DA",
            ["DA", "DZD", "da", "dzd", "دج", "د.ج", "دينار", "دينار جزائري"],
            2, 500, 400000, delivery=600, return_rate=0.30, ad_cost=500, margin_target=1800,
            keywords=[
                "livraison 58 wilayas", "التوصيل 58 ولاية",
                "الدفع عند الاستلام", "paiement à la livraison",
                "livraison disponible", "التوصيل متوفر", "اطلب الآن",
                "commandez maintenant", "السعر", "دج", "DA", "تخفيض",
                "livraison à domicile", "التوصيل لباب الدار", "السومة",
            ],
        ),
        "FR": _country(
            "France", "🇫🇷", "€", ["€", "EUR", "euros", "euro"],
            2, 5, 2000, delivery=5, return_rate=0.08, ad_cost=12, margin_target=20,
            keywords=["livraison gratuite", "commandez maintenant",
                      "satisfait ou remboursé", "-50%", "stock limité"],
        ),
        "SA": _country(
            "Arabie saoudite", "🇸🇦", "SAR",
            ["SAR", "ر.س", "ريال", "رس", "SR"],
            2, 19, 5000, delivery=25, return_rate=0.25, ad_cost=45, margin_target=90,
            keywords=["الدفع عند الاستلام", "شحن مجاني", "اطلب الآن",
                      "توصيل مجاني", "العرض محدود", "ريال"],
            aliexpress_lang="ar",
        ),
        "AE": _country(
            "Émirats", "🇦🇪", "AED",
            ["AED", "د.إ", "درهم", "Dhs", "DHS"],
            2, 19, 5000, delivery=20, return_rate=0.20, ad_cost=45, margin_target=90,
            keywords=["الدفع عند الاستلام", "cash on delivery", "free delivery",
                      "شحن مجاني", "اطلب الآن", "order now"],
            aliexpress_lang="ar",
        ),
    },
}

# Words that make an ad look like cash-on-delivery e-commerce (any language).
ECOM_HINTS = [
    "livraison", "paiement a la livraison", "commandez", "commander", "commande",
    "prix", "promo", "stock", "remise", "offre",
    "توصيل", "التوصيل", "الدفع عند", "الخلاص عند", "اطلب", "للطلب", "السعر",
    "سعر", "سوم", "الثمن", "تخفيض", "عرض", "الكميه", "شحن",
    "cash on delivery", "order now", "free delivery", "free shipping", "shop now",
]

SHOP_CTAS = {"SHOP_NOW", "ORDER_NOW", "BUY_NOW", "GET_OFFER", "BUY_TICKETS",
             "SEE_MENU", "GET_PROMOTIONS"}

# Words around a number that mean "this is a shipping fee, not the price".
DELIVERY_WORDS = ["livraison", "frais", "توصيل", "التوصيل", "شحن", "delivery",
                  "shipping"]

# Category keywords (normalised text, see textutil.normalize).
CATEGORIES = {
    "Beauté": ["creme", "serum", "peau", "visage", "cheveux", "maquillage",
               "parfum", "epilat", "rides", "acne", "shampo", "huile", "cils",
               "ongles", "بشره", "شعر", "كريم", "سيروم", "عطر", "تجاعيد",
               "مكياج", "تبييض", "الوجه"],
    "Santé": ["douleur", "massage", "masseur", "minceur", "posture", "genou",
              "dos", "sommeil", "orthoped", "tension", "ceinture", "correcteur",
              "الم", "مساج", "تنحيف", "ظهر", "ركبه", "طبي", "صحه", "تدليك",
              "الام"],
    "Cuisine": ["cuisine", "cuisson", "mixeur", "blender", "couteau", "poele",
                "friteuse", "hachoir", "eplucheur", "cafetiere", "مطبخ",
                "خلاط", "مقلاه", "سكين", "طبخ", "فرامه", "قهوه"],
    "Maison": ["maison", "rangement", "nettoy", "aspirateur", "lampe", "led",
               "matelas", "coussin", "rideau", "salle de bain", "meuble",
               "etagere", "balai", "serpill", "منزل", "تنظيف", "تخزين",
               "مصباح", "وساده", "ستاره", "حمام", "مكنسه", "اضاءه", "دار"],
    "Électronique": ["bluetooth", "ecouteur", "casque", "chargeur", "smartwatch",
                     "montre connect", "camera", "usb", "sans fil", "wifi",
                     "projecteur", "powerbank", "telephone", "enceinte",
                     "سماعه", "سماعات", "شاحن", "كاميرا", "ساعه ذكيه",
                     "لاسلكي", "هاتف", "بلوتوث"],
    "Mode": ["robe", "chaussure", "sac", "montre", "lunettes", "veste",
             "pantalon", "ensemble", "pyjama", "basket", "bijou", "collier",
             "حذاء", "حقيبه", "فستان", "ساعه", "نظارات", "ملابس", "طقم",
             "سروال", "قميص", "عبايه"],
    "Auto": ["voiture", "auto", "moto", "pneu", "siege auto", "pare-brise",
             "dashcam", "سياره", "كرهبه", "سيارات", "عجله"],
    "Bébé & enfants": ["bebe", "enfant", "jouet", "poussette", "biberon",
                       "scolaire", "cartable", "طفل", "اطفال", "رضيع",
                       "لعبه", "العاب", "صغار"],
    "Sport": ["sport", "fitness", "muscu", "abdos", "yoga", "velo",
              "halteres", "رياضه", "تمارين", "عضلات", "دراجه"],
    "Alimentation": ["miel", "huile d'olive", "dattes", "epice", "chocolat",
                     "complement alimentaire", "cafe", "عسل", "زيت الزيتون",
                     "تمر", "قهوه", "توابل", "شوكولا"],
    "Animaux": ["chat", "chien", "animaux", "croquette", "litiere", "aquarium",
                "قطط", "قط", "كلب", "كلاب", "حيوانات"],
    "Bricolage & jardin": ["outil", "perceuse", "jardin", "arrosage", "colle",
                           "tournevis", "soudure", "pompe", "ادوات", "حديقه",
                           "مثقاب", "لحام", "ري", "مضخه"],
}


# One-click niches (control panel > Mots-clés). Each niche adds these searches
# to the country's scans. Names match CATEGORIES so the dashboard's category
# filter shows the same niche. French + Arabic work across the Maghreb.
NICHES = {
    "Beauté": ["sérum visage", "crème anti-rides", "parfum", "soin cheveux", "épilateur",
               "maquillage", "العناية بالبشرة", "سيروم", "عطر", "تساقط الشعر"],
    "Santé": ["masseur", "ceinture lombaire", "correcteur de posture", "anti douleur",
              "minceur", "مساج", "آلام الظهر", "تخسيس"],
    "Cuisine": ["ustensiles de cuisine", "mixeur", "friteuse sans huile", "robot de cuisine",
                "hachoir", "poêle", "مطبخ", "خلاط", "قلاية"],
    "Maison": ["rangement", "décoration maison", "matelas", "aspirateur", "lampe LED",
               "canapé", "ديكور", "تنظيف", "للمنزل"],
    "Électronique": ["écouteurs bluetooth", "smartwatch", "chargeur rapide",
                     "caméra de surveillance", "projecteur", "power bank", "سماعات",
                     "ساعة ذكية", "كاميرا مراقبة"],
    "Mode": ["chaussures", "montre", "sac à main", "robe", "lunettes de soleil",
             "ensemble femme", "حذاء", "ساعة", "حقيبة"],
    "Auto": ["accessoires voiture", "aspirateur voiture", "support téléphone voiture",
             "housse siège voiture", "للسيارة", "اكسسوارات سيارات"],
    "Bébé & enfants": ["jouet", "bébé", "poussette", "jeux éducatifs", "ألعاب أطفال",
                       "للرضع", "للأطفال"],
    "Sport": ["fitness", "musculation", "tapis de course", "vélo d'appartement",
              "haltères", "رياضة", "تمارين"],
    "Alimentation": ["miel", "complément alimentaire", "huile d'olive", "dattes", "عسل",
                     "مكمل غذائي"],
    "Animaux": ["chat", "chien", "croquettes", "accessoires animaux", "قطط", "كلاب"],
    "Bricolage & jardin": ["perceuse", "outils", "visseuse", "jardin", "arrosage",
                           "أدوات", "حديقة"],
}
