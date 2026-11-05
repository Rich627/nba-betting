/* NBA Master 預測 — static front-end over data/site.json (built by pipeline/build_site_json.py). */
(function () {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtPnl = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Math.round(v));
  const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
  const cls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
  const COLORS = { GPT: '#10a37f', Claude: '#d97757', Gemini: '#4285f4', DeepSeek: '#4d6bfe', Kimi: '#8b5cf6', Grok: '#9ca3af' };
  let DATA = null;

  // theme
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('nba-theme', next); } catch (_) {}
    if (DATA) drawChart(DATA);
  });

  function oddsLine(g) {
    const o = g.odds;
    if (!o || !o.ml_home) return '<div class="line muted">無盤口</div>';
    const parts = [`獨贏 ${esc(g.home_zh)} <b>${o.ml_home.toFixed(2)}</b> / ${esc(g.away_zh)} <b>${o.ml_away.toFixed(2)}</b>`];
    if (o.spread_line != null) parts.push(`讓分 ${esc(g.home_zh)} <b>${o.spread_line > 0 ? '+' : ''}${o.spread_line}</b>`);
    if (o.total_line != null) parts.push(`大小 <b>${o.total_line}</b>`);
    return `<div class="line">${parts.join(' · ')}</div>`;
  }

  function consensusLine(g) {
    const c = g.consensus;
    if (!c) return '<div class="line muted">尚未跑模型</div>';
    const ph = Math.round((c.pred_total + c.pred_margin) / 2), pa = Math.round((c.pred_total - c.pred_margin) / 2);
    return `<div class="line">共識 <b>${esc(c.fav_zh)}</b> 勝率 <b>${pct(c.fav_prob)}</b>（${c.n_models} 模型 · 一致 ${pct(c.agreement)}）· 預測 ${esc(g.away_zh)} ${pa}-${ph} ${esc(g.home_zh)}</div>`;
  }

  function recChips(g) {
    if (!g.recs || !g.recs.length) return g.consensus ? '<span class="rec push">無 +EV，不買</span>' : '';
    return g.recs.map((r) => {
      const k = r.result === 'WON' ? 'won' : r.result === 'LOST' ? 'lost' : r.result === 'PUSH' ? 'push' : '';
      const mark = r.result === 'WON' ? '✅ ' : r.result === 'LOST' ? '❌ ' : '👉 ';
      const odds = r.odds ? ` @${r.odds.toFixed(2)}` : '';
      const edge = r.edge != null ? `（EV ${r.edge > 0 ? '+' : ''}${Math.round(r.edge * 100)}%）` : '';
      const pnl = r.pnl != null ? ` ${fmtPnl(r.pnl)}` : '';
      return `<span class="rec ${k}">${mark}${esc(r.label)}${odds}${edge}${pnl}</span>`;
    }).join('');
  }

  function modelDots(g) {
    if (!g.models || !g.models.length) return '';
    return '<div class="dots">' + g.models.map((m) => {
      const k = m.result === 'WON' ? 'won' : m.result === 'LOST' ? 'lost' : '';
      return `<span class="mdot ${k}" title="${esc(m.name)} → ${esc(m.pick_zh)} ${pct(m.home_win_prob != null ? (m.pick === g.home ? m.home_win_prob : 1 - m.home_win_prob) : null)}">${esc(m.name)} ${esc(m.pick_zh)}</span>`;
    }).join('') + '</div>';
  }

  function gameCard(g, final) {
    const score = final
      ? `<span class="score">${g.away_score}</span><span class="at">–</span><span class="score">${g.home_score}</span>`
      : '<span class="at">@</span>';
    const hits = final && g.hits ? `<span>模型命中 ${g.hits}</span>` : '';
    return `<article class="card game" data-id="${esc(g.game_id)}">
      <div class="when"><span>${esc(g.tw)}</span>${hits || `<span>${esc(g.status === 'live' ? '進行中' : '')}</span>`}</div>
      <div class="teams">
        <div><span class="abbr">${esc(g.away)} 客</span>${esc(g.away_zh)}</div>
        <div style="display:flex;gap:8px;align-items:center">${score}</div>
        <div style="text-align:right"><span class="abbr">${esc(g.home)} 主</span>${esc(g.home_zh)}</div>
      </div>
      ${final ? '' : consensusLine(g)}${g.status === 'live' && !g.consensus ? '' : oddsLine(g)}
      <div>${recChips(g)}</div>
      ${modelDots(g)}
    </article>`;
  }

  function render(d) {
    DATA = d;
    $('#generated').textContent = '更新 ' + d.generated_at.replace('T', ' ').slice(0, 16);
    const s = d.summary;
    const recRate = s.recs_graded ? `${s.recs_won}/${s.recs_graded}` : '—';
    $('#stats').innerHTML = [
      ['已預測場次', s.n_predicted_games],
      ['喊過的建議', s.n_recs],
      ['建議命中', recRate],
      ['建議損益', `<span class="${cls(s.recs_pnl)}">${fmtPnl(s.recs_pnl)}</span>`],
    ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

    $('#leaderboard').innerHTML = d.wallet.length
      ? d.wallet.map((w) => `<div class="lb"><div><div class="name">${esc(w.model)}</div><div class="rec">${w.wins}/${w.bets} 命中</div></div><div class="v ${cls(w.pnl)}" style="font-family:'Space Grotesk',monospace;font-weight:700">${fmtPnl(w.pnl)}</div></div>`).join('')
      : '<div class="empty">還沒有結算過的場次</div>';

    $('#upcoming-list').innerHTML = d.upcoming.length ? d.upcoming.map((g) => gameCard(g, false)).join('') : '<div class="empty">目前沒有排定的比賽</div>';
    $('#results-list').innerHTML = d.results.length ? d.results.map((g) => gameCard(g, true)).join('') : '<div class="empty">還沒有賽果</div>';
    drawChart(d);

    document.querySelectorAll('.game').forEach((el) => el.addEventListener('click', () => openDetail(el.dataset.id)));
  }

  let chart = null;
  function drawChart(d) {
    const h = d.wallet_history;
    const ctx = $('#wallet-chart');
    if (chart) { chart.destroy(); chart = null; }
    if (!h.dates.length) { ctx.parentElement.innerHTML = '<div class="empty">錢包曲線會在第一天結算後出現</div>'; return; }
    const dark = document.documentElement.dataset.theme === 'dark';
    const grid = dark ? 'rgba(255,255,255,.08)' : 'rgba(0,0,0,.08)';
    const tick = dark ? '#8f98ab' : '#646b7a';
    chart = new Chart(ctx, {
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
    const final = g.status === 'final';
    $('#detail-title').textContent = `${g.away_zh} @ ${g.home_zh} · ${g.tw}` + (final ? ` · ${g.away_score}-${g.home_score}` : '');
    const rows = (g.models || []).map((m) => {
      const prob = m.home_win_prob == null ? null : (m.pick === g.home ? m.home_win_prob : 1 - m.home_win_prob);
      const ph = m.pred_total != null ? Math.round((m.pred_total + (m.pred_margin || 0)) / 2) : null;
      const pa = m.pred_total != null ? Math.round((m.pred_total - (m.pred_margin || 0)) / 2) : null;
      const res = m.result === 'WON' ? `<span class="pos">✅ ${fmtPnl(m.pnl)}</span>` : m.result === 'LOST' ? `<span class="neg">❌ ${fmtPnl(m.pnl)}</span>` : '';
      return `<div class="model-row">
        <div class="top"><b>${esc(m.name)}</b><span>${esc(m.pick_zh)} ${pct(prob)}${ph != null ? ` · ${pa}-${ph}` : ''} <span class="tag">${esc(m.confidence || '')}</span>${m.odds ? ` <span class="tag">@${m.odds.toFixed(2)}</span>` : ''} ${res}</span></div>
        <div class="why">${esc(m.reasoning || '')}</div>
      </div>`;
    }).join('');
    $('#detail-body').innerHTML = `${final ? '' : consensusLine(g)}${oddsLine(g)}<div style="margin:6px 0 10px">${recChips(g)}</div>${rows || '<div class="empty">這場沒跑模型</div>'}`;
    $('#detail').classList.remove('hidden');
  }
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => $('#detail').classList.add('hidden')));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#detail').classList.add('hidden'); });

  fetch('data/site.json?t=' + Date.now()).then((r) => r.json()).then(render).catch((e) => {
    $('#upcoming-list').innerHTML = `<div class="empty">資料載入失敗：${esc(e.message)}</div>`;
  });
})();
