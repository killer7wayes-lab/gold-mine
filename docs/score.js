/* Score /100 + money estimate + French reason.
 *
 * THIS FILE AND goldmine/scoring.py MUST STAY IDENTICAL IN LOGIC.
 * tests/test_parity.py runs both on the same products.
 *
 *   s         raw signals of one product (exported by Python)
 *   settings  { weights, thresholds, days_full, new7_full, dups_full,
 *               est_supplier_ratio, tier_multipliers }
 *   costs     { delivery_cost, return_rate, ad_cost, margin_target, currency }
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.GMScore = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var TIERS = ['low', 'medium', 'high'];

  function clamp(x, lo, hi) {
    if (lo === undefined) lo = 0;
    if (hi === undefined) hi = 1;
    return Math.max(lo, Math.min(hi, x));
  }
  function num(x) { var v = parseFloat(x); return isNaN(v) ? 0 : v; }

  function costTier(s, settings) {
    var maxSellers = settings.thresholds.max_sellers || 8;
    var level = s.sellers <= 2 ? 0 : (s.sellers <= maxSellers ? 1 : 2);
    if (s.days > 60 && s.new7 === 0) level += 1;
    if (s.days < 14 && s.sellers <= 3) level -= 1;
    return TIERS[clamp(level, 0, 2)];
  }

  function money(s, settings, costs) {
    var tier = costTier(s, settings);
    var adCost = costs.ad_cost * settings.tier_multipliers[tier];
    var r = clamp(costs.return_rate, 0, 0.9);
    var returns = r / (1 - r) * (costs.delivery_cost + adCost);
    var out = { tier: tier, ad_cost: adCost, returns: returns, delivery: costs.delivery_cost,
                supplier: null, supplier_estimated: true, margin: null };
    var price = s.price;
    if (price === null || price === undefined) return out;
    if (s.supplier !== null && s.supplier !== undefined) {
      out.supplier = s.supplier; out.supplier_estimated = false;
    } else {
      out.supplier = price * settings.est_supplier_ratio;
    }
    out.margin = price - out.supplier - costs.delivery_cost - adCost - returns;
    return out;
  }

  function sellersCurve(n, maxSellers) {
    if (n <= 0) return 0;
    if (n === 1) return 0.4;
    if (n === 2) return 0.7;
    var sweetEnd = Math.max(3, pyRound(maxSellers * 0.6));
    if (n <= sweetEnd) return 1;
    return Math.max(0.15, 1 - 0.6 * (n - sweetEnd) / Math.max(maxSellers - sweetEnd, 1));
  }
  // Python's round(): halves go to the even number
  function pyRound(x) {
    var f = Math.floor(x);
    var d = x - f;
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }

  function trendLabel(s) {
    if (!s.active || s.trend <= -0.15) return 'dying';
    return s.trend >= 0.15 ? 'rising' : 'stable';
  }

  function priceWar(s) {
    if ((s.price_drop || 0) >= 0.15) return true;
    return !!(s.sellers >= 3 && s.price && s.price_min && s.price_min <= s.price * 0.65);
  }

  function fmt(v) { return String(Math.floor(v + 0.5)); }
  function plural(n, one, many) { return n + ' ' + (n <= 1 ? one : many); }

  function reason(s, m, costs) {
    var parts = [];
    if (s.active) parts.push('Actif depuis ' + plural(s.days, 'jour', 'jours'));
    else parts.push('Plus aucune pub active');
    parts.push(plural(s.sellers, 'vendeur', 'vendeurs'));
    if (s.new7 > 0) parts.push(plural(s.new7, 'nouvelle pub', 'nouvelles pubs') + ' cette semaine');
    if (s.dups >= 3) parts.push('créatif dupliqué ' + s.dups + '×');
    var label = trendLabel(s);
    if (label === 'rising') parts.push('en hausse');
    else if (label === 'dying' && s.active) parts.push('en baisse');
    if (priceWar(s)) parts.push('guerre des prix');
    if (m.margin === null) parts.push('prix inconnu');
    else parts.push('marge ' + (m.supplier_estimated ? '~' : '') + fmt(m.margin) + ' ' + costs.currency);
    return parts.join(', ');
  }

  function score(s, settings, costs) {
    var w = settings.weights, th = settings.thresholds;
    var maxSellers = th.max_sellers || 8;
    var m = money(s, settings, costs);
    var parts = {
      days: clamp(s.days / Math.max(settings.days_full, 1)),
      sellers: sellersCurve(s.sellers, maxSellers),
      new7: clamp(s.new7 / Math.max(settings.new7_full, 1)),
      dups: clamp(Math.log(Math.max(s.dups, 1)) / Math.log(Math.max(settings.dups_full, 2))),
      trend: clamp((s.trend + 1) / 2),
      margin: m.margin === null ? 0 : clamp(m.margin / Math.max(costs.margin_target, 1))
    };
    var total = 0, sum = 0, k;
    for (k in parts) {
      var wk = Math.max(num(w[k]), 0);
      total += wk; sum += wk * parts[k];
    }
    var value = total > 0 ? 100 * sum / total : 0;
    var war = priceWar(s);
    if (!s.active) value *= 0.35;
    if (war) value -= 8;
    if (s.sellers_stopped > s.sellers && s.sellers + s.sellers_stopped >= 3) value -= 8;
    value = Math.floor(clamp(value, 0, 100) + 0.5);

    var passes = value >= (th.min_score || 0);
    if (th.min_margin !== null && th.min_margin !== undefined) {
      passes = passes && m.margin !== null && m.margin >= th.min_margin;
    }
    if (th.max_sellers) passes = passes && s.sellers <= th.max_sellers;
    passes = passes && s.days >= (th.min_days || 0);
    return { score: value, parts: parts, money: m, tier: m.tier, trend_label: trendLabel(s),
             price_war: war, reason: reason(s, m, costs), passes: !!passes };
  }

  return { score: score, money: money, costTier: costTier, sellersCurve: sellersCurve,
           trendLabel: trendLabel, priceWar: priceWar };
});
