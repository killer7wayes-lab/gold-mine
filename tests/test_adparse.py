import json

from goldmine import adparse


def _node(**over):
    node = {
        "ad_archive_id": "111222333",
        "collation_count": 4, "collation_id": "999",
        "page_id": "555", "page_name": "Boutique Test", "is_active": True,
        "start_date": 1785222000, "end_date": 1794643200,
        "publisher_platform": ["FACEBOOK", "INSTAGRAM"],
        "snapshot": {
            "page_name": "Boutique Test", "page_like_count": 1200,
            "page_categories": ["Shopping & retail"],
            "page_profile_picture_url": "https://cdn.example/p.jpg",
            "body": {"text": "Mixeur portable 89 DT\nLivraison gratuite"},
            "title": "Mixeur portable", "caption": "shop.example",
            "cta_type": "SHOP_NOW", "cta_text": "Shop now",
            "link_url": "https://l.facebook.com/l.php?u=https%3A%2F%2Fshop.example%2Fmixeur%3Ffbclid%3Dabc&h=x",
            "link_description": None, "display_format": "VIDEO",
            "images": [],
            "videos": [{"video_preview_image_url": "https://cdn.example/v.jpg",
                        "video_sd_url": "https://cdn.example/v.mp4"}],
            "cards": [],
        },
    }
    node.update(over)
    return node


def _response(nodes, has_next=True):
    return json.dumps({"data": {"ad_library_main": {"search_results_connection": {
        "edges": [{"node": {"collated_results": [n]}} for n in nodes],
        "page_info": {"has_next_page": has_next, "end_cursor": "abc"}}}}})


def test_parse_graphql_response():
    docs = adparse.parse_payload(_response([_node()]))
    ads, info, error = adparse.ads_from_documents(docs, "TN")
    assert error == "" and info["has_next_page"] is True
    ad = ads[0]
    assert ad["ad_id"] == "111222333" and ad["country"] == "TN"
    assert ad["page_id"] == "555" and ad["page_name"] == "Boutique Test"
    assert ad["start_date"] == "2026-07-28" and ad["is_active"] == 1
    assert ad["collation_count"] == 4
    assert ad["landing_url"] == "https://shop.example/mixeur?fbclid=abc"
    assert ad["landing_domain"] == "shop.example"
    assert ad["platforms"] == ["facebook", "instagram"]
    assert ad["image_url"] == "https://cdn.example/v.jpg"
    assert ad["cta"] == "SHOP_NOW"


def test_catalogue_ad_uses_first_card():
    node = _node()
    node["snapshot"].update({
        "title": "{{product.name}}", "body": {"text": "{{product.description}}"},
        "link_url": "https://shop.example/",
        "cards": [{"title": "Lunettes soleil", "body": "Promo 39 DT",
                   "link_url": "https://shop.example/p/lunettes",
                   "resized_image_url": "https://cdn.example/c.jpg", "cta_type": "ORDER_NOW"}],
    })
    ad = adparse.parse_ad(node, "TN")
    assert ad["title"] == "Lunettes soleil" and ad["body"] == "Promo 39 DT"
    assert ad["landing_url"] == "https://shop.example/p/lunettes"


def test_prefix_multiline_and_missing_collation():
    text = "for (;;);" + _response([_node(collation_count=None)]) + "\n" + json.dumps({"x": 1})
    ads, _, _ = adparse.ads_from_documents(adparse.parse_payload(text), "MA")
    assert len(ads) == 1 and ads[0]["collation_count"] == 1 and ads[0]["country"] == "MA"


def test_embedded_html_and_error():
    html = ('<html><script type="application/json" data-sjs>'
            + json.dumps({"require": [["x", {"deep": [_node(ad_archive_id="42")]}]]})
            + "</script></html>")
    ads, _, _ = adparse.ads_from_documents(adparse.parse_html(html), "TN")
    assert [a["ad_id"] for a in ads] == ["42"]
    _, _, error = adparse.ads_from_documents(
        [{"errors": [{"message": "Rate limit exceeded"}]}], "TN")
    assert "Rate limit" in error


def test_dom_fallback():
    cards = [{
        "text": "Active\nLibrary ID: 987654321\nStarted running on Oct 3, 2026\nPlatforms\n"
                "3 ads use this creative and text\nMa Boutique\nSponsored\nBrosse 45 DT\nShop now",
        "links": ["https://www.facebook.com/ads/library/?view_all_page_id=777",
                  "https://l.facebook.com/l.php?u=https%3A%2F%2Fex.tn%2Fbrosse"],
        "images": ["https://scontent.fbcdn.net/a.jpg"],
    }]
    ad = adparse.parse_dom_cards(cards, "TN")[0]
    assert ad["ad_id"] == "987654321" and ad["start_date"] == "2026-10-03"
    assert ad["collation_count"] == 3 and ad["page_name"] == "Ma Boutique"
    assert ad["page_id"] == "777" and ad["landing_url"] == "https://ex.tn/brosse"
    assert "Brosse 45 DT" in ad["body"] and ad["is_active"] == 1
