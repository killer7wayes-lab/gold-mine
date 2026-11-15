import pytest

from goldmine import pricing
from goldmine.defaults import DEFAULT_CONFIG

TN = DEFAULT_CONFIG["countries"]["TN"]
MA = DEFAULT_CONFIG["countries"]["MA"]
DZ = DEFAULT_CONFIG["countries"]["DZ"]
FR = DEFAULT_CONFIG["countries"]["FR"]


@pytest.mark.parametrize("text,expected", [
    ("Prix: 89 DT livraison gratuite", 89),
    ("seulement 89dt 🔥", 89),
    ("89,900 DT au lieu de 129,000 DT", 89.9),
    ("السعر 49 د.ت فقط", 49),
    ("٥٩ دينار و التوصيل مجاني", 59),
    ("بسوم 35د برك", 35),
    ("1 pièce 49 DT, 2 pièces 89 DT", 49),
    ("Livraison 7 DT. Prix 65 DT", 65),
    ("Livraison gratuite ✅ Prix 89 DT", 89),
    ("التوصيل 8 دينار\nالسعر 120 دينار", 120),
    ("Remise 20 DT ! Maintenant 79 TND", 79),
    ("PC portable 1.299 DT", 1299),
    ("Smart TV 2 499 DT", 2499),
    ("lot de 4 199dt", 199),
    ("prix 75 seulement", 75),
    ("-50% sur tout le site", None),
    ("Appelez le 55 123 456", None),
    ("Promo DT", None),
    ("Pâtée chat 3,000 DT", None),
    ("12,500 DT", 12.5),
    ("47d livraison gratuite meuble d'entrée", 47),
    ("🚚 𝐋𝐢𝐯𝐫𝐚𝐢𝐬𝐨𝐧 gratuite dès 100 DT", None),
    ("𝐏𝐫𝐢𝐱 𝟔𝟓 𝐃𝐓", 65),
    ("écran 3D incroyable", None),
])
def test_tunisia(text, expected):
    assert pricing.best_price(text, TN) == expected


@pytest.mark.parametrize("country,text,expected", [
    (MA, "الثمن 199 درهم التوصيل بالمجان", 199),
    (MA, "Prix 249DH au lieu de 399 DH", 249),
    (MA, "1,299.00 MAD", 1299),
    (DZ, "السعر 3900 دج", 3900),
    (DZ, "Prix 4 500 DA livraison 58 wilayas", 4500),
    (DZ, "2.900 DA", 2900),
    (FR, "Seulement 29,99 € aujourd'hui", 29.99),
    (FR, "€ 49.90", 49.9),
])
def test_other_countries(country, text, expected):
    assert pricing.best_price(text, country) == expected


def test_currency_word_inside_another_word_is_ignored():
    # "dt" inside a word, "da" inside "dans"
    assert pricing.best_price("modèle 2024 dtx", TN) is None
    assert pricing.best_price("livré en 24 dans tout le pays", DZ) is None
