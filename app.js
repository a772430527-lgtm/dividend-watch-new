"use strict";

const $ = id => document.getElementById(id);
const PERIODS = ["day", "week", "month"];
const PERIOD_LABELS = {day: "日线", week: "周线", month: "月线"};
const LEVEL_CLASSES = {"上": "level-up", "中上": "level-upper", "中": "level-mid", "中下": "level-lower", "下": "level-down"};
const state = {
  stocks: [], selected: "000807", period: "day", refreshing: false,
  chartData: {}, reports: {}, dividends: {}, chartReady: false, currentSource: null,
  reportLoading: {}, autoReportsStarted: false
};
const fmt = (v, digits=2) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);
const pct = v => v == null ? "—" : fmt(v) + "%";
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const escapeAttr = esc;
const text = (id, value) => { $(id).textContent = value == null ? "—" : value; };
function badge(level) {
  return `<span class="levelTag ${LEVEL_CLASSES[level] || "level-na"}">${esc(level || "—")}</span>`;
}
async function api(url, options) {
  const response = await fetch(url, options);
  const j = await response.json();
  if (!response.ok) throw new Error(j.detail || `${response.status} ${response.statusText}`);
  return j;
}
function status(message, error=false) {
  text("status", message);
  $("pulse").classList.toggle("active", !error && state.refreshing);
}
function makePositions(boll) {
  return PERIODS.map(period => {
    const data = boll?.[period];
    const b = data || {};
    const tooltip = `${PERIOD_LABELS[period]} HFQ ｜日期:${b.date || "未获取"} ｜上轨:${fmt(b.upper, 3)} 中轨:${fmt(b.mid, 3)} 下轨:${fmt(b.lower, 3)} ｜后复权收盘:${fmt(b.close_hfq, 3)}`;
    return `<span title="${escapeAttr(tooltip)}">${badge(b.position)}</span>`;
  }).join("");
}
function makeRow(r) {
  const ch = r.change_pct;
  const c = ch == null ? "" : ch >= 0 ? "rise" : "fall";
  const change = ch == null ? "—" : `${ch >= 0 ? "▲" : "▼"} ${pct(Math.abs(ch))}`;
  const p = r.yield_percentile;
  const barWidth = p == null ? 0 : Math.max(0, Math.min(100, p));
  return `<tr data-code="${r.code}" class="${r.code === state.selected ? "selected" : ""}">
    <td><div class="stockName">${esc(r.name)}</div><div class="stockCode">${r.code} · ${esc(r.industry)}</div>${r.pool_group !== "原有33只" ? `<span class="poolTag" title="新增观察候选，筛选条件需按最新数据核验">${esc(r.pool_group || '')}</span>` : ''}</td>
    <td><div class="number">${fmt(r.price)}</div><div class="${c} subval">${change}</div></td>
    <td><div class="number gold">${pct(r.yield)}</div><div class="subval">${r.yield == null ? "待核验" : "已公告RLY"}</div></td>
    <td><div class="number">${pct(p)}</div><div class="progressbar"><i style="width:${barWidth}%"></i></div></td>
    <td><div class="btags">${makePositions(r.boll)}</div></td>
    <td>${r.resonance == null ? '<span class="no" title="需要有效的周线和月线HFQ BOLL">待计算</span>' : r.resonance ? '<span class="yes">★ 是</span>' : '<span class="no">否</span>'}</td>
    <td>${r.recent_report ? `<div class="reportInRow"><b>${esc(r.recent_report.period_label || r.recent_report.period)}</b><small>${esc(r.recent_report.date || '披露日未知')}</small><a href="${escapeAttr(r.recent_report.pdf_url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">官方PDF ↗</a></div>` : `<span class="${r.report_error ? 'reportFail' : 'subval'}">${r.report_error ? '读取失败' : '待同步'}</span>`}</td>
    <td><button class="minorButton" data-fin="${r.code}">财报分析</button></td>
  </tr>`;
}
function getSorted() {
  const q = $("search").value.trim().toLowerCase();
  const industry = $("industry").value;
  const mode = $("sort").value;
  const poolFilter = $("poolFilter").value;
  let list = state.stocks.filter(r =>
    (!q || (r.name + r.code + r.industry).toLowerCase().includes(q)) &&
    (!industry || r.industry === industry) &&
    (!poolFilter || (poolFilter === "new_soe" && ["央企候选", "地方国企候选"].includes(r.pool_group)) ||
     (poolFilter === "nuclear" && r.pool_group === "双核电新增") ||
     (poolFilter === "existing" && r.pool_group === "原有33只") ||
     (poolFilter === "new_all" && r.pool_group !== "原有33只")) &&
    (!$("resonanceOnly").checked || r.resonance === true));
  if (mode === "name") list.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  else {
    const key = mode === "change" ? "change_pct" : mode === "percentile" ? "yield_percentile" : "yield";
    list.sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity));
  }
  return list;
}
function fillIndustries() {
  const industries = [...new Set(state.stocks.map(x => x.industry))].sort();
  const select = $("industry"), previous = select.value;
  select.innerHTML = '<option value="">全部行业</option>' + industries.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
  select.value = previous;
}
function drawList() {
  const list = getSorted();
  $("rows").innerHTML = list.map(makeRow).join("") || '<tr><td colspan="8" style="padding:28px;color:#9cb1ca;text-align:center">没有符合条件的标的</td></tr>';
  text("count", `${list.length} / ${state.stocks.length} 只股票`);
  document.querySelectorAll("tbody tr[data-code]").forEach(tr => {
    tr.onclick = e => {
      const code = tr.dataset.code;
      selectStock(code);
      if (e.target.closest("[data-fin]")) loadFinancials(code);
    };
  });
}
function selectStock(code) {
  if (code !== state.selected) closeReportPreview();
  state.selected = code;
  drawList();
  drawDetail();
  loadStockDetail(code).catch(e => status(`单股缓存获取失败：${e.message}`, true));
  // Load the most recent financial statement and original PDF upon selection.
  loadFinancials(code).catch(() => {});
}
async function loadStockDetail(code) {
  const info = await api("/api/stock/" + code);
  if (code !== state.selected) return;
  if (info.chart && info.boll_source === $('bollSource').value) state.chartData[code] = info.chart;
  if (info.dividends) state.dividends[code] = info.dividends;
  drawDetail();
}
function drawDetail() {
  const r = state.stocks.find(x => x.code === state.selected);
  if (!r) return;
  text("selectedName", r.name);
  text("selectedCode", r.code);
  text("selectedIndustry", r.industry + (r.pool_group !== "原有33只" ? " · " + r.pool_group : ""));
  text("selectedPrice", fmt(r.price));
  text("selectedChange", r.change_pct == null ? "报价未获取" : `${r.change_pct >= 0 ? "▲" : "▼"} ${pct(Math.abs(r.change_pct))}`);
  $("selectedChange").className = r.change_pct == null ? "muted" : r.change_pct >= 0 ? "rise" : "fall";
  text("mYield", pct(r.yield));
  text("mYieldBasis", r.rly_details?.status || "尚未取得有效的宣告分红记录");
  renderRlyBreakdown(r);
  text("mPercentile", pct(r.yield_percentile));
  text("mSamples", r.yield_samples ? `${r.yield_samples} 个有效历史周样本` : "近10年历史数据暂缺 / 样本不足");
  text("mResonance", r.resonance == null ? "待计算" : r.resonance ? "★ 是" : "否");
  $("mResonance").className = r.resonance === true ? "gold" : "";
  text("metricStamp", r.boll?.week?.date ? `共振：周${r.boll.week.date} · 月${r.boll.month?.date||'未取得'} · HFQ · ${r.boll_source} · 更新${(r.boll_updated_at||'').slice(11,19)}` : "周/月 HFQ 待计算");
  $("bollrow").innerHTML = PERIODS.map(period => {
    const b = r.boll?.[period];
    return `<div><small>${PERIOD_LABELS[period]} · HFQ</small><b>${badge(b?.position)}</b><small>${b?.date || "待获取"}</small></div>`;
  }).join("");
  drawChart();
  const report = state.reports[r.code];
  if (report) renderReports(report);
  else {
    renderLatestReportCard(r.recent_report, r.report_error, r.report_fetched_at);
    text("reportPeriod", r.recent_report?.period_label || "最近财报待同步");
    text("reportText", "正在核验最近一期财务指标（报告期可能与PDF不同）……");
    $("financialMetrics").innerHTML = "";
    $("reportLinks").innerHTML = "";
  }
  const dividends = state.dividends[r.code];
  if (dividends) renderDividends(dividends);
  else text("dividendText", "点击「分红明细」读取报告期、公告日和每10股派息金额。");
}
function bollNumbers() {
  const r = state.stocks.find(x => x.code === state.selected);
  const b = r?.boll?.[state.period];
  const line = $("bollNumbers");
  if (!b) {
    line.textContent = `${PERIOD_LABELS[state.period]} HFQ BOLL 暂无数据。请点击「刷新HFQ BOLL」读取，不以未复权行情填充。`;
    return;
  }
  line.innerHTML = [
    ["后复权收盘", b.close_hfq], ["上轨 UB", b.upper],
    ["中轨 MB", b.mid], ["下轨 LB", b.lower]
  ].map(([label, value]) => `<div><span>${esc(label)}</span><b>${fmt(value, 3)}</b></div>`).join("");
}
function chartPoints(points) {
  const canvas = $("priceChart"), ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const cs = (points || []).filter(p => Number.isFinite(p.close) && p.adjust === "hfq");
  if (cs.length < 20 || cs.filter(p => Number.isFinite(p.upper) && Number.isFinite(p.lower)).length < 2) return false;
  const all = cs.flatMap(p => [p.close, p.upper, p.mid, p.lower]).filter(v => v != null && Number.isFinite(v));
  let min = Math.min(...all), max = Math.max(...all);
  const pad = Math.max(0.0001, (max - min) * .12);
  min -= pad; max += pad;
  const left = 45, right = 62, top = 19, bottom = 30;
  const x = i => left + i / Math.max(1, cs.length - 1) * (W - left - right);
  const y = v => top + (max - v) / (max - min) * (H - top - bottom);
  ctx.font = "12px system-ui";
  ctx.lineWidth = 1;
  for (let j=0; j<5; j++) {
    const yy = top + j / 4 * (H - top - bottom);
    ctx.strokeStyle = "#274565";
    ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(W-right, yy); ctx.stroke();
    ctx.fillStyle = "#93adc9";
    ctx.fillText((max - j / 4 * (max - min)).toFixed(2), W-right+7, yy+4);
  }
  const series = (key, color, width=2, dash=[]) => {
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash);
    let started = false;
    cs.forEach((p, i) => {
      if (p[key] == null || !Number.isFinite(p[key])) { started = false; return; }
      if (!started) { ctx.moveTo(x(i), y(p[key])); started=true; }
      else ctx.lineTo(x(i), y(p[key]));
    });
    ctx.stroke();ctx.setLineDash([]);
  };
  series("upper", "#f49db5", 2);
  series("lower", "#6fdfae", 2);
  series("mid", "#f5c76b", 2);
  series("close", "#76c8ff", 2.4);
  const last = cs[cs.length-1];
  ctx.fillStyle = "#76c8ff";
  ctx.beginPath();ctx.arc(x(cs.length-1), y(last.close), 4.2, 0, Math.PI*2);ctx.fill();
  ctx.fillStyle = "#9ab1d0";
  ctx.fillText(cs[0].date, left, H-8);
  ctx.fillText(last.date, W-130, H-8);
  canvas.title = `HFQ ${PERIOD_LABELS[state.period]} ${last.date} 收盘 ${fmt(last.close,3)}；上 ${fmt(last.upper,3)} 中 ${fmt(last.mid,3)} 下 ${fmt(last.lower,3)}`;
  return true;
}
function drawChart() {
  bollNumbers();
  const points = state.chartData[state.selected]?.[state.period];
  const ready = chartPoints(points);
  $("priceChart").style.display = ready ? "block" : "none";
  $("chartPlaceholder").style.display = ready ? "none" : "block";
  if (!ready) text("chartPlaceholder", "需要至少 20 根真实 HFQ K 线来计算 BOLL；请先刷新当前标的。");
  document.querySelectorAll(".chartnav button").forEach(button => button.classList.toggle("active", button.dataset.period === state.period));
}
function finFormat(value, label) {
  if (value == null || String(value).trim() === "") return "—";
  const n = Number(String(value).replaceAll(",", ""));
  if (!Number.isFinite(n)) return esc(value);
  if (label.includes("每股")) return fmt(n,3) + " 元";
  if (Math.abs(n) > 10000000) return fmt(n/1e8, 2) + " 亿元";
  return fmt(n, 2);
}
function renderLatestReportCard(report, err, fetchedAt) {
  const host = $("latestReportBox");
  if (!report) {
    host.innerHTML = `<div class="reportEmpty">${esc(err ? '最新财报检索失败：'+err : '财报索引尚未同步，正在请求官方公告。')}</div>`;
    return;
  }
  const freshness = fetchedAt ? `索引更新时间：${esc(String(fetchedAt).replace('T',' '))}` : '索引更新时间未知';
  const note = err ? `<div class="reportFail">最近一次自动更新失败；以下保留已核验的缓存报告：${esc(err)}</div>` : '';
  host.innerHTML = `<div class="reportCard"><div><small>最近已披露定期报告</small><div class="reportBig">${esc(report.period_label || report.period)}</div><div class="muted tiny">报告期：${esc(report.period)} ｜披露：${esc(report.date)} ｜巨潮资讯</div><div class="reportTitle">${esc(report.title)}</div></div><div class="reportButtons"><button class="btn secondary" id="previewLatestReport">页面内预览</button><a class="btn primary" href="${escapeAttr(report.pdf_url)}" target="_blank" rel="noopener noreferrer">打开官方 PDF ↗</a></div></div><div class="muted tiny reportFreshness">${freshness} · 报告期≠披露日期；数据源无法访问时保留原链接并标注状态</div>${note}`;
}
function closeReportPreview() {
  $("reportViewer").hidden = true;
  $("reportFrame").removeAttribute('src');
}
function toggleReportPreview() {
  const panel = $('reportViewer');
  if (!panel.hidden) { closeReportPreview(); return; }
  const row = state.stocks.find(x=>x.code===state.selected);
  const report = state.reports[state.selected]?.latest_report || row?.recent_report;
  if (!report?.pdf_url) { status('没有可核验的官方PDF链接，无法预览',true); return; }
  panel.hidden = false;
  $('reportFrame').src = '/api/stock/'+encodeURIComponent(state.selected)+'/reports/pdf?index=0';
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function renderReports(payload) {
  const official = payload.latest_report || payload.reports?.[0];
  const row = state.stocks.find(x => x.code === state.selected);
  renderLatestReportCard(official, row?.report_error, row?.report_fetched_at || payload.fetched_at);
  text("reportPeriod", official?.period_label || payload.statement?.period || "尚无财报");
  const statement = payload.statement;
  const warn = payload.period_warning;
  text("reportText", statement?.period ?
      `财务指标报告期：${statement.period} · 新浪财经${warn ? '；⚠️ '+warn : '（与官方PDF期间独立核对）'}` :
      "未取到新浪财务数据，请直接查看巨潮财报 PDF 原文。");
  $("financialMetrics").innerHTML = statement?.metrics?.length ? statement.metrics.map(m => `<div class="finCard"><small>${esc(m.label)}</small><b>${finFormat(m.value,m.label)}</b><em>${m.yoy != null && m.yoy !== "" ? "同比(源字段)："+esc(m.yoy) : "同比暂缺"}</em></div>`).join("") : "";
  $("reportLinks").innerHTML = (payload.reports || []).slice(0,8).map(a => `<a href="${escapeAttr(a.pdf_url || a.url)}" target="_blank" rel="noopener noreferrer"><span>${esc(a.period_label || '')}｜${esc(a.title)}</span><small>${esc(a.date || '')} · PDF ↗</small></a>`).join("") || '<div class="textMuted">暂未获取可核验的报告PDF链接</div>';
  if (payload.errors?.length) $("reportLinks").innerHTML += `<div class="textMuted tiny">部分来源异常：${esc(payload.errors.join("；"))}</div>`;
}
function renderRlyBreakdown(r) {
  const d = r.rly_details || {};
  $("sourceDividend").href = `https://data.eastmoney.com/yjfp/detail/${r.code}.html`;
  if (d.yield == null) {
    $("rlyBreakdown").textContent = d.status || "宣告分红尚未核验，不能计算 RLY；请刷新全部或核对公告。";
    return;
  }
  const parts = (d.components || []).map(c => `<div class="rlyEntry"><span>${esc(c.category)} · ${esc(c.report_date)}<br><small>公告：${esc(c.announcement_date)} · ${esc(c.status || '状态未提供')}</small></span><strong>10派 ${fmt(c.cash_per_ten, 4)} 元<br><small>每股 ${fmt(c.adjusted_cash_per_share, 4)} 元${c.share_adjustment_factor && c.share_adjustment_factor !== 1 ? ' · 送转折算×'+fmt(c.share_adjustment_factor,3) : ''}</small></strong></div>`).join("");
  $("rlyBreakdown").innerHTML = `<div class="rlyStatus">${esc(d.mid_year_status || d.status || "公告已读取")}</div>`+
  parts + `<div class="rlyFormula">(末期 ${fmt(d.year_end_cash,4)} + 中期 ${fmt(d.interim_cash,4)}) ÷ 未复权现价 ${fmt(r.price)} × 100 = <strong>${pct(d.yield)}</strong></div>`+
  `<div class="tiny muted">历史股息率分位与当期RLY采用相同规则；分红可能因最新公告更正或股本变化而变化。</div>`;
}
function renderDividends(records) {
  $("dividendText").innerHTML = records.slice(0,24).map(x => `<div class="divrow"><span>${esc(x.report_date || '未知报告期')} · ${esc(x.category || '')}<br><small>公告：${esc(x.announcement_date || '—')} ｜除息：${esc(x.ex_date || '尚未实施')}</small></span><strong>10派 ${fmt(x.cash_per_ten,4)} 元<br><small>${fmt(x.cash_per_share,4)} 元/股 · ${esc(x.status || '待确认')}</small></strong></div>`).join("") || "分红数据不足，需查看公告核验";
}

async function loadFinancials(code, force=false) {
  if (state.reportLoading[code]) return state.reportLoading[code];
  if (state.reports[code] && !force) {
    if (code === state.selected) renderReports(state.reports[code]);
    return state.reports[code];
  }
  if (code === state.selected) {
    text("reportText", "正在获取财务数据及最新财报PDF……");
    $("financialMetrics").innerHTML = "";
  }
  const work = (async () => {
    try {
      const result = await api("/api/stock/" + code + "/financials" + (force ? '?force=true' : ''));
      state.reports[code] = result;
      if (code === state.selected) renderReports(result);
      return result;
    } catch(e) {
      if (code === state.selected) text("reportText", "财报加载失败：" + e.message + "，可点击刷新重试。");
      return null;
    } finally { delete state.reportLoading[code]; }
  })();
  state.reportLoading[code] = work;
  return work;
}
async function loadDividends(code) {
  text("dividendText", "正在读取真实分红记录……");
  try {
    const result = await api("/api/stock/" + code + "/dividends");
    state.dividends[code] = result.dividends;
    if (code === state.selected) renderDividends(result.dividends);
  } catch(e) {
    if(code === state.selected) text("dividendText", "分红加载失败：" + e.message);
  }
}
async function loadBoll(code) {
  text("chartPlaceholder", "正在读取 HFQ 日、周、月 K 线……");
  try {
    const source = $('bollSource').value;
    const result = await api("/api/stock/" + code + "/boll?source=" + source);
    if (source !== $('bollSource').value) return;
    state.chartData[code] = result.candles;
    const row = state.stocks.find(x => x.code === code);
    if (row) { row.boll = result.boll; row.resonance = result.resonance; row.boll_source = source; }
    if (code === state.selected) { drawList(); drawDetail(); }
    const failed = Object.entries(result.errors || {});
    if (failed.length) status("部分周期 HFQ 数据失败：" + failed.map(([p,msg])=>PERIOD_LABELS[p]+": "+msg).join("；"), true);
    else status(`已更新 ${row?.name || code} 的日/周/月后复权 BOLL（三轨）`);
  } catch(e) { status("读取 HFQ BOLL 失败：" + e.message, true); }
}
async function refreshAllBollSignals() {
  const source = $('bollSource').value;
  state.chartData = {};
  status('正在同步42只股票的后复权BOLL与低位共振…');
  try {
    const result = await api('/api/boll/refresh?source='+encodeURIComponent(source), {method:'POST'});
    state.currentSource = source;
    await loadStocks();
    status(result.message);
  } catch(e) {
    status('BOLL及共振同步失败：'+e.message,true);
  }
}
async function refreshQuotes() {
  status("更新腾讯行情快照……");
  try { await api("/api/quotes", {method:"POST"}); await loadStocks(); status("腾讯报价已更新；BOLL 需单独刷新HFQ K线"); }
  catch(e) { status("报价获取失败：" + e.message,true); }
}
async function refreshAll() {
  try { const result=await api("/api/refresh?boll_source=" + $('bollSource').value, {method:"POST"}); state.refreshing=true; status(result.message); }
  catch(e) { status("启动刷新失败：" + e.message,true); }
}
async function loadStocks() {
  try {
    const info = await api("/api/stocks");
    const firstLoad = state.stocks.length === 0;
    const completedNow = state.refreshing && !info.jobs.running;
    state.stocks = info.stocks;
    fillIndustries();drawList();drawDetail();
    const rj = info.report_job || {};
    text('reportJobStatus', rj.running ? `财报核验 ${rj.done || 0}/${rj.total || 42}` :
       rj.last_refresh ? `财报索引刷新于 ${String(rj.last_refresh).slice(0,16).replace('T',' ')}` : '财报索引：待同步');
    if (!state.autoReportsStarted) {
      state.autoReportsStarted = true;
      api('/api/reports/refresh', {method:'POST'}).catch(e => status('财报自动同步未启动：'+e.message,true));
    }
    // Index can complete after auto-selected statement. Keep the latest PDF card current.
    const selectedRow = state.stocks.find(x=>x.code===state.selected);
    if (selectedRow?.recent_report && state.reports[state.selected] &&
        state.reports[state.selected].latest_report?.pdf_url !== selectedRow.recent_report.pdf_url) {
      state.reports[state.selected].latest_report = selectedRow.recent_report;
      if (state.reports[state.selected].statement?.period && state.reports[state.selected].statement.period !== selectedRow.recent_report.period)
        state.reports[state.selected].period_warning = `财务指标${state.reports[state.selected].statement.period}，最新官方PDF${selectedRow.recent_report.period}，报告期不同，请勿混用`;
      state.reports[state.selected].reports = [selectedRow.recent_report, ...(state.reports[state.selected].reports||[]).filter(x=>x.pdf_url!==selectedRow.recent_report.pdf_url)];
      renderReports(state.reports[state.selected]);
    }
    if (firstLoad) {
      state.currentSource = info.metadata?.boll_source || "eastmoney";
      $("bollSource").value = state.currentSource;
    }
    if (firstLoad || completedNow) loadStockDetail(state.selected).catch(() => {});
    if (firstLoad) loadFinancials(state.selected).catch(() => {});
    const j=info.jobs;
    state.refreshing=j.running;
    $("pulse").classList.toggle("active",j.running);
    status(j.running ? `${j.phase} · ${j.done}/${j.total}` : j.error ? `行情部分刷新失败：${j.error}` : j.finished ? "刷新结束；缺失的真实数据展示「—」" : "等待联网获取行情", !!j.error);
    text("updated", j.finished ? "上次刷新 " + j.finished.slice(0,19).replace("T"," ") : "");
  } catch(e) { status("无法连接本地后端："+e.message,true); }
}
["search","industry","sort","resonanceOnly"].forEach(id => $(id).addEventListener(id === "search" ? "input" : "change",drawList));
$("refreshAll").onclick=refreshAll;
$("refreshBollSignals").onclick=refreshAllBollSignals;
$("updateQuote").onclick=refreshQuotes;
$("financialBtn").onclick=()=>loadFinancials(state.selected, true);
$('closeReportViewer').onclick=closeReportPreview;
$('latestReportBox').addEventListener('click', e=>{
  if (e.target.closest('#previewLatestReport')) toggleReportPreview();
});
$("refreshReports").onclick=async()=>{
  text('reportJobStatus','正在启动42只财报索引更新…');
  try { await api('/api/reports/refresh?force=true',{method:'POST'}); await loadStocks(); }
  catch(e) { text('reportJobStatus','财报更新失败：'+e.message); }
};
$("dividendBtn").onclick=()=>loadDividends(state.selected);
$("bollBtn").onclick=()=>loadBoll(state.selected);
document.querySelectorAll(".chartnav button").forEach(button => button.onclick=()=>{ state.period=button.dataset.period;drawChart(); });

async function auditBoll() {
  const code=state.selected, asof=$('auditDate').value;
  const panel=$('auditResult');panel.hidden=false;
  panel.textContent='正在核对 ' + code + ' 截至 ' + (asof || '最近交易日') + ' 的20根后复权K线…';
  try {
    const source = $('bollSource').value;
    const payload=await api('/api/stock/'+code+'/boll/audit?source='+source+(asof?'&asof='+encodeURIComponent(asof):''));
    const labels={day:'日线',week:'周线',month:'月线'};
    const groups=['day','week','month'].map(p => {
      const a=payload.audit?.[p];
      if(!a) return `<div class="auditCard"><b>${labels[p]}</b>：${esc(payload.errors?.[p] || '无数据')}</div>`;
      const b=a.result;
      return `<div class="auditCard"><h4>${labels[p]} (${esc(a.last_bar_date)}) ${badge(b.position)}</h4>
        <p>HFQ收盘：<b>${fmt(b.close_hfq,4)}</b> ｜20根均值：<b>${fmt(a.mean,4)}</b> ｜总体标准差：<b>${fmt(a.std_population,4)}</b></p>
        <p>上轨 ${fmt(b.upper,4)} ｜中轨 ${fmt(b.mid,4)} ｜下轨 ${fmt(b.lower,4)} ｜Z = ${fmt(a.z_score,3)}</p>
        <details><summary>检查20根后复权收盘价（${esc(a.window_start)} 至 ${esc(a.window_end)}）</summary><div class="auditCloses">${a.closes.map(x=>`<span>${esc(x.date)}：${fmt(x.close_hfq,4)}</span>`).join('')}</div></details>
      </div>`;
    });
    panel.innerHTML='<div class="tiny muted">数据源：'+esc(source)+'；历史对照只影响本面板，不改变左侧排名。如手机软件显示盘中14:45，本接口的日K可能已经更新或尚未包含当天。</div>'+groups.join('');
  } catch (e) {panel.textContent='核验失败：'+e.message;}
}
$('auditBtn').onclick=auditBoll;
async function compareBoll() {
  const code=state.selected, asof=$('auditDate').value;
  const panel=$('compareResult'); panel.hidden=false;
  panel.textContent='正在获取腾讯与东方财富两套后复权K线……';
  try {
    const c=await api('/api/stock/'+code+'/boll/compare'+(asof?'?asof='+encodeURIComponent(asof):''));
    const hdr='<div class="tiny muted">双源对比不会擅自修改复权因子。不同供应商的累计后复权价格可能采用不同基准；比较同一天与相同周期后再判断差异。截图为14:45盘中，事后日K可能无法精确重现。</div>';
    const providers=['eastmoney','tencent'].map(src=>{
      const p=c.providers[src]||{};
      const data=['day','week','month'].map(period=>{
        const b=p.boll?.[period];
        return b?`<div class="auditCard"><b>${src==='eastmoney'?'东方财富':'腾讯'} · ${PERIOD_LABELS[period]} ${esc(b.date)}</b> ${badge(b.position)}
        <p>HFQ收盘 ${fmt(b.close_hfq,4)} · 上轨 ${fmt(b.upper,4)} · 中轨 ${fmt(b.mid,4)} · 下轨 ${fmt(b.lower,4)}</p></div>`:
        `<div class="auditCard"><b>${src==='eastmoney'?'东方财富':'腾讯'} · ${PERIOD_LABELS[period]}</b>：${esc(p.errors?.[period]||'无数据')}</div>`;
      }).join('');
      return `<div style="flex:1;min-width:210px">${data}</div>`;
    });
    const r=c.screenshot_reference;
    const ref=r?`<div class="auditCard"><strong>你提供的浙能电力截图 · 2026-11-03 14:45:59（盘中参考）</strong><p>HFQ 日K 上轨 10.45 · 中轨 10.23 · 下轨 10.02 · 未复权现价 5.35</p><p class="tiny muted">数据源APP与14:45历史快照不一定可从盘后日K接口精确还原；不能直接拿未复权现价与HFQ布林通道比较。</p></div>`:'';
    const comparison=c.comparison?.day;
    const diff=comparison?`<div class="auditCard"><b>日线差异（东财 − 腾讯）</b><p>上轨 ${fmt(comparison.upper_diff,4)} · 中轨 ${fmt(comparison.mid_diff,4)} · 下轨 ${fmt(comparison.lower_diff,4)}</p><p>日K日期 ${comparison.same_k_date?'一致':'不一致'}；五档 ${comparison.same_position?'相同':'不同'}；标准化 Z-score 差 ${fmt(comparison.shape_z_diff,4)}</p></div>`:'';
    panel.innerHTML=hdr+`<div style="display:flex;flex-wrap:wrap;gap:8px">${providers.join('')}</div>`+diff+ref;
  } catch(e) { panel.textContent='双源对照失败：'+e.message; }
}
$('compareBtn').onclick=compareBoll;
$('bollSource').addEventListener('change',async()=>{
  const source = $('bollSource').value;
  state.chartData = {}; // Never show old-source K lines after switching providers.
  $('auditResult').hidden = true;
  $('compareResult').hidden = true;
  status('正在切换后复权源，全部42只的BOLL和低位共振将自动重算…');
  try {
    const result=await api('/api/boll/refresh?source='+encodeURIComponent(source), {method:'POST'});
    state.currentSource=source;
    await loadStocks();
    status(result.message);
  } catch(e) {
    $('bollSource').value=state.currentSource || 'eastmoney';
    status('切换数据源失败：'+e.message,true);
  }
});
$("poolFilter").addEventListener("change", drawList);
loadStocks();
setInterval(loadStocks, 3500);
