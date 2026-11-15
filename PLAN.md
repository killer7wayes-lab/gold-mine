# Gold Mine – Plan

A free tool that finds winning cash-on-delivery e-commerce products by watching the public
Meta Ad Library. Tunisia first, any country by config. Python only, no paid API, no AI API.

If you are another developer (or model) continuing this work: read this file, then
`git log --oneline`. Each build step below is one commit.

## 1. Architecture

```
                    (your Windows PC, twice a day)
 Task Scheduler ──► python -m goldmine run
                      │
                      ├─ 1 scraper.py   Playwright (headless, no login) ─► Ad Library website
                      │                 keyword discovery + watched-page re-scan (snowball)
                      ├─ 2 media.py     download thumbnails, perceptual hash
                      ├─ 3 landing.py   fetch landing pages: title, price, platform
                      ├─ 4 grouping.py  ads ─► products (text + image + landing similarity)
                      ├─ 5 signals.py   per-product raw signals + snapshot (trend history)
                      ├─ 6 sourcing.py  local stores search + AliExpress link
                      ├─ 7 export.py    SQLite ─► docs/data/*.json (+ product images)
                      ├─ 8 alerts.py    Telegram message for products crossing the score
                      └─ 9 publish.py   git commit + push docs/ ─► GitHub Pages

 data/goldmine.db (SQLite)  ◄──────────  panel/ (Flask, http://localhost:5000)
 config/config.json         ◄──────────  the panel is the only thing that writes config

 docs/ (GitHub Pages, static)  index.html + app.js: loads JSON, computes scores IN THE BROWSER
```

Key decisions

- **Scrape, not API.** We open the public Ad Library search page and read the JSON the page
  itself loads (GraphQL responses + JSON embedded in the HTML). We look for any object that has
  `ad_archive_id` + `snapshot`, wherever it is, so small layout changes do not break us. A DOM
  text fallback exists for when the JSON shape changes completely.
- **Scores are computed twice with the same formula**: in the browser (`docs/app.js`, live
  sliders) and in Python (`scoring.py`, for Telegram alerts and the panel). The exporter ships
  only raw signals + the default settings. Both implementations are checked against each other
  on 400 random products by `tests/test_scoring.py` (needs Node.js, skipped without).
- **Config is one JSON file with one block per country.** Defaults live in
  `goldmine/defaults.py`; `config/config.json` only stores what the user changed in the panel.
  It is git-ignored (it can hold the Telegram token).
- **Product ids are stable.** Grouping is incremental: an ad already assigned to a product
  never moves on its own. Only new ads are clustered and attached. Manual merge/split from the
  panel always wins and is remembered.
- **Images are copied locally** (Facebook CDN links expire in a few days): one small JPEG per
  exported product, stored in `docs/data/img/`.

## 2. Folder layout

```
gold-mine/
  setup.bat              one-command install (venv, deps, Chromium, DB, scheduled tasks)
  panel.bat / run.bat    start the control panel / run a scan now
  requirements.txt
  goldmine/
    defaults.py          default config: countries, keywords, currencies, costs, scoring
    config.py            load / save / merge config
    db.py                schema + helpers
    textutil.py          normalisation (Arabic, French, darija), tokenising
    pricing.py           price extraction from text using country currency patterns
    adparse.py           Ad Library JSON ─► clean ad dict (pure, unit-tested)
    scraper.py           Playwright driver: pacing, retries, block detection, resume
    media.py             thumbnails + imagehash
    landing.py           landing page fetch + platform price patterns
    grouping.py          product grouping + merge/split
    signals.py           raw signals per product + snapshots + trend
    scoring.py           score /100 + French reason (mirror of docs/app.js)
    sourcing.py          local stores + AliExpress link
    export.py            JSON for the dashboard
    alerts.py            Telegram
    publish.py           git push
    scheduler.py         Windows scheduled tasks
    pipeline.py          one full run, with logging and resume
    cli.py / __main__.py
    panel/               Flask app + templates
  docs/                  GitHub Pages dashboard (French, mobile-first)
  tests/
  data/                  goldmine.db, thumbs/, logs/   (git-ignored)
  config/config.json     (git-ignored)
```

## 3. Data model (SQLite)

| Table | Key | What |
|---|---|---|
| `runs` | id | one row per run: start, end, status, counters, error |
| `tasks` | id | scan queue of a run: (country, kind=keyword/page, value, status). Lets a crashed run resume where it stopped |
| `pages` | (page_id, country) | advertiser page: name, first/last seen, `is_ecom`, `watch` (auto or manual), `ignored`, last scan |
| `ads` | (ad_id, country) | ad archive id, page, start/end date, active, `collation_count` ("X ads use this creative"), text, title, CTA, landing URL, platforms, media URLs, thumbnail path, phash, price, product_id, `pinned` |
| `ad_snapshots` | (run_id, ad_id, country) | active?, collation count, price at that run |
| `landings` | url | cached landing page: title, price, platform, status |
| `products` | id | country, name, category, image, supplier price (manual), `global_id` (cross-country link), manual name |
| `product_snapshots` | (product_id, day) | sellers, active ads, new ads 7d, duplicates, median/min price ─► trends |
| `sourcing` | (product_id, store) | best local store match: title, price, URL, checked_at |
| `alerts` | (product_id) | last score alerted, when |

An ad seen in two countries is two rows (same `ad_id`, different `country`): everything is
per country. Cross-country links are done at product level through `global_id`.

## 4. Product grouping

For each country, new ads are compared with each other and with existing products.

1. Text: TF-IDF cosine on normalised ad copy + title (boilerplate such as "livraison gratuite"
   gets a low weight automatically), and rapidfuzz `token_set_ratio` on product names.
2. Image: perceptual hash (pHash 64 bit) of the thumbnail, Hamming distance.
3. Landing: same normalised URL, or same domain + similar path/title.

`similarity = 0.50·text + 0.30·image + 0.20·landing` (missing parts are re-weighted).
Shortcuts: near-identical image (distance ≤ 6), same product landing URL, or text ≥ 0.90 link
directly. Link if similarity ≥ 0.62 (adjustable). Clusters are built with union-find; a new ad
that matches an existing product joins it.

Cross-country: products of different countries get the same `global_id` when their images
match (hash distance ≤ 8) or names are very similar.

Manual override: merge (all ads move, kept forever) and split (chosen ads go to a new
product and are pinned so they never drift back).

## 5. Scoring

### Raw signals (per product, per country) – computed in Python, exported as is

| Signal | Meaning |
|---|---|
| `days` | age in days of the oldest ad still active |
| `sellers` | distinct pages with an active ad |
| `sellers_stopped` | pages that ran it before and have nothing active now |
| `active_ads`, `total_ads` | |
| `new7` | ads started in the last 7 days |
| `dups` | highest "X ads use this creative" among active ads |
| `trend` | −1…+1, from snapshots (active ads + sellers now vs ~7 days ago); before 3 days of history exist, from start dates |
| `price`, `price_min`, `price_drop` | median / lowest selling price, % change of median vs first snapshots |
| `supplier` | manual supplier price if entered |

### Score /100 (same formula in `scoring.py` and `docs/app.js`)

Each part is turned into 0…1, then weighted. Default weights (sum 100):

| Part | Weight | 0…1 curve |
|---|---|---|
| Days running | 25 | `min(days / 45, 1)` |
| Sellers | 20 | 1 seller 0.40 · 2 sellers 0.70 · 3 up to 60 % of `max_sellers` 1.0 · then falls linearly to 0.40 at `max_sellers` (floor 0.15) |
| New ads this week | 15 | `min(new7 / 5, 1)` |
| Duplicate creatives | 10 | `min(log(dups) / log(10), 1)` (1 = no duplicate = 0 point) |
| Trend | 15 | `(trend + 1) / 2` |
| Margin | 15 | `clamp(margin / margin_target, 0, 1)` (`margin_target` per country, 40 DT in TN); unknown price = 0 |

Penalties (after weighting): no active ad → ×0.35; price war (median price dropped ≥ 15 %,
or cheapest seller ≥ 35 % under the median with ≥ 3 sellers) → −8; more than half of the
sellers stopped → −8. Result clamped to 0…100.

Trend label: `rising` if trend ≥ 0.15, `dying` if ≤ −0.15 (or nothing active), else `stable`.

### Money

```
ad_cost   = country.ad_cost × tier multiplier       (low 0.7 · medium 1.0 · high 1.5)
returns   = r / (1 − r) × (delivery + ad_cost)      r = country return rate
supplier  = manual price, else price × est_supplier_ratio (default 35 %, shown with "~")
margin    = price − supplier − delivery − ad_cost − returns
```

`returns` reads as: for every parcel delivered, `r/(1−r)` parcels come back, and each one
wasted its shipping and its ad money.

Ad cost tier (an estimate, never real data): starts from competition (sellers ≤ 2 low,
≤ `max_sellers` medium, above high) and moves one tier up when creatives are tired
(oldest active ad > 60 days and nothing new in 7 days), one tier down when the product is
fresh (< 14 days, ≤ 3 sellers).

Reason line (French), built from the signals, e.g.
`Actif depuis 52 jours, 7 vendeurs, 4 nouvelles pubs cette semaine, marge ~30 DT`.

Thresholds (filters, not score): min score, min margin, max sellers, min days.
Presets shipped: "Faible concurrence", "Grosse marge", "En pleine croissance".

## 6. Scraper behaviour

- URLs: `facebook.com/ads/library/?active_status=active&ad_type=all&country=XX&q=…&search_type=keyword_unordered`
  for discovery, `…&view_all_page_id=ID&search_type=page` for watched pages (active ads only: when a
  page scan reaches the end of the list, its ads we did not see are marked stopped).
- Browser: the installed Chrome, else Edge, else Playwright's Chromium. Windows Smart App Control
  blocks the unsigned bundled Chromium on some PCs, the installed browsers always work.
- Never logs in. If Facebook redirects to login / checkpoint / shows a rate-limit message or
  returns 429, the run backs off (exponential wait), and after 3 blocks stops scraping for
  this run and keeps what it has.
- Random 2–6 s pauses between scrolls, 8–20 s between searches, max ads per run and per
  search (panel settings). One browser, one tab.
- Each search is a row in `tasks`; an interrupted run is resumed by the next start if it is
  less than 6 hours old.
- Snowball: a page becomes "e-commerce" when one of its ads has a price, a COD keyword or a
  known shop platform landing. E-commerce pages are auto-watched; each run re-scans the
  watched pages that were scanned longest ago (limit per run).

## 7. Build order (one commit each)

0. PLAN.md
1. Skeleton (config, DB, CLI) + Ad Library scraper
2. Product grouping (+ thumbnails, hashes, landing pages)
3. Winner scoring (price extraction, signals, snapshots, score, reason)
4. Sourcing & margin
5. Dashboard + live settings
6. Control panel, automation, alerts, docs

## 8. Not in scope / honest limits

- Ad spend, reach and real sales are not public for commercial ads: every profit number is an
  estimate built from visible behaviour (how long, how many sellers, how much duplication).
- Facebook can change the Ad Library at any time; the parser is tolerant but may need a fix.
- AliExpress is never scraped: we only build a search link.

## 9. Status (for whoever continues)

Done and committed: steps 0-4 (plan, scraper, grouping, scoring, sourcing & margin).
`python -m goldmine run` and `python -m goldmine process` work end to end up to sourcing.

Still to build:
- Step 5: `goldmine/export.py` (SQLite -> `docs/data/index.json` + `docs/data/<CC>.json` + product
  images in `docs/data/img/`, using `signals.compute_country`, `signals.new_sellers`,
  `sourcing.links_for`) and the dashboard `docs/index.html` + `app.js` + `style.css`
  (French, mobile-first, tabs, filters, live sliders, presets in localStorage). `docs/score.js`
  (the scorer) already exists and is tested against `scoring.py`.
- Step 6: `goldmine/panel/` (Flask), `scheduler.py` (schtasks, 07:00 / 14:00 Tunis time),
  `alerts.py` (Telegram), `publish.py` (git push docs/), `setup.bat`, `panel.bat`, `run.bat`.
  `cli.py` already references `scheduler.setup()` and `panel.app.serve()`.
- README.md and the Windows setup guide.

Known issues:
- Sourcing: Spacenet and Tunisianet searches work. Mytek answers HTTP 403 to plain requests and
  SBS Informatique returns no items with the PrestaShop selectors: both need their selectors /
  access checked (the search links are still generated for them).
- Grouping across several sellers is covered by unit tests but has only been seen on a small
  live sample (120 ads); check the groups after a full-size scan.
- `setup` must not rely on `playwright install`: Windows Smart App Control blocks the bundled
  Chromium on this PC. The scraper already uses installed Chrome, then Edge.
