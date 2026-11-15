# ⛏️ Gold Mine

Find winning cash-on-delivery products by watching what sellers advertise on Facebook and
Instagram. Tunisia first, any country by settings. **Free to run**: Python only, no paid API,
no AI API, no Facebook login.

- **Scraper** – reads the public Meta Ad Library website twice a day from your own PC.
- **Dashboard** – a French, phone-friendly web page on GitHub Pages, with live sliders.
- **Control panel** – a local page (`http://localhost:5000`) to change every setting. You never edit a file.

New here? Follow **[SETUP_WINDOWS.md](SETUP_WINDOWS.md)** (about 15 minutes).
Developer or continuing the build? Read **[PLAN.md](PLAN.md)**.

---

## How it works, in simple words

1. **It searches the Ad Library** of each active country with your keywords
   ("livraison gratuite", "الدفع عند الاستلام", "DT"…) and keeps the ads that look like
   e-commerce: a price, a delivery/COD word, a "Shop now" button.
2. **Snowball.** Every shop page it finds goes on a watchlist, and its ads are re-read on later
   scans. That is how it notices when an ad stops.
3. **It groups ads into products.** Ten pages selling the same blender become one product, using
   three clues: similar text, the same picture (even resized), the same sales page.
4. **It measures each product**: how long, how many sellers, how many new ads this week, how many
   copies of the same creative, going up or down, the price.
5. **It gives a score out of 100** and a one-line reason, for example
   `Actif depuis 52 jours, 7 vendeurs, 4 nouvelles pubs cette semaine, marge ~30 DT`.
6. **It publishes** the result to your dashboard and can ping you on Telegram.

Everything is stored in one file on your PC (`data/goldmine.db`), with a snapshot every day, so
trends get better the longer it runs.

---

## How the score works

The idea: **nobody pays for an ad that loses money for long.** So behaviour you can see from the
outside tells you what sells.

| Signal | Why it matters | Full points when… |
|---|---|---|
| **Days running** | A long-running ad is a profitable ad | the oldest active ad is 45+ days old |
| **Number of sellers** | Several sellers = proven demand. Too many = crowded | 3 sellers up to ~60 % of your "max sellers" |
| **New ads this week** | Sellers are relaunching: it is scaling *now* | 5 or more ads started in the last 7 days |
| **Duplicate creatives** | "10 ads use this creative" = budget is being pushed | 10 copies of one creative |
| **Trend** | Is it growing or dying compared with last week? | active ads + sellers doubled |
| **Margin** | What you keep per sale | margin reaches the country's "ideal margin" (40 DT in Tunisia) |

Each signal becomes a number between 0 and 1, is multiplied by its **weight**, and the total is
scaled to 100. Default weights: days 25, sellers 20, new ads 15, duplicates 10, trend 15,
margin 15.

Sellers follow a hill, not a line: 1 seller = 0.4 (unproven), 2 = 0.7, 3 to about 5 = 1.0, then
it goes down to 0.4 as you approach your "max sellers" limit.

Penalties after that:

- no active ad any more → score × 0.35
- price war (the usual price dropped 15 %, or someone sells 35 % cheaper than the others) → −8
- more sellers have stopped than are still running → −8

### The margin

```
margin = selling price − supplier price − delivery − ad cost per sale − returns allowance
```

- **Selling price**: read from the ad text, otherwise from the sales page (Shopify, WooCommerce,
  YouCan, Converty, EasyOrders and others). The usual (median) price among sellers is used.
- **Supplier price**: the one you type in the control panel. Until you do, it is guessed as 35 %
  of the selling price and shown with a `~`.
- **Ad cost per sale**: your country setting, × 0.7 when competition is low, × 1.5 when it is
  high or the creatives are old. **This is an estimate from what is visible, not real data.**
  Facebook does not publish spend for normal ads.
- **Returns allowance**: with 25 % returns, every 3 delivered parcels pay for 1 that came back
  (its delivery and its ad). Formula: `rate / (1 − rate) × (delivery + ad cost)`.

A product with no readable price gets 0 for margin and says `prix inconnu`.

### Trend

Each scan saves a daily snapshot. The trend compares "active ads + 2 × sellers" today with about
7 days ago. During the first 4 days there is no history, so it is estimated from the ads' start
dates (the dashboard tells you).

---

## How to tune it

### On the dashboard (works on your phone, instant)

Open **Réglages**. Nothing is re-scanned: the page recalculates every score as you move a slider,
and your settings stay in that browser.

| You want… | Do this |
|---|---|
| Products few people sell yet | Preset **Faible concurrence**, or raise "Nombre de vendeurs" and set "Vendeurs maximum" to 3 |
| Comfortable profit per sale | Preset **Grosse marge**, or raise "Marge" and set "Marge minimum" |
| What is taking off right now | Preset **En pleine croissance** (new ads + trend), and look at the **Tendances** tab |
| Only proven, stable products | Raise "Ancienneté", set "Jours en ligne minimum" to 30 |
| Realistic margins | Set your real delivery cost, return rate and ad cost under "Coûts" |

Save your own mix with **Enregistrer**; **Valeurs par défaut** brings everything back.

### In the control panel (on your PC)

Double-click `panel.bat`.

| Page | What you can do |
|---|---|
| **Scans** | "Lancer un scan" now, see the log and the last runs |
| **Pays** | Turn countries on/off, set currency, delivery, returns, ad cost, local stores |
| **Mots-clés** | Tick the **niches** you want (Beauté, Cuisine, Mode…), add / remove search keywords per country |
| **Pages surveillées** | Add a competitor's page, remove a page that is not a shop |
| **Produits** | Merge two products that are the same, split ads that do not belong, type your supplier price, rename |
| **Réglages** | Scan hours, how many ads per scan, GitHub address, Telegram, default weights |

### Choosing a niche, and getting more data

- **Niches.** In **Mots-clés**, tick one or more niches. Each niche adds about 8 product searches
  in French and Arabic ("friteuse sans huile", "خلاط"…). Tick "Scanner uniquement les niches
  cochées" to drop the general keywords and scan only your niches. On the dashboard, the
  **Catégorie** filter uses the same names.
- **More data.** The library grows by itself: about 600 ads per scan, twice a day. To go faster,
  raise "Pubs à lire par scan" in **Réglages** (1 000–1 500 is reasonable; more means more risk
  that Facebook slows the scan down) and "Produits envoyés au tableau de bord" (300 by default).
- Searches take turns: with many keywords, each scan runs the ones searched longest ago.

### "Aussi sur Google": does the seller also pay for Google ads?

Gold Mine finds products on Facebook and Instagram only. Google has a public archive too (the Ads
Transparency Center), but it cannot be searched by keyword, so it cannot discover products. Gold
Mine uses it as a second opinion: after each scan it asks Google whether the websites of the
best-scored sellers also run Google ads.

- On the dashboard, such products carry the chip **Aussi sur Google**, the filter **Google Ads**
  keeps only them, and the details show how many Google ads the site has, with a link to see them.
- The answer is about the seller's whole website, not about that one product.
- Sellers without their own website (Converty or Shopify sub-addresses, Messenger, WhatsApp)
  cannot be checked.
- It is slow on purpose: 20 websites per scan, each one re-checked every 10 days. Change the
  number in **Réglages** ("Sites de vendeurs vérifiés sur Google Ads par scan"; 0 switches it off).

### The other tabs of the dashboard

- **Tendances** – products rising fastest this week.
- **Nouveaux vendeurs** – shop pages whose first ad is less than 14 days old.
- **Gagnants ailleurs** – products winning in another active country that nobody (or only one
  page) sells in the selected country yet. Turn on Morocco or Algeria to use it.

---

## Being gentle with Facebook

- One browser tab, random pauses (2–6 s between scrolls, 8–20 s between searches).
- A cap on ads per scan and per search (600 and 60 by default).
- If Facebook shows a login wall or a rate-limit message, the scan waits (90 s, then 3 min),
  and after the third time it stops for that run and keeps what it has. The next run continues
  with the keywords it did not reach.
- It never logs in and never touches anything private.

## Honest limits

- Money numbers are estimates. Use them to rank ideas, then test with a small budget.
- Facebook can change the Ad Library page. The reader is tolerant, but one day it may need a fix.
- Grouping is good, not perfect. Fix mistakes in two clicks in **Produits**.
- Sourcing: Tunisianet, Spacenet and SBS Informatique are electronics and appliance stores, so
  they are only looked up for products of those categories (never for shoes or creams). Mytek
  refuses automatic reading, so for Mytek, for Jumia (all categories) and for AliExpress, which
  is never scraped, you get a ready-made search link instead.
- Google ads: Google refuses quickly when asked too often (about 25 questions in three minutes
  were enough). When that happens the tool stops asking for 12 hours instead of insisting, so the
  Google marks fill in over several days.
- The free GitHub Pages site is public: anyone who has the link can open your dashboard (it asks
  search engines not to list it).

## Commands (optional)

```
.venv\Scripts\python -m goldmine run        full scan + publish
.venv\Scripts\python -m goldmine process    recalculate and export, no scan
.venv\Scripts\python -m goldmine panel      control panel
.venv\Scripts\python -m goldmine status     last runs
.venv\Scripts\python -m pytest tests        tests
```
