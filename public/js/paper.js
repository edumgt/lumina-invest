/* 모의투자(주식·코인·대체자산·Open API) + QuantConnect LEAN 백테스트 화면 로직.
 * stock-coin-trade(frontend/js/order.js, hold_crypto.js, alternatives.js, api-keys.js, alpaca-test.js)와
 * domain-rag-lab(frontend/app.js의 backtest 워크플로우)을 lumina-invest SPA(app.html)로 이식한 모듈.
 * app.html의 메인 모듈에서 initPaperViews() / onPaperViewActivated(view) 로 연결한다. */
import { api, setToast, escHtml, fmt, fmtPct } from "/js/common.js";

const $ = (id) => document.getElementById(id);
const won = (n, d = 0) => `${fmt(n, d)}원`;
const signCls = (n) => (parseFloat(n) >= 0 ? "text-emerald-600" : "text-red-600");
const ts = (ms) => new Date(ms).toLocaleString("ko-KR", { hour12: false });
const sideBadge = (t) => `<span class="${String(t).toUpperCase() === "BUY" ? "badge-buy" : "badge-sell"}">${String(t).toUpperCase() === "BUY" ? "매수" : "매도"}</span>`;
const kpi = (label, value, cls = "") => `
  <div class="rounded-xl border border-white/10 bg-black/20 p-3">
    <div class="text-xs" style="color:var(--text-mute);">${escHtml(label)}</div>
    <div class="text-lg font-bold ${cls}">${value}</div>
  </div>`;
const emptyRow = (cols, text) => `<tr><td colspan="${cols}" style="color:var(--text-mute);text-align:center;">${escHtml(text)}</td></tr>`;

let altChart = null;
let leanChart = null;
let cryptoMarkets = [];

/* ────────────────────────────────────────────────────────────────
 * 1. 모의투자 대시보드
 * ──────────────────────────────────────────────────────────────── */
async function loadPaperDashboard() {
  try {
    const a = await api("/api/paper/account");
    $("pd-kpis").innerHTML = [
      kpi("보유 현금", won(a.cash)),
      kpi("주식 평가액", won(a.stockEval)),
      kpi("코인 평가액", won(a.cryptoEval)),
      kpi("대체자산 평가액", won(a.alternativeEval)),
      kpi("총 자산", won(a.totalAsset)),
      kpi("총 손익", `${won(a.totalPnl)} <span class="text-sm">(${fmtPct(a.totalPnlRate)})</span>`, signCls(a.totalPnl)),
    ].join("");
    $("pd-counts").textContent = `보유: 주식 ${a.counts.stocks}종목 · 코인 ${a.counts.crypto}종목 · 대체자산 ${a.counts.alternatives}종목 · 초기자금 ${won(a.initialCash)}`;
  } catch (e) { setToast(e.message, "error"); }

  try {
    const [s, c, alt] = await Promise.all([
      api("/api/paper/stocks/orders/history?limit=10"),
      api("/api/paper/trade/order/history?limit=10"),
      api("/api/paper/alternatives/orders/history?limit=10"),
    ]);
    const rows = [
      ...s.history.map(o => ({ ts: o.ts, kind: "주식", name: `${o.name} (${o.symbol})`, type: o.type, qty: fmt(o.quantity), price: won(o.price), amount: won(o.amount), source: o.source })),
      ...c.history.map(o => ({ ts: o.ts, kind: "코인", name: `${o.koreanName} (${o.marketCode})`, type: o.type, qty: fmt(o.quantity, 8), price: won(o.price), amount: won(o.amount), source: o.source })),
      ...alt.history.map(o => ({ ts: o.ts, kind: o.category, name: o.name, type: o.type, qty: fmt(o.quantity), price: won(o.price), amount: won(o.amount), source: o.source })),
    ].sort((x, y) => y.ts - x.ts).slice(0, 15);
    $("pd-recent").innerHTML = `<table><thead><tr><th>시각</th><th>구분</th><th>종목</th><th>매매</th><th style="text-align:right">수량</th><th style="text-align:right">단가</th><th style="text-align:right">금액</th><th>출처</th></tr></thead>
      <tbody>${rows.length ? rows.map(r => `<tr><td class="text-xs">${ts(r.ts)}</td><td>${escHtml(r.kind)}</td><td>${escHtml(r.name)}</td><td>${sideBadge(r.type)}</td><td style="text-align:right">${r.qty}</td><td style="text-align:right">${r.price}</td><td style="text-align:right">${r.amount}</td><td class="text-xs">${escHtml(r.source)}</td></tr>`).join("") : emptyRow(8, "아직 체결 내역이 없습니다.")}</tbody></table>`;
  } catch (e) { $("pd-recent").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function resetPaperAccount() {
  if (!confirm("주식·코인·대체자산 포지션과 주문 이력을 모두 삭제하고 초기 현금(1억원)으로 되돌립니다. 계속할까요?")) return;
  try {
    const r = await api("/api/paper/account/reset", { method: "POST" });
    setToast(`계좌를 초기화했습니다. 현금 ${won(r.cash)}`, "ok");
    loadPaperDashboard();
  } catch (e) { setToast(e.message, "error"); }
}

/* ────────────────────────────────────────────────────────────────
 * 2. 국내주식 모의주문
 * ──────────────────────────────────────────────────────────────── */
async function loadPaperStock() {
  loadStockPositions();
  loadStockHistory();
  try {
    const a = await api("/api/paper/account");
    $("ps-cash").textContent = won(a.cash);
  } catch {}
}

async function stockQuote() {
  const symbol = $("ps-symbol").value.trim();
  if (!symbol) return setToast("종목코드를 입력하세요.", "error");
  $("ps-quote").innerHTML = `<span style="color:var(--text-mute);">조회 중…</span>`;
  try {
    const q = await api(`/api/paper/stocks/quote?symbol=${encodeURIComponent(symbol)}`);
    const chg = q.prev_close ? ((q.price - q.prev_close) / q.prev_close * 100) : null;
    $("ps-quote").innerHTML = `<strong>${escHtml(q.name)}</strong> <span class="text-xs" style="color:var(--text-mute);">${escHtml(q.symbol)} · ${escHtml(q.market)}</span>
      <div class="text-xl font-bold mt-1">${fmt(q.price, q.currency === "KRW" ? 0 : 2)} ${escHtml(q.currency)} ${chg != null ? `<span class="text-sm ${signCls(chg)}">${fmtPct(chg)}</span>` : ""}</div>`;
    $("ps-symbol").value = q.symbol;
  } catch (e) { $("ps-quote").innerHTML = `<span class="text-red-500">${escHtml(e.message)}</span>`; }
}

async function stockPreview(side) {
  const symbol = $("ps-symbol").value.trim();
  const quantity = parseInt($("ps-qty").value);
  if (!symbol || !quantity) return setToast("종목코드와 수량을 입력하세요.", "error");
  try {
    const p = await api("/api/paper/stocks/orders/preview", { method: "POST", body: { symbol, side, quantity } });
    $("ps-preview").innerHTML = `
      <div class="rounded-xl border p-3 text-sm" style="border-color:${p.executable ? "var(--green-bd)" : "var(--red-bd)"}">
        <div class="flex items-center gap-2 mb-2">${sideBadge(p.side)} <strong>${escHtml(p.name)}</strong> <span class="text-xs" style="color:var(--text-mute);">${escHtml(p.symbol)}</span>
          <span class="ml-auto text-xs font-bold ${p.executable ? "text-emerald-600" : "text-red-600"}">${p.executable ? "체결 가능" : escHtml(p.reason || "체결 불가")}</span></div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <div>예상 단가<br><b>${won(p.estimatedPrice)}</b></div><div>예상 금액<br><b>${won(p.estimatedAmount)}</b></div>
          <div>현금 ${won(p.cashBefore)} → <b>${won(p.cashAfter)}</b></div><div>보유 ${fmt(p.positionBefore)}주 → <b>${fmt(p.positionAfter)}주</b></div>
        </div>
        <div class="text-xs mt-2" style="color:var(--text-mute);">${escHtml(p.notice)}</div>
      </div>`;
  } catch (e) { setToast(e.message, "error"); }
}

async function stockOrder(side) {
  const symbol = $("ps-symbol").value.trim();
  const quantity = parseInt($("ps-qty").value);
  if (!symbol || !quantity) return setToast("종목코드와 수량을 입력하세요.", "error");
  try {
    const r = await api("/api/paper/stocks/orders", { method: "POST", body: { symbol, side, quantity } });
    setToast(`${r.name} ${side === "BUY" ? "매수" : "매도"} ${fmt(r.quantity)}주 체결 (@${won(r.price)})`, "ok");
    $("ps-preview").innerHTML = "";
    loadPaperStock();
  } catch (e) { setToast(e.message, "error"); }
}

async function loadStockPositions() {
  try {
    const { positions } = await api("/api/paper/stocks/positions");
    $("ps-positions").innerHTML = `<table><thead><tr><th>종목</th><th style="text-align:right">수량</th><th style="text-align:right">평균단가</th><th style="text-align:right">현재가</th><th style="text-align:right">평가금액</th><th style="text-align:right">손익</th><th></th></tr></thead>
      <tbody>${positions.length ? positions.map(p => `<tr>
        <td>${escHtml(p.name)}<div class="text-xs" style="color:var(--text-mute);">${escHtml(p.symbol)}</div></td>
        <td style="text-align:right">${fmt(p.quantity)}</td><td style="text-align:right">${won(p.avgPrice)}</td><td style="text-align:right">${won(p.currentPrice)}</td>
        <td style="text-align:right">${won(p.evalAmount)}</td><td style="text-align:right" class="${signCls(p.pnl)}">${won(p.pnl)}<div class="text-xs">${fmtPct(p.pnlRate)}</div></td>
        <td style="text-align:right"><button class="btn-secondary text-xs ps-sell-all" data-symbol="${escHtml(p.symbol)}" data-qty="${p.quantity}">전량매도</button></td></tr>`).join("") : emptyRow(7, "보유 종목이 없습니다.")}</tbody></table>`;
    $("ps-positions").querySelectorAll(".ps-sell-all").forEach(b => b.addEventListener("click", () => {
      $("ps-symbol").value = b.dataset.symbol; $("ps-qty").value = b.dataset.qty; stockOrder("SELL");
    }));
  } catch (e) { $("ps-positions").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function loadStockHistory() {
  try {
    const { history } = await api("/api/paper/stocks/orders/history?limit=30");
    $("ps-history").innerHTML = `<table><thead><tr><th>시각</th><th>매매</th><th>종목</th><th style="text-align:right">수량</th><th style="text-align:right">단가</th><th style="text-align:right">금액</th><th>출처</th></tr></thead>
      <tbody>${history.length ? history.map(o => `<tr><td class="text-xs">${ts(o.ts)}</td><td>${sideBadge(o.type)}</td><td>${escHtml(o.name)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(o.symbol)}</span></td>
        <td style="text-align:right">${fmt(o.quantity)}</td><td style="text-align:right">${won(o.price)}</td><td style="text-align:right">${won(o.amount)}</td><td class="text-xs">${escHtml(o.source)}</td></tr>`).join("") : emptyRow(7, "주문 내역이 없습니다.")}</tbody></table>`;
  } catch {}
}

/* ────────────────────────────────────────────────────────────────
 * 3. 코인 모의매매 (Upbit KRW 마켓)
 * ──────────────────────────────────────────────────────────────── */
async function loadPaperCrypto() {
  if (!cryptoMarkets.length) {
    try {
      const { markets } = await api("/api/paper/crypto/market-list");
      // 주요 코인을 앞에 두고 기본 선택은 비트코인
      const major = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA", "KRW-USDT"];
      cryptoMarkets = [...markets].sort((a, b) => {
        const ia = major.indexOf(a.market), ib = major.indexOf(b.market);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.koreanName.localeCompare(b.koreanName, "ko");
      });
      $("pc-market").innerHTML = cryptoMarkets.map(m => `<option value="${escHtml(m.market)}">${escHtml(m.koreanName)} (${escHtml(m.market)})</option>`).join("");
      if (cryptoMarkets.some(m => m.market === "KRW-BTC")) $("pc-market").value = "KRW-BTC";
    } catch (e) { setToast(e.message, "error"); }
  }
  cryptoTicker();
  loadCryptoHold();
  loadCryptoHistory();
  loadCryptoRankings();
}

async function cryptoTicker() {
  const code = $("pc-market").value;
  if (!code) return;
  try {
    const [{ tickers }, dom] = await Promise.all([
      api(`/api/paper/crypto/ticker?markets=${encodeURIComponent(code)}`),
      api(`/api/paper/crypto/${encodeURIComponent(code)}/domestic-prices`),
    ]);
    const t = tickers[0];
    const m = cryptoMarkets.find(x => x.market === code) || {};
    $("pc-ticker").innerHTML = t ? `<strong>${escHtml(m.koreanName || code)}</strong> <span class="text-xs" style="color:var(--text-mute);">${escHtml(code)}</span>
      <div class="text-xl font-bold mt-1">${won(t.price)} <span class="text-sm ${signCls(t.changeRate)}">${fmtPct(t.changeRate)}</span></div>
      <div class="text-xs" style="color:var(--text-mute);">24h 고가 ${won(t.high24h)} · 저가 ${won(t.low24h)} · 거래대금 ${fmt(t.accTradePrice24h / 1e8, 1)}억</div>` : `<span class="text-red-500">시세 없음</span>`;
    const p = dom.prices || {};
    $("pc-domestic").innerHTML = `<div class="text-xs font-semibold mb-1">국내 거래소 가격 비교 ${dom.spreadPct != null ? `<span style="color:var(--text-mute);">(최대 스프레드 ${dom.spreadPct}%)</span>` : ""}</div>
      <div class="grid grid-cols-3 gap-2 text-xs">${[["upbit", "Upbit"], ["bithumb", "Bithumb"], ["korbit", "Korbit"]].map(([k, label]) => `<div class="rounded-lg p-2" style="background:var(--surf);border:1px solid var(--border);"><div style="color:var(--text-dim);font-weight:600;">${label}</div><b>${p[k] ? won(p[k]) : "-"}</b></div>`).join("")}</div>`;
  } catch (e) { $("pc-ticker").innerHTML = `<span class="text-red-500">${escHtml(e.message)}</span>`; }
}

async function cryptoPreview(side) {
  const marketCode = $("pc-market").value;
  const body = { marketCode, side };
  if (side === "BUY") body.buyKrw = parseFloat(String($("pc-buy-krw").value).replace(/,/g, ""));
  else body.sellCount = parseFloat($("pc-sell-count").value);
  if (side === "BUY" ? !body.buyKrw : !body.sellCount) return setToast(side === "BUY" ? "매수 금액을 입력하세요." : "매도 수량을 입력하세요.", "error");
  try {
    const p = await api("/api/paper/trade/order/preview", { method: "POST", body });
    $("pc-preview").innerHTML = `<div class="rounded-xl border p-3 text-sm" style="border-color:${p.executable ? "var(--green-bd)" : "var(--red-bd)"}">
      <div class="flex items-center gap-2 mb-2">${sideBadge(p.side)} <strong>${escHtml(p.koreanName)}</strong> <span class="text-xs" style="color:var(--text-mute);">${escHtml(p.marketCode)}</span>
        <span class="ml-auto text-xs font-bold ${p.executable ? "text-emerald-600" : "text-red-600"}">${p.executable ? "체결 가능" : escHtml(p.reason || "체결 불가")}</span></div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs"><div>예상 단가<br><b>${won(p.estimatedPrice)}</b></div><div>수량<br><b>${fmt(p.quantity, 8)}</b></div><div>예상 금액<br><b>${won(p.estimatedAmount)}</b></div><div>현금 / 보유<br><b>${won(p.cashBefore)}</b> / ${fmt(p.heldQuantity, 8)}</div></div>
      <div class="text-xs mt-2" style="color:var(--text-mute);">${escHtml(p.notice)}</div></div>`;
  } catch (e) { setToast(e.message, "error"); }
}

async function cryptoOrder(side) {
  const marketCode = $("pc-market").value;
  try {
    let r;
    if (side === "BUY") {
      const buyKrw = parseFloat(String($("pc-buy-krw").value).replace(/,/g, ""));
      if (!buyKrw) return setToast("매수 금액을 입력하세요.", "error");
      r = await api("/api/paper/trade/order/buy", { method: "POST", body: { marketCode, buyKrw } });
    } else {
      const sellCount = parseFloat($("pc-sell-count").value);
      if (!sellCount) return setToast("매도 수량을 입력하세요.", "error");
      r = await api("/api/paper/trade/order/sell", { method: "POST", body: { marketCode, sellCount } });
    }
    setToast(`${marketCode} ${side === "BUY" ? "매수" : "매도"} 체결 · 수량 ${fmt(r.quantity, 8)} @${won(r.price)}`, "ok");
    $("pc-preview").innerHTML = "";
    loadCryptoHold(); loadCryptoHistory();
  } catch (e) { setToast(e.message, "error"); }
}

async function loadCryptoHold() {
  try {
    const h = await api("/api/paper/trade/hold");
    $("pc-cash").textContent = won(h.memberAsset);
    $("pc-hold").innerHTML = `<div class="text-xs mb-2" style="color:var(--text-mute);">매수 원금 ${won(h.totalBuyKrw)} · 평가금액 ${won(h.totalEvalKrw)}</div>
      <table><thead><tr><th>코인</th><th style="text-align:right">보유수량</th><th style="text-align:right">매수평균</th><th style="text-align:right">현재가</th><th style="text-align:right">평가금액</th><th style="text-align:right">손익</th><th></th></tr></thead>
      <tbody>${h.holdCryptoList.length ? h.holdCryptoList.map(c => `<tr><td>${escHtml(c.koreanName)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(c.marketCode)}</span></td>
        <td style="text-align:right">${fmt(c.holdCount, 8)}</td><td style="text-align:right">${won(c.buyAverage)}</td><td style="text-align:right">${won(c.currentPrice)}</td><td style="text-align:right">${won(c.evalKrw)}</td>
        <td style="text-align:right" class="${signCls(c.pnl)}">${won(c.pnl)}<div class="text-xs">${fmtPct(c.pnlRate)}</div></td>
        <td style="text-align:right"><button class="btn-secondary text-xs pc-sell-all" data-market="${escHtml(c.marketCode)}" data-qty="${c.holdCount}">전량매도</button></td></tr>`).join("") : emptyRow(7, "보유 코인이 없습니다.")}</tbody></table>`;
    $("pc-hold").querySelectorAll(".pc-sell-all").forEach(b => b.addEventListener("click", () => {
      $("pc-market").value = b.dataset.market; $("pc-sell-count").value = b.dataset.qty; cryptoOrder("SELL");
    }));
  } catch (e) { $("pc-hold").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function loadCryptoHistory() {
  try {
    const { history } = await api("/api/paper/trade/order/history?limit=30");
    $("pc-history").innerHTML = `<table><thead><tr><th>시각</th><th>매매</th><th>코인</th><th style="text-align:right">수량</th><th style="text-align:right">단가</th><th style="text-align:right">금액</th></tr></thead>
      <tbody>${history.length ? history.map(o => `<tr><td class="text-xs">${ts(o.ts)}</td><td>${sideBadge(o.type)}</td><td>${escHtml(o.koreanName)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(o.marketCode)}</span></td><td style="text-align:right">${fmt(o.quantity, 8)}</td><td style="text-align:right">${won(o.price)}</td><td style="text-align:right">${won(o.amount)}</td></tr>`).join("") : emptyRow(6, "거래 내역이 없습니다.")}</tbody></table>`;
  } catch {}
}

async function loadCryptoRankings() {
  try {
    const { rankings } = await api("/api/paper/crypto/rankings?limit=15");
    $("pc-rankings").innerHTML = `<table><thead><tr><th>#</th><th>코인</th><th style="text-align:right">현재가</th><th style="text-align:right">24h</th><th style="text-align:right">거래대금(24h)</th></tr></thead>
      <tbody>${rankings.map((r, i) => `<tr class="cursor-pointer pc-rank-row" data-market="${escHtml(r.market)}"><td>${i + 1}</td><td>${escHtml(r.koreanName)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(r.symbol)}</span></td><td style="text-align:right">${won(r.price)}</td><td style="text-align:right" class="${signCls(r.changeRate)}">${fmtPct(r.changeRate)}</td><td style="text-align:right">${fmt(r.accTradePrice24h / 1e8, 1)}억</td></tr>`).join("")}</tbody></table>`;
    $("pc-rankings").querySelectorAll(".pc-rank-row").forEach(r => r.addEventListener("click", () => { $("pc-market").value = r.dataset.market; cryptoTicker(); }));
  } catch {}
}

/* ────────────────────────────────────────────────────────────────
 * 4. 대체자산 (선물·옵션·파생·금속·부동산)
 * ──────────────────────────────────────────────────────────────── */
async function loadPaperAlt() {
  try {
    const { markets, notice } = await api("/api/paper/alternatives/markets");
    $("pa-notice").textContent = notice;
    const sel = $("pa-symbol");
    const prev = sel.value;
    sel.innerHTML = markets.map(m => `<option value="${escHtml(m.symbol)}">[${escHtml(m.category)}] ${escHtml(m.name)}</option>`).join("");
    if (prev) sel.value = prev;
    $("pa-markets").innerHTML = markets.map(m => `
      <div class="rounded-xl border border-white/10 bg-black/20 p-3 cursor-pointer pa-card" data-symbol="${escHtml(m.symbol)}">
        <div class="flex items-center justify-between"><span class="text-xs font-semibold" style="color:var(--accent);">${escHtml(m.category)}</span><span class="text-xs ${signCls(m.changeRate)}">${fmtPct(m.changeRate)}</span></div>
        <div class="font-semibold mt-1">${escHtml(m.name)}</div>
        <div class="text-lg font-bold">${won(m.price)} <span class="text-xs font-normal" style="color:var(--text-mute);">/ ${escHtml(m.unit)}</span></div>
        <div class="text-xs" style="color:var(--text-mute);">1단위 주문금액 ${won(m.tradeAmountPerUnit)}${m.marginRate < 100 ? ` (증거금 ${m.marginRate}%)` : ""}</div>
        <div class="text-xs mt-1" style="color:var(--text-mute);">${escHtml(m.source)}</div>
      </div>`).join("");
    $("pa-markets").querySelectorAll(".pa-card").forEach(c => c.addEventListener("click", () => { sel.value = c.dataset.symbol; altChartLoad(); }));
  } catch (e) { setToast(e.message, "error"); }
  altChartLoad();
  loadAltPositions();
  loadAltHistory();
}

async function altChartLoad() {
  const symbol = $("pa-symbol").value;
  if (!symbol) return;
  try {
    const { data } = await api(`/api/paper/alternatives/markets/${encodeURIComponent(symbol)}/chart?days=120`);
    const el = $("pa-chart");
    if (altChart) { altChart.destroy(); altChart = null; }
    if (!window.ApexCharts) { el.innerHTML = `<div class="text-xs" style="color:var(--text-mute);">차트 라이브러리 로드 실패</div>`; return; }
    altChart = new ApexCharts(el, {
      chart: { type: "candlestick", height: 260, toolbar: { show: false }, background: "transparent" },
      series: [{ data: data.map(d => ({ x: d.time, y: [d.open, d.high, d.low, d.close] })) }],
      xaxis: { type: "category", labels: { show: false } },
      yaxis: { labels: { formatter: v => fmt(v) } },
      theme: { mode: "light" },
      plotOptions: { candlestick: { colors: { upward: "#059669", downward: "#dc2626" } } },
    });
    altChart.render();
  } catch (e) { $("pa-chart").innerHTML = `<div class="text-xs text-red-500">${escHtml(e.message)}</div>`; }
}

async function altPreview(side) {
  const symbol = $("pa-symbol").value, quantity = parseInt($("pa-qty").value);
  if (!symbol || !quantity) return setToast("상품과 수량을 입력하세요.", "error");
  try {
    const p = await api("/api/paper/alternatives/orders/preview", { method: "POST", body: { symbol, side, quantity } });
    $("pa-preview").innerHTML = `<div class="rounded-xl border p-3 text-sm" style="border-color:${p.executable ? "var(--green-bd)" : "var(--red-bd)"}">
      <div class="flex items-center gap-2 mb-2">${sideBadge(p.side)} <strong>${escHtml(p.name)}</strong> <span class="text-xs" style="color:var(--text-mute);">${escHtml(p.category)}</span>
        <span class="ml-auto text-xs font-bold ${p.executable ? "text-emerald-600" : "text-red-600"}">${p.executable ? "체결 가능" : escHtml(p.reason || "체결 불가")}</span></div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs"><div>기준가<br><b>${won(p.estimatedPrice)}</b></div><div>주문금액${p.marginRate < 100 ? ` (증거금 ${p.marginRate}%)` : ""}<br><b>${won(p.estimatedAmount)}</b></div><div>보유 현금<br><b>${won(p.cashBefore)}</b></div><div>보유 수량<br><b>${fmt(p.heldQuantity)}</b></div></div>
      <div class="text-xs mt-2" style="color:var(--text-mute);">${escHtml(p.notice)}</div></div>`;
  } catch (e) { setToast(e.message, "error"); }
}

async function altOrder(side) {
  const symbol = $("pa-symbol").value, quantity = parseInt($("pa-qty").value);
  if (!symbol || !quantity) return setToast("상품과 수량을 입력하세요.", "error");
  try {
    const r = await api("/api/paper/alternatives/orders", { method: "POST", body: { symbol, side, quantity } });
    setToast(`${r.symbol} ${side === "BUY" ? "매수" : "매도"} ${fmt(r.quantity)}단위 체결 · 금액 ${won(r.amount)}`, "ok");
    $("pa-preview").innerHTML = "";
    loadAltPositions(); loadAltHistory();
  } catch (e) { setToast(e.message, "error"); }
}

async function loadAltPositions() {
  try {
    const { positions, totalEvalAmount } = await api("/api/paper/alternatives/positions?volatility=1");
    $("pa-positions").innerHTML = `<div class="text-xs mb-2" style="color:var(--text-mute);">총 평가금액 ${won(totalEvalAmount)}</div>
      <table><thead><tr><th>상품</th><th style="text-align:right">수량</th><th style="text-align:right">평균가</th><th style="text-align:right">현재가</th><th style="text-align:right">평가금액</th><th style="text-align:right">손익</th><th style="text-align:right">변동성</th><th></th></tr></thead>
      <tbody>${positions.length ? positions.map(p => `<tr><td>${escHtml(p.name)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(p.category)}</span></td><td style="text-align:right">${fmt(p.quantity)} ${escHtml(p.unit)}</td><td style="text-align:right">${won(p.avgPrice)}</td><td style="text-align:right">${won(p.currentPrice)}</td><td style="text-align:right">${won(p.evalAmount)}</td><td style="text-align:right" class="${signCls(p.pnl)}">${won(p.pnl)}</td><td style="text-align:right">${p.volatility != null ? p.volatility + "%" : "-"}</td>
        <td style="text-align:right"><button class="btn-secondary text-xs pa-sell-all" data-symbol="${escHtml(p.symbol)}" data-qty="${p.quantity}">전량매도</button></td></tr>`).join("") : emptyRow(8, "보유 포지션이 없습니다.")}</tbody></table>`;
    $("pa-positions").querySelectorAll(".pa-sell-all").forEach(b => b.addEventListener("click", () => {
      $("pa-symbol").value = b.dataset.symbol; $("pa-qty").value = b.dataset.qty; altOrder("SELL");
    }));
  } catch (e) { $("pa-positions").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function loadAltHistory() {
  try {
    const { history } = await api("/api/paper/alternatives/orders/history?limit=30");
    $("pa-history").innerHTML = `<table><thead><tr><th>시각</th><th>매매</th><th>상품</th><th style="text-align:right">수량</th><th style="text-align:right">단가</th><th style="text-align:right">금액</th></tr></thead>
      <tbody>${history.length ? history.map(o => `<tr><td class="text-xs">${ts(o.ts)}</td><td>${sideBadge(o.type)}</td><td>${escHtml(o.name)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(o.category)}</span></td><td style="text-align:right">${fmt(o.quantity)}</td><td style="text-align:right">${won(o.price)}</td><td style="text-align:right">${won(o.amount)}</td></tr>`).join("") : emptyRow(6, "주문 내역이 없습니다.")}</tbody></table>`;
  } catch {}
}

/* ────────────────────────────────────────────────────────────────
 * 5. Open API 키 + 외부 연동 문서 + Alpaca 연결 테스트
 * ──────────────────────────────────────────────────────────────── */
async function loadPaperOpenApi() {
  loadApiKeys();
  try {
    const d = await api("/openapi/v1/docs-summary");
    const origin = window.location.origin;
    $("po-docs").innerHTML = `
      <div class="text-xs mb-2" style="color:var(--text-mute);">Base URL <code>${escHtml(origin + d.baseUrl)}</code> · 인증 <code>${escHtml(d.auth)}</code> · 제한 ${escHtml(d.rateLimit)}</div>
      <table><thead><tr><th>Method</th><th>Path</th><th>설명</th></tr></thead><tbody>
        ${d.endpoints.map(e => `<tr><td><span class="${e.method === "POST" ? "badge-buy" : "badge-hold"}">${e.method}</span></td><td><code class="text-xs">${escHtml(e.path)}</code></td><td class="text-sm">${escHtml(e.desc)}</td></tr>`).join("")}
      </tbody></table>
      <pre class="text-xs mt-3 p-3 rounded-lg overflow-x-auto" style="background:var(--surf2);border:1px solid var(--border);">curl -H "Authorization: Bearer &lt;API_KEY&gt;" ${escHtml(origin)}/openapi/v1/quote/005930
curl -X POST -H "Authorization: Bearer &lt;API_KEY&gt;" -H "Content-Type: application/json" \\
     -d '{"symbol":"005930","side":"BUY","quantity":10}' ${escHtml(origin)}/openapi/v1/orders</pre>`;
  } catch (e) { $("po-docs").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function loadApiKeys() {
  try {
    const { keys } = await api("/api/paper/api-keys");
    $("po-keys").innerHTML = `<table><thead><tr><th>라벨</th><th>키 앞부분</th><th>상태</th><th style="text-align:right">호출수</th><th>마지막 사용</th><th>발급일</th><th></th></tr></thead>
      <tbody>${keys.length ? keys.map(k => `<tr><td>${escHtml(k.label)}</td><td><code class="text-xs">${escHtml(k.keyPrefix)}…</code></td><td>${k.isActive ? `<span class="badge-buy">활성</span>` : `<span class="badge-hold">폐기</span>`}</td><td style="text-align:right">${fmt(k.callCount)}</td><td class="text-xs">${k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleString("ko-KR") : "-"}</td><td class="text-xs">${new Date(k.createdAt).toLocaleDateString("ko-KR")}</td>
        <td style="text-align:right">${k.isActive ? `<button class="btn-secondary text-xs po-revoke" data-id="${escHtml(k.id)}">폐기</button>` : ""}</td></tr>`).join("") : emptyRow(7, "발급된 API 키가 없습니다.")}</tbody></table>`;
    $("po-keys").querySelectorAll(".po-revoke").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("이 키를 폐기하면 외부 시스템에서 더 이상 사용할 수 없습니다. 계속할까요?")) return;
      try { await api(`/api/paper/api-keys/${b.dataset.id}`, { method: "DELETE" }); setToast("키를 폐기했습니다.", "ok"); loadApiKeys(); }
      catch (e) { setToast(e.message, "error"); }
    }));
  } catch (e) { $("po-keys").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
}

async function createApiKey() {
  const label = $("po-label").value.trim() || "My API Key";
  try {
    const k = await api("/api/paper/api-keys", { method: "POST", body: { label } });
    $("po-new-key").innerHTML = `<div class="rounded-xl border p-3 text-sm" style="border-color:var(--green-bd);">
      <div class="font-semibold mb-1">새 API 키가 발급되었습니다. 지금만 표시되니 안전한 곳에 복사하세요.</div>
      <code class="text-xs break-all" id="po-new-key-value">${escHtml(k.apiKey)}</code>
      <button class="btn-secondary text-xs ml-2" id="po-copy-key">복사</button></div>`;
    $("po-copy-key").addEventListener("click", () => navigator.clipboard?.writeText(k.apiKey).then(() => setToast("복사했습니다.", "ok")));
    $("po-label").value = "";
    loadApiKeys();
  } catch (e) { setToast(e.message, "error"); }
}

async function alpacaTest(kind) {
  const body = { api_key: $("po-alpaca-key").value.trim(), secret_key: $("po-alpaca-secret").value.trim() };
  $("po-alpaca-result").innerHTML = `<span style="color:var(--text-mute);">Alpaca Paper API 호출 중…</span>`;
  try {
    const r = await api(`/api/paper/alpaca/${kind}`, { method: "POST", body });
    $("po-alpaca-result").innerHTML = `<pre class="text-xs p-3 rounded-lg overflow-x-auto" style="background:var(--surf2);border:1px solid var(--border);">${escHtml(JSON.stringify(r, null, 2))}</pre>`;
  } catch (e) { $("po-alpaca-result").innerHTML = `<span class="text-red-500">${escHtml(e.message)}</span>`; }
}

/* ────────────────────────────────────────────────────────────────
 * 6. QuantConnect LEAN 백테스트
 * ──────────────────────────────────────────────────────────────── */
const LEAN_EXAMPLES = [
  { id: "bh", tag: "기준선", title: "삼성전자 매수 후 보유", ticker: "005930.KS", strategy: "buy_hold", start: "2023-01-01", cmpStart: "2022-01-01",
    focus: "아무 규칙 없이 첫날 사서 끝까지 들고 있으면 어떤 결과인가?", read: "다른 전략의 수익률·MDD를 이 값과 비교합니다." },
  { id: "ma", tag: "추세", title: "골든/데드크로스 (20·60일)", ticker: "005930.KS", strategy: "ma_cross", start: "2023-01-01", cmpStart: "2022-01-01",
    focus: "이동평균 교차 규칙이 횡보장에서 잦은 매매로 손해를 보는지 확인", read: "규칙 변경 횟수와 시장 노출 일수를 함께 보세요." },
  { id: "dca", tag: "적립", title: "SPY 월 1회 정액 매수", ticker: "SPY", strategy: "dca", start: "2022-01-01", cmpStart: "2021-01-01",
    focus: "하락장에서 분할 매수가 낙폭을 얼마나 줄이는지", read: "MDD가 매수 후 보유보다 작아지는지 확인합니다." },
  { id: "mom", tag: "모멘텀", title: "SK하이닉스 20일 돌파", ticker: "000660.KS", strategy: "momentum", start: "2023-01-01", cmpStart: "2022-01-01",
    focus: "신고가 돌파 진입이 강한 추세에서 수익을 지키는지", read: "돌파 후 되돌림으로 손절이 반복되는지 거래 횟수를 확인합니다." },
];

async function loadLeanView() {
  try {
    const s = await api("/api/backtests/lean/status");
    const modeLabel = { ssh: `원격 서버(${s.ssh_host}) Docker`, docker: `로컬 Docker (${s.docker_via})`, local: "LEAN 미실행 · pandas 계산만" }[s.mode] || s.mode;
    $("ql-status").innerHTML = `<span class="${s.engine_runs ? "badge-buy" : "badge-hold"}">${s.engine_runs ? "LEAN 엔진 연결됨" : "LEAN 미연결"}</span>
      <span class="text-xs ml-2" style="color:var(--text-mute);">실행 모드: ${escHtml(modeLabel)} · 이미지 ${escHtml(s.image)} · 참조데이터 ${s.reference_data_ok ? "OK" : "누락"}</span>`;
    const sel = $("ql-strategy");
    if (!sel.options.length) {
      sel.innerHTML = s.strategies.map(x => `<option value="${x.value}">${escHtml(x.label)}</option>`).join("");
      sel.dataset.hints = JSON.stringify(Object.fromEntries(s.strategies.map(x => [x.value, x.hint])));
      leanStrategyHint();
    }
  } catch (e) { $("ql-status").innerHTML = `<span class="text-red-500">${escHtml(e.message)}</span>`; }
  if (!$("ql-examples").children.length) {
    $("ql-examples").innerHTML = LEAN_EXAMPLES.map(e => `<button type="button" class="rounded-xl border border-white/10 bg-black/20 p-3 text-left ql-example" data-id="${e.id}">
      <div class="text-xs font-semibold" style="color:var(--accent);">${escHtml(e.tag)}</div><div class="font-semibold">${escHtml(e.title)}</div>
      <div class="text-xs mt-1" style="color:var(--text-mute);">${escHtml(e.focus)}</div></button>`).join("");
    $("ql-examples").querySelectorAll(".ql-example").forEach(b => b.addEventListener("click", () => applyLeanExample(b.dataset.id)));
    const today = new Date().toISOString().slice(0, 10);
    $("ql-end").value = today; $("ql-cmp-end").value = today;
  }
  loadLeanHistory();
}

function leanStrategyHint() {
  const sel = $("ql-strategy");
  const hints = JSON.parse(sel.dataset.hints || "{}");
  $("ql-strategy-hint").textContent = hints[sel.value] || "";
  const v = sel.value;
  $("ql-p-ma").style.display = v === "ma_cross" ? "" : "none";
  $("ql-p-dca").style.display = v === "dca" ? "" : "none";
  $("ql-p-mom").style.display = v === "momentum" ? "" : "none";
}

function applyLeanExample(id) {
  const e = LEAN_EXAMPLES.find(x => x.id === id);
  if (!e) return;
  $("ql-ticker").value = e.ticker; $("ql-strategy").value = e.strategy;
  $("ql-start").value = e.start; $("ql-cmp-start").value = e.cmpStart;
  leanStrategyHint();
  $("ql-example-detail").innerHTML = `<div class="rounded-xl border border-white/10 bg-black/20 p-3 text-sm"><div class="font-semibold mb-1">${escHtml(e.title)}</div><div><b>질문:</b> ${escHtml(e.focus)}</div><div><b>읽는 법:</b> ${escHtml(e.read)}</div></div>`;
}

async function runLeanBacktest() {
  const btn = $("ql-run");
  const payload = {
    ticker: $("ql-ticker").value.trim(), strategy: $("ql-strategy").value,
    start_date: $("ql-start").value, end_date: $("ql-end").value,
    compare_start_date: $("ql-cmp-start").value, compare_end_date: $("ql-cmp-end").value,
    initial_cash: parseFloat($("ql-cash").value) || 10000,
    short_window: parseInt($("ql-short").value) || 20, long_window: parseInt($("ql-long").value) || 60,
    dca_interval_days: parseInt($("ql-dca").value) || 21, breakout_window: parseInt($("ql-breakout").value) || 20,
  };
  if (!payload.ticker || !payload.start_date || !payload.end_date) return setToast("티커와 기간을 입력하세요.", "error");
  btn.disabled = true;
  $("ql-result").innerHTML = `<div class="text-sm" style="color:var(--text-mute);"><i class="fa-solid fa-spinner fa-spin"></i> Yahoo Finance 데이터를 정리하고 LEAN 컨테이너를 실행하고 있습니다. (최대 수 분 소요)</div>`;
  try {
    const d = await api("/api/backtests/lean/run", { method: "POST", body: payload });
    renderLeanResult(d);
    loadLeanHistory();
  } catch (e) { $("ql-result").innerHTML = `<div class="text-sm text-red-500">${escHtml(e.message)}</div>`; }
  finally { btn.disabled = false; }
}

function renderLeanResult(d) {
  const up = v => v >= 0 ? "text-emerald-600" : "text-red-600";
  const stats = d.lean_statistics || {};
  const statRows = Object.keys(stats).length ? Object.entries(stats).map(([k, v]) => `<tr><td class="text-xs">${escHtml(k)}</td><td class="text-xs" style="text-align:right">${escHtml(v)}</td></tr>`).join("") : emptyRow(2, d.lean_ok ? "통계 없음" : "LEAN 엔진 결과 없음 (로그 확인)");
  $("ql-result").innerHTML = `
    <div class="flex items-center gap-2 flex-wrap mb-3"><span class="badge-hold">${escHtml(d.engine)}</span><span class="text-sm font-semibold">${escHtml(d.ticker)} · ${escHtml(d.strategy_label)}</span>
      <span class="text-xs" style="color:var(--text-mute);">${escHtml(d.start_date)} ~ ${escHtml(d.end_date)} (비교 ${escHtml(d.compare_start_date)} ~ ${escHtml(d.compare_end_date)})</span>
      ${d.lean_ok ? `<span class="badge-buy">LEAN 실행 완료</span>` : `<span class="badge-sell">LEAN 미실행/실패</span>`}
      <button type="button" class="btn-secondary text-xs ml-auto" id="ql-add-compare">비교에 추가</button></div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
      ${kpi("전략 수익률", fmtPct(d.strategy_return_pct), up(d.strategy_return_pct))}
      ${kpi("단순 보유 수익률", fmtPct(d.benchmark_return_pct), up(d.benchmark_return_pct))}
      ${kpi("비교 기간 보유 수익률", fmtPct(d.comparison_return_pct), up(d.comparison_return_pct))}
      ${kpi("초과 수익 (vs 비교기간)", fmtPct(d.outperformance_pct), up(d.outperformance_pct))}
      ${kpi("최대 낙폭 (MDD)", `${d.max_drawdown_pct}%`, "text-red-600")}
      ${kpi("연환산 수익률", fmtPct(d.annualized_return_pct), up(d.annualized_return_pct))}
      ${kpi("연환산 변동성", `${d.annualized_volatility_pct}%`)}
      ${kpi("샤프 비율", d.sharpe_ratio)}
      ${kpi("시장 노출 일수", `${d.invested_days_pct}%`)}
      ${kpi("규칙 변경 횟수", `${d.trade_count}회`)}
      ${kpi("현재 추세", escHtml(d.market_snapshot?.trend || "-"))}
      ${kpi("52주 범위 내 위치", `${d.market_snapshot?.range_252d_position_pct ?? "-"}%`)}
    </div>
    <div id="ql-chart" class="mb-3"></div>
    <div class="grid md:grid-cols-2 gap-3">
      <div><h4 class="text-sm font-semibold mb-1">LEAN 엔진 통계 (summary.json)</h4><table><tbody>${statRows}</tbody></table></div>
      <div><h4 class="text-sm font-semibold mb-1">시장 스냅샷 (${escHtml(d.market_snapshot?.as_of || "")})</h4>
        <table><tbody>
          <tr><td class="text-xs">마지막 종가</td><td class="text-xs" style="text-align:right">${fmt(d.market_snapshot?.last_price, 2)}</td></tr>
          <tr><td class="text-xs">20일 수익률</td><td class="text-xs" style="text-align:right">${d.market_snapshot?.return_20d_pct ?? "-"}%</td></tr>
          <tr><td class="text-xs">MA20 / MA60</td><td class="text-xs" style="text-align:right">${fmt(d.market_snapshot?.ma20, 2)} / ${fmt(d.market_snapshot?.ma60, 2)}</td></tr>
        </tbody></table></div>
    </div>
    <p class="text-xs mt-3" style="color:var(--text-mute);">${escHtml(d.disclaimer)}</p>
    <details class="mt-2"><summary class="text-sm cursor-pointer">LEAN 실행 로그</summary><pre class="text-xs p-3 rounded-lg overflow-x-auto" style="background:var(--surf2);border:1px solid var(--border);max-height:320px;">${escHtml(d.lean_log || "로그 없음")}</pre></details>
    <details class="mt-2"><summary class="text-sm cursor-pointer">생성된 LEAN 알고리즘 (main.py)</summary><pre class="text-xs p-3 rounded-lg overflow-x-auto" style="background:var(--surf2);border:1px solid var(--border);max-height:360px;">${escHtml(d.algorithm_source || "")}</pre></details>`;
  $("ql-add-compare").addEventListener("click", () => window.compareTrayAdd?.({
    source: "LEAN 백테스트", label: `${d.ticker} · ${d.strategy_label}`,
    summary: `수익률 ${fmtPct(d.strategy_return_pct)} · MDD ${d.max_drawdown_pct}% · 샤프 ${d.sharpe_ratio}`,
  }));
  if (leanChart) { leanChart.destroy(); leanChart = null; }
  if (window.ApexCharts) {
    leanChart = new ApexCharts($("ql-chart"), {
      chart: { type: "line", height: 280, toolbar: { show: false }, background: "transparent", zoom: { enabled: false } },
      series: [
        { name: d.strategy_label, data: d.points.map(p => ({ x: p.date, y: p.value })) },
        { name: "단순 보유", data: (d.benchmark_points || []).map(p => ({ x: p.date, y: p.value })) },
      ],
      stroke: { width: [3, 2], curve: "smooth", dashArray: [0, 4] },
      colors: ["#2563eb", "#94a3b8"],
      xaxis: { type: "category", tickAmount: 8, labels: { rotate: 0 } },
      yaxis: { labels: { formatter: v => fmt(v) }, title: { text: "자산 가치" } },
      theme: { mode: "light" }, legend: { position: "top" },
    });
    leanChart.render();
  }
}

async function loadLeanHistory() {
  try {
    const { runs } = await api("/api/backtests/lean/history?limit=15");
    $("ql-history").innerHTML = `<table><thead><tr><th>실행 시각</th><th>티커</th><th>전략</th><th>기간</th><th style="text-align:right">수익률</th><th style="text-align:right">MDD</th><th style="text-align:right">샤프</th><th>엔진</th></tr></thead>
      <tbody>${runs.length ? runs.map(r => `<tr><td class="text-xs">${new Date(r.created_at).toLocaleString("ko-KR")}</td><td>${escHtml(r.ticker)}</td><td class="text-xs">${escHtml(r.strategy_label)}</td><td class="text-xs">${escHtml(r.start_date)}~${escHtml(r.end_date)}</td>
        <td style="text-align:right" class="${signCls(r.strategy_return_pct)}">${fmtPct(r.strategy_return_pct)}</td><td style="text-align:right">${r.max_drawdown_pct}%</td><td style="text-align:right">${r.sharpe_ratio}</td><td>${r.lean_ok ? `<span class="badge-buy">LEAN</span>` : `<span class="badge-hold">pandas</span>`}</td></tr>`).join("") : emptyRow(8, "실행 이력이 없습니다.")}</tbody></table>`;
  } catch {}
}

/* ────────────────────────────────────────────────────────────────
 * 공개 API: app.html 메인 모듈에서 호출
 * ──────────────────────────────────────────────────────────────── */
export function initPaperViews() {
  const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
  on("pd-reset", "click", resetPaperAccount);
  on("pd-refresh", "click", loadPaperDashboard);

  on("ps-quote-btn", "click", stockQuote);
  on("ps-symbol", "keydown", e => { if (e.key === "Enter") stockQuote(); });
  on("ps-preview-buy", "click", () => stockPreview("BUY"));
  on("ps-preview-sell", "click", () => stockPreview("SELL"));
  on("ps-buy", "click", () => stockOrder("BUY"));
  on("ps-sell", "click", () => stockOrder("SELL"));

  on("pc-market", "change", cryptoTicker);
  on("pc-refresh", "click", cryptoTicker);
  on("pc-preview-buy", "click", () => cryptoPreview("BUY"));
  on("pc-preview-sell", "click", () => cryptoPreview("SELL"));
  on("pc-buy", "click", () => cryptoOrder("BUY"));
  on("pc-sell", "click", () => cryptoOrder("SELL"));

  on("pa-symbol", "change", altChartLoad);
  on("pa-preview-buy", "click", () => altPreview("BUY"));
  on("pa-preview-sell", "click", () => altPreview("SELL"));
  on("pa-buy", "click", () => altOrder("BUY"));
  on("pa-sell", "click", () => altOrder("SELL"));

  on("po-create", "click", createApiKey);
  on("po-alpaca-account", "click", () => alpacaTest("account"));
  on("po-alpaca-positions", "click", () => alpacaTest("positions"));

  on("ql-strategy", "change", leanStrategyHint);
  on("ql-run", "click", runLeanBacktest);
}

export function onPaperViewActivated(view) {
  if (view === "paper-dashboard") loadPaperDashboard();
  if (view === "paper-stock") loadPaperStock();
  if (view === "paper-crypto") loadPaperCrypto();
  if (view === "paper-alternative") loadPaperAlt();
  if (view === "paper-openapi") loadPaperOpenApi();
  if (view === "quant-lean") loadLeanView();
}
