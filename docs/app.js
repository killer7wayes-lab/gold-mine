/* Gold Mine dashboard. Static: reads data/*.json, scores in the browser
 * (score.js) so every setting re-ranks the products instantly.
 * All text coming from ads is untrusted: it only reaches the page through esc(). */
(function () {
  'use strict';
  var LS_SETTINGS = 'gm.settings.v1', LS_PRESETS = 'gm.presets.v1', LS_COUNTRY = 'gm.country';
  var PAGE = 30;
  var WEIGHTS = [
    ['days', 'Ancienneté (jours en ligne)', 'Une pub qui tourne longtemps est une pub rentable.'],
    ['sellers', 'Nombre de vendeurs', 'Plusieurs vendeurs = produit validé. Trop = saturé.'],
    ['new7', 'Nouvelles pubs cette semaine', 'Les vendeurs relancent : ça scale maintenant.'],
    ['dups', 'Créatifs dupliqués', '« X pubs utilisent ce créatif » : le vendeur met du budget.'],
    ['trend', 'Tendance', 'En hausse ou en baisse par rapport à la semaine dernière.'],
    ['margin', 'Marge', 'Ce qui reste après fournisseur, livraison, pub et retours.']
  ];
  var TREND = { rising: '↗ En hausse', stable: '→ Stable', dying: '↘ En baisse' };
  var TIER = { low: 'faible', medium: 'moyen', high: 'élevé' };

  var state = { index: null, data: {}, country: '', tab: 'winners', settings: null,
                cat: '', trend: '', shown: PAGE, open: {} };
  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function safeUrl(u) { return /^https?:\/\//i.test(u || '') ? u : ''; }
  function link(url, label, cls) {
    url = safeUrl(url);
    return url ? '<a class="btn ' + (cls || '') + '" target="_blank" rel="noopener noreferrer" href="' +
      esc(url) + '">' + esc(label) + '</a>' : '';
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function load(key, fallback) {
    try { var v = JSON.parse(localStorage.getItem(key)); return v === null ? fallback : v; }
    catch (e) { return fallback; }
  }
  function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } }
  function getJSON(path) {
    return fetch(path + '?t=' + Date.now(), { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(path + ' : ' + r.status);
      return r.json();
    });
  }
  function money(v, cur) {
    if (v === null || v === undefined) return '?';
    var n = Math.abs(v) >= 20 ? Math.round(v) : Math.round(v * 10) / 10;
    return String(n).replace('.', ',') + ' ' + cur;
  }
  function dateFr(iso) {
    if (!iso) return '';
    var d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
    return isNaN(d) ? iso : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function countryInfo(code) {
    return state.index.countries.filter(function (c) { return c.code === code; })[0];
  }
  function adLibraryPage(code, pageId) {
    return 'https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=' +
      encodeURIComponent(code) + '&search_type=page&view_all_page_id=' + encodeURIComponent(pageId);
  }

  // ------------------------------------------------------------ settings
  function defaults() {
    var d = state.index.settings;
    return { weights: clone(d.weights), thresholds: clone(d.thresholds),
             est_supplier_ratio: d.est_supplier_ratio, costs: {} };
  }
  function loadSettings() {
    var s = load(LS_SETTINGS, null), d = defaults();
    if (!s || !s.weights || !s.thresholds) return d;
    Object.keys(d.weights).forEach(function (k) { if (typeof s.weights[k] !== 'number') s.weights[k] = d.weights[k]; });
    if (typeof s.est_supplier_ratio !== 'number') s.est_supplier_ratio = d.est_supplier_ratio;
    s.costs = s.costs || {};
    return s;
  }
  function saveSettings() { store(LS_SETTINGS, state.settings); }
  function scoreSettings() {             // what score.js expects
    var d = state.index.settings, s = state.settings;
    return { weights: s.weights, thresholds: s.thresholds, days_full: d.days_full,
             new7_full: d.new7_full, dups_full: d.dups_full,
             est_supplier_ratio: s.est_supplier_ratio, tier_multipliers: d.tier_multipliers };
  }
  function costsFor(code) {
    var c = countryInfo(code), over = state.settings.costs[code] || {};
    return { delivery_cost: over.delivery_cost !== undefined ? over.delivery_cost : c.costs.delivery_cost,
             return_rate: over.return_rate !== undefined ? over.return_rate : c.costs.return_rate,
             ad_cost: over.ad_cost !== undefined ? over.ad_cost : c.costs.ad_cost,
             margin_target: c.costs.margin_target, currency: c.currency };
  }
  function scored(code) {
    var data = state.data[code];
    if (!data) return [];
    var st = scoreSettings(), costs = costsFor(code);
    return data.products.map(function (p) { return { p: p, r: GMScore.score(p, st, costs), code: code }; });
  }

  // ------------------------------------------------------------ cards
  function sparkline(history) {
    if (!history || history.length < 2) return '';
    var vals = history.map(function (h) { return h[2]; });
    var max = Math.max.apply(null, vals.concat([1])), n = vals.length;
    var pts = vals.map(function (v, i) {
      return (i / (n - 1) * 100).toFixed(1) + ',' + (30 - v / max * 26).toFixed(1);
    }).join(' ');
    return '<div class="block"><h4>Pubs actives (' + n + ' derniers relevés)</h4>' +
      '<svg class="spark" viewBox="0 0 100 32" preserveAspectRatio="none"><polyline points="' + pts + '"/></svg></div>';
  }
  function moneyBlock(p, r, cur) {
    var m = r.money;
    if (m.margin === null) {
      return '<div class="block"><h4>Marge</h4><p class="small">Prix de vente non trouvé dans la pub ni sur la page de vente.</p></div>';
    }
    var sup = m.supplier_estimated
      ? 'Fournisseur (estimé à ' + Math.round(state.settings.est_supplier_ratio * 100) + ' % du prix)'
      : 'Fournisseur (prix saisi' + (p.supplier_note ? ' – ' + esc(p.supplier_note) : '') + ')';
    return '<div class="block"><h4>Calcul de la marge</h4><table class="money">' +
      '<tr><td>Prix de vente' + (p.price_min && p.price_min < p.price ? ' (le moins cher : ' + esc(money(p.price_min, cur)) + ')' : '') +
      '</td><td>' + esc(money(p.price, cur)) + '</td></tr>' +
      '<tr><td>' + sup + '</td><td>− ' + esc(money(m.supplier, cur)) + '</td></tr>' +
      '<tr><td>Livraison</td><td>− ' + esc(money(m.delivery, cur)) + '</td></tr>' +
      '<tr><td>Pub par vente (niveau ' + TIER[m.tier] + ')</td><td>− ' + esc(money(m.ad_cost, cur)) + '</td></tr>' +
      '<tr><td>Provision retours</td><td>− ' + esc(money(m.returns, cur)) + '</td></tr>' +
      '<tr class="total"><td>Marge par vente</td><td>' + (m.supplier_estimated ? '~' : '') + esc(money(m.margin, cur)) + '</td></tr>' +
      '</table><p class="small">Le coût pub est une estimation tirée de la concurrence et de la fatigue des créatifs, pas un chiffre réel.</p></div>';
  }
  function adsBlock(p, code) {
    var items = (p.ads || []).map(function (a) {
      return '<li><b>' + esc(a.page || 'Page') + '</b> · ' + (a.active ? 'active' : 'arrêtée') +
        (a.start ? ' · depuis le ' + esc(dateFr(a.start)) : '') +
        (a.copies > 1 ? ' · ' + a.copies + ' pubs avec ce créatif' : '') +
        (a.price ? ' · ' + esc(money(a.price, countryInfo(code).currency)) : '') +
        '<div class="links">' +
        link('https://www.facebook.com/ads/library/?id=' + encodeURIComponent(a.id), 'Voir la pub') +
        (a.page_id ? link(adLibraryPage(code, a.page_id), 'Toutes ses pubs') : '') +
        link(a.url, 'Page de vente') + '</div></li>';
    }).join('');
    return '<div class="block"><h4>Pubs (' + p.active_ads + ' actives sur ' + p.total_ads + ')</h4><ul class="plain">' + items + '</ul></div>';
  }
  function sourcingBlock(p, cur) {
    var s = p.sourcing || {}, out = '';
    (s.stores || []).forEach(function (st) {
      if (st.price !== undefined) {
        out += '<li><b>' + esc(st.store) + '</b> · ' + esc(money(st.price, cur)) + ' · ' + esc(st.title) +
          ' <span class="small">(ressemblance ' + st.match + ' %)</span><div class="links">' +
          link(st.url, 'Voir le produit') + link(st.search, 'Recherche') + '</div></li>';
      } else {
        out += '<li><b>' + esc(st.store) + '</b><div class="links">' + link(st.search, 'Chercher sur ' + st.store) + '</div></li>';
      }
    });
    var lens = '';
    if (p.img && location.protocol === 'https:') {
      lens = link('https://lens.google.com/uploadbyurl?url=' + encodeURIComponent(new URL(p.img, location.href).href),
                  'Chercher par image (Google Lens)');
    }
    return '<div class="block"><h4>Sourcing</h4><ul class="plain">' + out + '</ul><div class="links">' +
      link(s.aliexpress, 'Chercher sur AliExpress', 'gold') + lens + '</div></div>';
  }
  function card(item, extraChip) {
    var p = item.p, r = item.r, code = item.code, cur = countryInfo(code).currency;
    var cls = r.score >= 70 ? 'high' : (r.score >= 45 ? 'mid' : '');
    var isNew = p.first_seen && (Date.now() - new Date(p.first_seen + 'T12:00:00').getTime()) < 4 * 864e5;
    var m = r.money.margin, key = code + ':' + p.id;
    return '<article class="card"><div class="card-main"><div class="thumb">' +
      (p.img ? '<img loading="lazy" alt="" src="' + esc(p.img) + '">' : '<div class="noimg">📦</div>') +
      '<span class="score ' + cls + '" title="Score sur 100">' + r.score + '</span></div><div class="info">' +
      '<h3 dir="auto">' + esc(p.name) + '</h3><div class="chips">' + (extraChip || '') +
      '<span class="chip">' + esc(p.cat) + '</span>' +
      '<span class="chip ' + r.trend_label + '">' + TREND[r.trend_label] + '</span>' +
      (isNew ? '<span class="chip new">Nouveau</span>' : '') +
      (r.price_war ? '<span class="chip dying">Guerre des prix</span>' : '') + '</div>' +
      '<div class="kpis"><div><b>' + esc(money(p.price, cur)) + '</b><small>prix</small></div>' +
      '<div><b class="' + (m === null ? '' : (m < 0 ? 'neg' : 'pos')) + '">' +
      (m === null ? '?' : (r.money.supplier_estimated ? '~' : '') + esc(money(m, cur))) + '</b><small>marge</small></div>' +
      '<div><b>' + p.sellers + '</b><small>vendeur' + (p.sellers > 1 ? 's' : '') + '</small></div>' +
      '<div><b>' + p.days + ' j</b><small>en ligne</small></div></div></div></div>' +
      '<p class="reason" dir="auto">' + esc(r.reason) + '</p>' +
      '<details data-key="' + esc(key) + '"' + (state.open[key] ? ' open' : '') + '><summary>Détails, pubs et sourcing</summary>' +
      moneyBlock(p, r, cur) + sourcingBlock(p, cur) + adsBlock(p, code) + sparkline(p.history) +
      '</details></article>';
  }
  function grid(items, chipFn) {
    var shown = items.slice(0, state.shown);
    var html = '<div class="grid">' + shown.map(function (it) { return card(it, chipFn ? chipFn(it) : ''); }).join('') + '</div>';
    if (items.length > shown.length) {
      html += '<button class="btn more" id="more">Afficher plus (' + (items.length - shown.length) + ' restants)</button>';
    }
    return html;
  }
  function byFilters(it) {
    if (state.cat && it.p.cat !== state.cat) return false;
    if (state.trend && it.r.trend_label !== state.trend) return false;
    return true;
  }

  // ------------------------------------------------------------ tabs
  function viewWinners() {
    var all = scored(state.country);
    var items = all.filter(function (it) { return it.r.passes && byFilters(it); })
      .sort(function (a, b) { return b.r.score - a.r.score || b.p.days - a.p.days; });
    if (!all.length) return '<p class="empty">Aucun produit pour ce pays pour le moment. Lancez un scan depuis le panneau de contrôle.</p>';
    return '<p class="count">' + items.length + ' produit' + (items.length > 1 ? 's' : '') + ' sur ' + all.length +
      ' (classés par score, selon vos réglages)</p>' +
      (items.length ? grid(items) : '<p class="empty">Aucun produit ne passe vos filtres. Baissez le score ou la marge minimum.</p>');
  }
  function viewTrends() {
    var all = scored(state.country);
    var items = all.filter(function (it) {
      return it.p.active && it.r.trend_label === 'rising' && (!state.cat || it.p.cat === state.cat);
    }).sort(function (a, b) { return b.p.trend - a.p.trend || b.p.new7 - a.p.new7 || b.r.score - a.r.score; });
    var estimated = all.some(function (it) { return it.p.trend_source === 'estimate'; });
    return (estimated ? '<p class="note">Tant que l’historique a moins de 4 jours, la tendance est estimée à partir des dates de lancement des pubs. Elle devient plus fiable à chaque scan.</p>' : '') +
      '<p class="count">' + items.length + ' produit' + (items.length > 1 ? 's' : '') + ' en hausse cette semaine</p>' +
      (items.length ? grid(items, function (it) {
        return it.p.new7 ? '<span class="chip rising">+' + it.p.new7 + ' pub' + (it.p.new7 > 1 ? 's' : '') + ' cette semaine</span>' : '';
      }) : '<p class="empty">Rien en hausse pour le moment.</p>');
  }
  function viewSellers() {
    var data = state.data[state.country], list = (data && data.new_sellers) || [];
    if (!list.length) return '<p class="empty">Aucun nouveau vendeur détecté ces 14 derniers jours.</p>';
    return '<p class="count">' + list.length + ' page' + (list.length > 1 ? 's' : '') +
      ' e-commerce dont la première pub a moins de 14 jours</p><div class="grid">' + list.map(function (s) {
        return '<article class="card"><h3 dir="auto" style="margin:0 0 6px">' + esc(s.name) + '</h3>' +
          '<div class="chips"><span class="chip new">1re pub le ' + esc(dateFr(s.first_ad)) + '</span>' +
          '<span class="chip">' + s.active_ads + ' pub' + (s.active_ads > 1 ? 's' : '') + ' active' + (s.active_ads > 1 ? 's' : '') + '</span>' +
          (s.likes ? '<span class="chip">' + s.likes.toLocaleString('fr-FR') + ' abonnés</span>' : '') + '</div>' +
          (s.products.length ? '<ul class="plain">' + s.products.map(function (p) {
            return '<li dir="auto">' + esc(p.name) + '</li>'; }).join('') + '</ul>' : '') +
          '<div class="links">' + link(adLibraryPage(state.country, s.page_id), 'Voir ses pubs', 'gold') + '</div></article>';
      }).join('') + '</div>';
  }
  function viewElsewhere() {
    var others = state.index.countries.filter(function (c) { return c.code !== state.country; });
    var here = countryInfo(state.country);
    if (!others.length) {
      return '<p class="empty">Un seul pays est actif. Activez un autre pays (Maroc, Algérie…) dans le panneau de contrôle pour repérer les produits qui gagnent ailleurs avant qu’ils n’arrivent en ' + esc(here.name) + '.</p>';
    }
    var missing = others.filter(function (c) { return !state.data[c.code]; });
    if (missing.length) {
      Promise.all(missing.map(function (c) { return loadCountry(c.code); })).then(render, render);
      return '<p class="empty">Chargement des autres pays…</p>';
    }
    var local = {};
    (state.data[state.country].products || []).forEach(function (p) {
      if (p.active) local[p.gid] = Math.max(local[p.gid] || 0, p.sellers);
    });
    var floor = Math.max(state.settings.thresholds.min_score || 0, 50), items = [];
    others.forEach(function (c) {
      scored(c.code).forEach(function (it) {
        var sellersHere = local[it.p.gid] || 0;
        if (it.p.active && it.r.score >= floor && sellersHere <= 1 && (!state.cat || it.p.cat === state.cat)) {
          it.sellersHere = sellersHere; items.push(it);
        }
      });
    });
    items.sort(function (a, b) { return b.r.score - a.r.score; });
    return '<p class="note">Produits qui gagnent dans un autre pays actif (score ≥ ' + floor + ') et pas encore vendus, ou par un seul vendeur, en ' +
      esc(here.name) + '. Prix et marges dans la monnaie du pays d’origine.</p>' +
      (items.length ? grid(items, function (it) {
        var c = countryInfo(it.code);
        return '<span class="chip flag">' + esc(c.flag + ' ' + c.name) + '</span><span class="chip new">' +
          (it.sellersHere ? '1 seul vendeur ici' : 'Pas encore vendu ici') + '</span>';
      }) : '<p class="empty">Aucune opportunité détectée pour le moment.</p>');
  }

  // ------------------------------------------------------------ settings tab
  function slider(id, label, hint, value, min, max, step, unit) {
    return '<div class="row"><label for="' + id + '">' + esc(label) + '</label><output id="' + id + '-out">' +
      esc(value + (unit || '')) + '</output><input type="range" id="' + id + '" min="' + min + '" max="' + max +
      '" step="' + step + '" value="' + value + '" data-unit="' + esc(unit || '') + '">' +
      (hint ? '<span class="hint">' + esc(hint) + '</span>' : '') + '</div>';
  }
  function numberRow(id, label, hint, value, placeholder) {
    return '<div class="row"><label for="' + id + '">' + esc(label) + '</label><input type="number" inputmode="decimal" id="' + id +
      '" value="' + (value === null || value === undefined ? '' : value) + '" placeholder="' + esc(placeholder || '') + '">' +
      (hint ? '<span class="hint">' + esc(hint) + '</span>' : '') + '</div>';
  }
  function userPresets() { return load(LS_PRESETS, {}); }
  function viewSettings() {
    var s = state.settings, c = costsFor(state.country), info = countryInfo(state.country), th = s.thresholds;
    var total = WEIGHTS.reduce(function (a, w) { return a + (s.weights[w[0]] || 0); }, 0) || 1;
    var user = userPresets();
    var html = '<div class="panel"><h2>Préréglages</h2><p class="help">Un clic applique un profil complet. Vos propres préréglages restent dans ce navigateur.</p><div class="presets">' +
      '<button class="btn" data-reset="1">Valeurs par défaut</button>' +
      Object.keys(state.index.presets || {}).map(function (n) {
        return '<button class="btn" data-preset="' + esc(n) + '">' + esc(n) + '</button>'; }).join('') +
      Object.keys(user).map(function (n) {
        return '<button class="btn gold" data-upreset="' + esc(n) + '">' + esc(n) + '</button>' +
          '<button class="btn danger" data-delpreset="' + esc(n) + '" aria-label="Supprimer ' + esc(n) + '">✕</button>'; }).join('') +
      '</div><div class="save-row"><input type="text" id="preset-name" maxlength="30" placeholder="Nom du préréglage">' +
      '<button class="btn gold" id="preset-save">Enregistrer</button></div></div>';

    html += '<div class="settings-cols"><div><div class="panel"><h2>Poids du score</h2><p class="help">Plus un curseur est haut, plus ce signal compte dans la note sur 100.</p>' +
      WEIGHTS.map(function (w) {
        return slider('w-' + w[0], w[1] + ' · ' + Math.round((s.weights[w[0]] || 0) / total * 100) + ' %', w[2], s.weights[w[0]] || 0, 0, 50, 1, '');
      }).join('') + '</div></div><div>';

    html += '<div class="panel"><h2>Seuils</h2><p class="help">Les produits hors seuils sont masqués.</p>' +
      slider('t-min_score', 'Score minimum', '', th.min_score || 0, 0, 100, 5, '') +
      numberRow('t-min_margin', 'Marge minimum (' + info.currency + ')', 'Vide = pas de minimum.', th.min_margin, 'aucune') +
      slider('t-max_sellers', 'Vendeurs maximum (saturation)', 'Au-delà, le produit est considéré saturé et masqué.', th.max_sellers || 8, 1, 30, 1, '') +
      slider('t-min_days', 'Jours en ligne minimum', '', th.min_days || 0, 0, 90, 1, ' j') + '</div>';

    html += '<div class="panel"><h2>Coûts – ' + esc(info.flag + ' ' + info.name) + '</h2><p class="help">Utilisés pour estimer la marge par vente.</p>' +
      numberRow('c-delivery_cost', 'Livraison (' + info.currency + ')', 'Ce que la livraison vous coûte par colis.', c.delivery_cost) +
      slider('c-return_rate', 'Taux de retour', 'Part des colis refusés ou non livrés.', Math.round(c.return_rate * 100), 0, 60, 1, ' %') +
      numberRow('c-ad_cost', 'Coût pub par vente (' + info.currency + ')', 'Niveau moyen. Ajusté ×0,7 (faible concurrence) à ×1,5 (forte).', c.ad_cost) +
      slider('s-est_supplier_ratio', 'Prix fournisseur estimé', 'En % du prix de vente, quand vous n’avez pas saisi le prix réel.', Math.round(s.est_supplier_ratio * 100), 5, 80, 1, ' %') +
      '</div></div></div>';

    html += '<div class="panel"><h2>Aperçu en direct</h2><p class="help">Les 8 premiers produits avec ces réglages.</p><ul class="plain preview" id="preview"></ul></div>';
    return html;
  }
  function renderPreview() {
    var el = $('#preview');
    if (!el) return;
    var cur = countryInfo(state.country).currency;
    var items = scored(state.country).filter(function (it) { return it.r.passes; })
      .sort(function (a, b) { return b.r.score - a.r.score; }).slice(0, 8);
    el.innerHTML = items.length ? items.map(function (it) {
      return '<li dir="auto"><b>' + it.r.score + '</b> ' + esc(it.p.name) + '<br><span class="small">' + esc(it.r.reason) +
        (it.p.price ? ' · ' + esc(money(it.p.price, cur)) : '') + '</span></li>';
    }).join('') : '<li class="small">Aucun produit ne passe ces seuils.</li>';
  }
  function onSettingInput(e) {
    var el = e.target, id = el.id || '', s = state.settings, v = el.value === '' ? null : parseFloat(el.value);
    var kind = id.slice(0, 2), key = id.slice(2);
    if (kind === 'w-') s.weights[key] = v || 0;
    else if (kind === 't-') s.thresholds[key] = (key === 'min_margin') ? v : (v || 0);
    else if (kind === 'c-') {
      var over = s.costs[state.country] = s.costs[state.country] || {};
      if (v === null) delete over[key]; else over[key] = key === 'return_rate' ? v / 100 : v;
    } else if (id === 's-est_supplier_ratio') s.est_supplier_ratio = (v || 0) / 100;
    else return;
    var out = $('#' + id + '-out');
    if (out) out.textContent = el.value + (el.dataset.unit || '');
    if (kind === 'w-') {                       // refresh the "· 25 %" shares
      var total = WEIGHTS.reduce(function (a, w) { return a + (s.weights[w[0]] || 0); }, 0) || 1;
      WEIGHTS.forEach(function (w) {
        var lab = $('label[for="w-' + w[0] + '"]');
        if (lab) lab.textContent = w[1] + ' · ' + Math.round((s.weights[w[0]] || 0) / total * 100) + ' %';
      });
    }
    saveSettings(); syncFilters(); renderPreview();
  }
  function applyPreset(p) {
    var d = defaults();
    state.settings.weights = clone(p.weights || d.weights);
    state.settings.thresholds = clone(p.thresholds || d.thresholds);
    if (typeof p.est_supplier_ratio === 'number') state.settings.est_supplier_ratio = p.est_supplier_ratio;
    if (p.costs) state.settings.costs = clone(p.costs);
    saveSettings(); syncFilters(); render();
  }
  function onSettingClick(e) {
    var t = e.target, d = t.dataset || {};
    if (d.reset) { state.settings = defaults(); saveSettings(); syncFilters(); render(); }
    else if (d.preset) applyPreset(state.index.presets[d.preset]);
    else if (d.upreset) applyPreset(userPresets()[d.upreset] || {});
    else if (d.delpreset) { var u = userPresets(); delete u[d.delpreset]; store(LS_PRESETS, u); render(); }
    else if (t.id === 'preset-save') {
      var name = ($('#preset-name').value || '').trim();
      if (!name) { $('#preset-name').focus(); return; }
      var all = userPresets(); all[name] = clone(state.settings); store(LS_PRESETS, all); render();
    }
  }

  // ------------------------------------------------------------ render
  function syncFilters() {
    var th = state.settings.thresholds;
    $('#f-score').value = th.min_score ? th.min_score : '';
    $('#f-margin').value = (th.min_margin === null || th.min_margin === undefined) ? '' : th.min_margin;
    $('.filters .cur').textContent = '(' + countryInfo(state.country).currency + ')';
  }
  function fillCategories() {
    var cats = {};
    ((state.data[state.country] || {}).products || []).forEach(function (p) { cats[p.cat] = (cats[p.cat] || 0) + 1; });
    var sel = $('#f-cat');
    sel.innerHTML = '<option value="">Toutes</option>' + Object.keys(cats).sort(function (a, b) { return a.localeCompare(b, 'fr'); })
      .map(function (c) { return '<option value="' + esc(c) + '">' + esc(c) + ' (' + cats[c] + ')</option>'; }).join('');
    if (!cats[state.cat]) state.cat = '';
    sel.value = state.cat;
  }
  function render() {
    var view = $('#view'), tab = state.tab;
    Array.prototype.forEach.call(document.querySelectorAll('#tabs button'), function (b) {
      b.classList.toggle('on', b.dataset.tab === tab);
    });
    var filters = $('#filters');
    filters.hidden = (tab === 'sellers' || tab === 'settings');
    $('#f-trend').parentNode.style.display = tab === 'winners' ? '' : 'none';
    $('#f-score').parentNode.style.display = tab === 'trends' ? 'none' : '';
    $('#f-margin').parentNode.style.display = tab === 'winners' ? '' : 'none';
    if (!state.data[state.country]) { view.innerHTML = '<p class="empty">Chargement…</p>'; return; }
    view.innerHTML = tab === 'winners' ? viewWinners() : tab === 'trends' ? viewTrends()
      : tab === 'sellers' ? viewSellers() : tab === 'elsewhere' ? viewElsewhere() : viewSettings();
    if (tab === 'settings') renderPreview();
  }
  function loadCountry(code) {
    if (state.data[code]) return Promise.resolve(state.data[code]);
    return getJSON('data/' + encodeURIComponent(code) + '.json').then(function (d) { state.data[code] = d; return d; });
  }
  function setCountry(code) {
    state.country = code; state.shown = PAGE; store(LS_COUNTRY, code);
    $('#country').value = code;
    render();
    loadCountry(code).then(function (d) {
      var when = new Date(d.generated_at || state.index.generated_at);
      $('#updated').textContent = 'Dernière mise à jour : ' + when.toLocaleString('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }) +
        ' · ' + d.total_products + ' produits suivis';
      fillCategories(); syncFilters(); render();
    }).catch(function (err) {
      $('#view').innerHTML = '<p class="empty">Données introuvables pour ce pays (' + esc(err.message) + ').</p>';
    });
  }

  function init() {
    getJSON('data/index.json').then(function (index) {
      state.index = index;
      if (!index.countries.length) {
        $('#updated').textContent = '';
        $('#view').innerHTML = '<p class="empty">Aucune donnée pour le moment. Lancez un premier scan depuis le panneau de contrôle.</p>';
        return;
      }
      state.settings = loadSettings();
      $('#country').innerHTML = index.countries.map(function (c) {
        return '<option value="' + esc(c.code) + '">' + esc(c.flag + ' ' + c.name) + '</option>'; }).join('');
      var saved = load(LS_COUNTRY, ''), codes = index.countries.map(function (c) { return c.code; });
      setCountry(codes.indexOf(saved) >= 0 ? saved : (index.default_country || codes[0]));
    }).catch(function () {
      $('#updated').textContent = '';
      $('#view').innerHTML = '<p class="empty">Aucune donnée pour le moment. Lancez un premier scan depuis le panneau de contrôle.</p>';
    });

    $('#country').addEventListener('change', function (e) { setCountry(e.target.value); });
    $('#tabs').addEventListener('click', function (e) {
      var tab = e.target.dataset && e.target.dataset.tab;
      if (!tab || !state.index) return;
      state.tab = tab; state.shown = PAGE; render(); window.scrollTo(0, 0);
    });
    $('#filters').addEventListener('submit', function (e) { e.preventDefault(); });
    $('#filters').addEventListener('input', function (e) {
      if (!state.settings) return;
      var id = e.target.id, v = e.target.value;
      if (id === 'f-cat') state.cat = v;
      else if (id === 'f-trend') state.trend = v;
      else if (id === 'f-score') state.settings.thresholds.min_score = parseFloat(v) || 0;
      else if (id === 'f-margin') state.settings.thresholds.min_margin = v === '' ? null : parseFloat(v);
      state.shown = PAGE; saveSettings(); render();
    });
    $('#view').addEventListener('input', function (e) { if (state.tab === 'settings') onSettingInput(e); });
    $('#view').addEventListener('click', function (e) {
      if (e.target.id === 'more') { state.shown += PAGE; render(); return; }
      if (state.tab === 'settings') onSettingClick(e);
    });
    $('#view').addEventListener('toggle', function (e) {
      var key = e.target.dataset && e.target.dataset.key;
      if (key) { if (e.target.open) state.open[key] = 1; else delete state.open[key]; }
    }, true);
  }
  init();
})();
