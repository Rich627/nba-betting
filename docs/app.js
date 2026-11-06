/* NBA Master 預測 — static front-end over data/site.json (built by pipeline/build_site_json.py).
   Bilingual: 繁中 / English. The choice comes from ?lang=, then localStorage, then the
   browser language (set in index.html before paint); the EN/中 button switches it. */
(function () {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtPnl = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Math.round(v));
  const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
  const cls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
  const COLORS = { GPT: '#10a37f', Claude: '#d97757', Gemini: '#4285f4', DeepSeek: '#4d6bfe', Kimi: '#8b5cf6', Grok: '#9ca3af', GLM: '#e11d74' };

  const STR = {
    zh: {
      title: 'NBA Master 預測 2026-27',
      banner: '⚠️ 純娛樂，別跟著下注 — 以下全部是 AI 模型的玩票預測與紙上錢包，不是投注建議',
      brand: 'NBA Master 預測', navResults: '賽果', navWallet: '模型錢包',
      langBtn: 'EN', langAria: 'Switch to English', themeAria: '切換深淺色', statsAria: '摘要', close: '關閉',
      walletTitle: '模型錢包',
      walletNote: '每個模型每場獨贏押 100，照當時 9 家書商中位數賠率結算。曲線往下就是沒 edge。',
      upcomingTitle: '賽前預測', upcomingNote: '亞利桑那時間（MST）。沒跑模型的場次只列賽程。',
      resultsTitle: '最近賽果', resultsNote: '模型命中 = 獨贏方向猜對；建議欄是 edge 引擎真的喊過的注。',
      footer: 'NBA Master 預測 2026-27 · 七個 AI 模型的玩票預測', footerWarn: '純娛樂，別跟著下注',
      noOdds: '無盤口', ml: '獨贏', spread: '讓分', total: '大小',
      noModels: '尚未跑模型',
      consensus: (fav, prob, n, agree, away, pa, ph, home) =>
        `共識 <b>${fav}</b> 勝率 <b>${prob}</b>（${n} 模型 · 一致 ${agree}）· 預測 ${away} ${pa}-${ph} ${home}`,
      noEv: '無 +EV，不買', hits: (h) => `模型命中 ${h}`, live: '進行中', away: '客', home: '主',
      sPredicted: '已預測場次', sRecs: '喊過的建議', sRecRate: '建議命中', sRecPnl: '建議損益',
      lbHits: (w, b) => `${w}/${b} 命中`, noWallet: '還沒有結算過的場次',
      noUpcoming: '目前沒有排定的比賽', noResults: '還沒有賽果',
      noChart: '錢包曲線會在第一天結算後出現', noModelRun: '這場沒跑模型',
      conf: { high: '高', med: '中', low: '低' },
      updated: '更新 ', loadFail: '資料載入失敗：',
    },
    en: {
      title: 'NBA Master Picks 2026-27',
      banner: '⚠️ For fun only — everything below is AI model guesswork and paper wallets, not betting advice',
      brand: 'NBA Master Picks', navResults: 'Results', navWallet: 'Wallets',
      langBtn: '中', langAria: '切換成中文', themeAria: 'Toggle light/dark', statsAria: 'Summary', close: 'Close',
      walletTitle: 'Model wallets',
      walletNote: 'Each model stakes 100 on its moneyline pick every game, settled at the median price of 9 books at the time. A falling line means no edge.',
      upcomingTitle: 'Upcoming picks', upcomingNote: 'Arizona time (MST). Games the models have not run yet show the schedule only.',
      resultsTitle: 'Recent results', resultsNote: 'A model hit = right moneyline side. Bets are the ones the edge engine actually called.',
      footer: 'NBA Master Picks 2026-27 · seven AI models, just for fun', footerWarn: 'For fun only — do not bet on this',
      noOdds: 'No odds', ml: 'ML', spread: 'Spread', total: 'Total',
      noModels: 'Models not run yet',
      consensus: (fav, prob, n, agree, away, pa, ph, home) =>
        `Consensus <b>${fav}</b> <b>${prob}</b> to win (${n} models · ${agree} agree) · Projected ${away} ${pa}-${ph} ${home}`,
      noEv: 'No +EV, no bet', hits: (h) => `Models right ${h}`, live: 'Live', away: 'away', home: 'home',
      sPredicted: 'Games predicted', sRecs: 'Bets called', sRecRate: 'Bets won', sRecPnl: 'Bet P&L',
      lbHits: (w, b) => `${w}/${b} right`, noWallet: 'No settled games yet',
      noUpcoming: 'No games scheduled right now', noResults: 'No results yet',
      noChart: 'The wallet chart appears after the first day is settled', noModelRun: 'Models did not run for this game',
      conf: { high: 'high', med: 'med', low: 'low' },
      updated: 'Updated ', loadFail: 'Could not load data: ',
    },
  };

  let LANG = document.documentElement.dataset.lang === 'en' ? 'en' : 'zh';
  const t = (k) => STR[LANG][k];
  let DATA = null;

  // Language-aware field pickers. Older site.json files have no *_en / reasoning_zh
  // fields, so each one falls back to what is there.
  const team = (g, side) => (LANG === 'en' ? g[side + '_en'] || g[side] : g[side + '_zh'] || g[side]);
  const nameOf = (g, abbr) => (abbr === g.home ? team(g, 'home') : abbr === g.away ? team(g, 'away') : abbr);
  const why = (m) => (LANG === 'zh' ? m.reasoning_zh || m.reasoning : m.reasoning || m.reasoning_zh) || '';
  const recLabel = (r) => (LANG === 'en' ? r.label_en || r.label : r.label);

  const WEEKDAY_ZH = '日一二三四五六';
  function when(g) {
    if (!g.commence_time) return g.tw || '';
    const tz = (DATA && DATA.timezone) || 'America/Phoenix';
    const d = new Date(g.commence_time);
    try {
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
        timeZone: tz, month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      }).formatToParts(d).map((p) => [p.type, p.value]));
      const hm = `${parts.hour}:${parts.minute}`;
      if (LANG === 'en') return `${parts.weekday} ${parts.month}/${parts.day} ${hm}`;
      const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday);
      return `${parts.month}/${parts.day}(${WEEKDAY_ZH[wd] || ''}) ${hm}`;
    } catch (_) { return g.tw || ''; }
  }

  function applyStatic() {
    document.documentElement.dataset.lang = LANG;
    document.documentElement.lang = LANG === 'en' ? 'en' : 'zh-Hant';
    document.title = t('title');
    document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll('[data-i18n-aria]').forEach((el) => el.setAttribute('aria-label', t(el.dataset.i18nAria)));
    $('#lang-toggle').textContent = t('langBtn');
  }

  // theme
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('nba-theme', next); } catch (_) {}
    if (DATA) drawChart(DATA);
  });

  // language
  let openId = null;
  $('#lang-toggle').addEventListener('click', () => {
    LANG = LANG === 'en' ? 'zh' : 'en';
    try { localStorage.setItem('nba-lang', LANG); } catch (_) {}
    try {
      const u = new URL(location.href);
      if (u.searchParams.has('lang')) { u.searchParams.set('lang', LANG); history.replaceState(null, '', u); }
    } catch (_) {}
    applyStatic();
    if (DATA) {
      render(DATA);
      if (openId) openDetail(openId);
    }
  });

  function oddsLine(g) {
    const o = g.odds;
    if (!o || !o.ml_home) return `<div class="line muted">${t('noOdds')}</div>`;
    const parts = [`${t('ml')} ${esc(team(g, 'home'))} <b>${o.ml_home.toFixed(2)}</b> / ${esc(team(g, 'away'))} <b>${o.ml_away.toFixed(2)}</b>`];
    if (o.spread_line != null) parts.push(`${t('spread')} ${esc(team(g, 'home'))} <b>${o.spread_line > 0 ? '+' : ''}${o.spread_line}</b>`);
    if (o.total_line != null) parts.push(`${t('total')} <b>${o.total_line}</b>`);
    return `<div class="line">${parts.join(' · ')}</div>`;
  }

  function consensusLine(g) {
    const c = g.consensus;
    if (!c) return `<div class="line muted">${t('noModels')}</div>`;
    const ph = Math.round((c.pred_total + c.pred_margin) / 2), pa = Math.round((c.pred_total - c.pred_margin) / 2);
    const fav = esc(c.fav ? nameOf(g, c.fav) : c.fav_zh);
    return `<div class="line">${t('consensus')(fav, pct(c.fav_prob), c.n_models, pct(c.agreement), esc(team(g, 'away')), pa, ph, esc(team(g, 'home')))}</div>`;
  }

  function recChips(g) {
    if (!g.recs || !g.recs.length) return g.consensus ? `<span class="rec push">${t('noEv')}</span>` : '';
    return g.recs.map((r) => {
      const k = r.result === 'WON' ? 'won' : r.result === 'LOST' ? 'lost' : r.result === 'PUSH' ? 'push' : '';
      const mark = r.result === 'WON' ? '✅ ' : r.result === 'LOST' ? '❌ ' : '👉 ';
      const odds = r.odds ? ` @${r.odds.toFixed(2)}` : '';
      const edge = r.edge != null ? (LANG === 'en' ? ` (EV ${r.edge > 0 ? '+' : ''}${Math.round(r.edge * 100)}%)` : `（EV ${r.edge > 0 ? '+' : ''}${Math.round(r.edge * 100)}%）`) : '';
      const pnl = r.pnl != null ? ` ${fmtPnl(r.pnl)}` : '';
      return `<span class="rec ${k}">${mark}${esc(recLabel(r))}${odds}${edge}${pnl}</span>`;
    }).join('');
  }

  function pickProb(g, m) {
    return m.home_win_prob == null ? null : (m.pick === g.home ? m.home_win_prob : 1 - m.home_win_prob);
  }

  function modelDots(g) {
    if (!g.models || !g.models.length) return '';
    return '<div class="dots">' + g.models.map((m) => {
      const k = m.result === 'WON' ? 'won' : m.result === 'LOST' ? 'lost' : '';
      const pick = esc(nameOf(g, m.pick));
      return `<span class="mdot ${k}" title="${esc(m.name)} → ${pick} ${pct(pickProb(g, m))}">${esc(m.name)} ${pick}</span>`;
    }).join('') + '</div>';
  }

  function gameCard(g, final) {
    const score = final
      ? `<span class="score">${g.away_score}</span><span class="at">–</span><span class="score">${g.home_score}</span>`
      : '<span class="at">@</span>';
    const hits = final && g.hits ? `<span>${t('hits')(g.hits)}</span>` : '';
    return `<article class="card game" data-id="${esc(g.game_id)}" tabindex="0">
      <div class="when"><span>${esc(when(g))}</span>${hits || `<span>${g.status === 'live' ? t('live') : ''}</span>`}</div>
      <div class="teams">
        <div><span class="abbr">${esc(g.away)} ${t('away')}</span>${esc(team(g, 'away'))}</div>
        <div style="display:flex;gap:8px;align-items:center">${score}</div>
        <div style="text-align:right"><span class="abbr">${esc(g.home)} ${t('home')}</span>${esc(team(g, 'home'))}</div>
      </div>
      ${final ? '' : consensusLine(g)}${g.status === 'live' && !g.consensus ? '' : oddsLine(g)}
      <div>${recChips(g)}</div>
      ${modelDots(g)}
    </article>`;
  }

  function render(d) {
    DATA = d;
    $('#generated').textContent = t('updated') + d.generated_at.replace('T', ' ').slice(0, 16);
    const s = d.summary;
    const recRate = s.recs_graded ? `${s.recs_won}/${s.recs_graded}` : '—';
    $('#stats').innerHTML = [
      [t('sPredicted'), s.n_predicted_games],
      [t('sRecs'), s.n_recs],
      [t('sRecRate'), recRate],
      [t('sRecPnl'), `<span class="${cls(s.recs_pnl)}">${fmtPnl(s.recs_pnl)}</span>`],
    ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

    $('#leaderboard').innerHTML = d.wallet.length
      ? d.wallet.map((w) => `<div class="lb"><div><div class="name">${esc(w.model)}</div><div class="rec">${t('lbHits')(w.wins, w.bets)}</div></div><div class="v ${cls(w.pnl)}" style="font-family:'Space Grotesk',monospace;font-weight:700">${fmtPnl(w.pnl)}</div></div>`).join('')
      : `<div class="empty">${t('noWallet')}</div>`;

    $('#upcoming-list').innerHTML = d.upcoming.length ? d.upcoming.map((g) => gameCard(g, false)).join('') : `<div class="empty">${t('noUpcoming')}</div>`;
    $('#results-list').innerHTML = d.results.length ? d.results.map((g) => gameCard(g, true)).join('') : `<div class="empty">${t('noResults')}</div>`;
    drawChart(d);

    document.querySelectorAll('.game').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.dataset.id));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.dataset.id); } });
    });
  }

  let chart = null;
  function drawChart(d) {
    const h = d.wallet_history;
    const card = document.querySelector('.chart-card');
    if (chart) { chart.destroy(); chart = null; }
    if (!h.dates.length) { card.innerHTML = `<div class="empty">${t('noChart')}</div>`; return; }
    if (!$('#wallet-chart')) card.innerHTML = '<canvas id="wallet-chart" height="120"></canvas>';
    const dark = document.documentElement.dataset.theme === 'dark';
    const grid = dark ? 'rgba(255,255,255,.08)' : 'rgba(0,0,0,.08)';
    const tick = dark ? '#8f98ab' : '#646b7a';
    chart = new Chart($('#wallet-chart'), {
      type: 'line',
      data: {
        labels: h.dates.map((x) => x.slice(5)),
        datasets: Object.entries(h.series).map(([m, vals]) => ({
          label: m, data: vals, borderColor: COLORS[m] || '#999', backgroundColor: COLORS[m] || '#999',
          tension: .25, pointRadius: 2, borderWidth: 2, spanGaps: true,
        })),
      },
      options: {
        responsive: true, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: tick, boxWidth: 10 } } },
        scales: { x: { grid: { color: grid }, ticks: { color: tick } }, y: { grid: { color: grid }, ticks: { color: tick } } },
      },
    });
  }

  function openDetail(id) {
    const g = [...DATA.upcoming, ...DATA.results].find((x) => x.game_id === id);
    if (!g) return;
    openId = id;
    const final = g.status === 'final';
    $('#detail-title').textContent = `${team(g, 'away')} @ ${team(g, 'home')} · ${when(g)}` + (final ? ` · ${g.away_score}-${g.home_score}` : '');
    const rows = (g.models || []).map((m) => {
      const ph = m.pred_total != null ? Math.round((m.pred_total + (m.pred_margin || 0)) / 2) : null;
      const pa = m.pred_total != null ? Math.round((m.pred_total - (m.pred_margin || 0)) / 2) : null;
      const res = m.result === 'WON' ? `<span class="pos">✅ ${fmtPnl(m.pnl)}</span>` : m.result === 'LOST' ? `<span class="neg">❌ ${fmtPnl(m.pnl)}</span>` : '';
      const conf = m.confidence ? (t('conf')[m.confidence] || m.confidence) : '';
      return `<div class="model-row">
        <div class="top"><b>${esc(m.name)}</b><span>${esc(nameOf(g, m.pick))} ${pct(pickProb(g, m))}${ph != null ? ` · ${pa}-${ph}` : ''} ${conf ? `<span class="tag">${esc(conf)}</span>` : ''}${m.odds ? ` <span class="tag">@${m.odds.toFixed(2)}</span>` : ''} ${res}</span></div>
        <div class="why">${esc(why(m))}</div>
      </div>`;
    }).join('');
    $('#detail-body').innerHTML = `${final ? '' : consensusLine(g)}${oddsLine(g)}<div style="margin:6px 0 10px">${recChips(g)}</div>${rows || `<div class="empty">${t('noModelRun')}</div>`}`;
    $('#detail').classList.remove('hidden');
  }
  function closeDetail() { openId = null; $('#detail').classList.add('hidden'); }
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', closeDetail));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

  applyStatic();
  fetch('data/site.json?t=' + Date.now()).then((r) => r.json()).then(render).catch((e) => {
    $('#upcoming-list').innerHTML = `<div class="empty">${t('loadFail')}${esc(e.message)}</div>`;
  });
})();
