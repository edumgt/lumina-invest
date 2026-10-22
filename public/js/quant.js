import { setIconText } from "/js/icons.js";
/* 퀀트자동매매: 대시보드, 자동매매·위험관리 상태, 전략 분석
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";
import { compareTrayAdd, renderCompareTrayAll } from "/js/core.js";

let quantChartInstance = null;
let quantChartRequest = 0;
let quantChartAbort = null;
// ── 퀀트 대시보드 ────────────────────────────────────────────────
async function loadQuantDashboard() {
  loadMarketCards();
  loadSignalCards();
  loadQuantChart();
}

async function loadMarketCards() {
  try {
    const { indices } = await api("/api/stocks/market");
    document.getElementById("market-cards").innerHTML = indices.map(idx => {
      const pct = idx.change_pct;
      const sign = pct != null && pct >= 0 ? "+" : "";
      const cls = pct != null && pct >= 0 ? "text-emerald-400" : "text-red-400";
      return `<div class="rounded-xl border border-white/10 bg-black/20 p-3 text-center">
        <div class="text-xs text-slate-400">${escHtml(idx.name)}</div>
        <div class="text-lg font-semibold mt-1">${fmt(idx.price)}</div>
        <div class="${cls} text-xs">${sign}${pct?.toFixed(2) ?? "-"}%</div>
      </div>`;
    }).join("");
  } catch {}
}

async function loadSignalCards() {
  const el = document.getElementById("signal-cards");
  el.innerHTML = "<div class='col-span-5 text-slate-400 text-xs'>시그널 로딩 중...</div>";
  try {
    const { stocks: candidates = [], indicators = {}, signal_policy: policy = {} } = await api("/api/stocks/universe/candidates?limit=10");
    const stocks = candidates.filter(s => indicators[s.symbol]?.signal?.action === "강력 매수").slice(0, 10);
    const title = document.getElementById("quant-signal-title");
    if (title) title.textContent = `대표 강력 매수 시그널 · ${stocks.length}종목`;
    const note = document.getElementById("quant-signal-note");
    if (note) note.textContent = `강력 매수 후보 중 점수·거래대금 순으로 최대 10종목을 표시합니다. 확정 일봉의 RSI·MA5/MA20·볼린저 규칙 점수입니다. 강력 매수는 +${policy.strong_buy_threshold ?? 3}점 이상이며 수익 확률을 뜻하지 않습니다. KIS ${policy.execution_environment === "paper" ? "Testbed" : "배치"}는 ${policy.order_universe_restricted === false ? `전체 ${policy.order_universe_count ?? ""}종목에서 적격 후보를 선정하고, ` : "주문 허용 범위 안에서 "}${policy.execution_interval || "별도 설정"} 기준${policy.max_buys_per_cycle != null ? ` · 사이클당 매수 최대 ${policy.max_buys_per_cycle}종목` : ""}으로 판단하고 위험관리를 통과해야 주문합니다.`;
    if (!stocks.length) {
      el.innerHTML = "<div class='col-span-5 text-slate-400 text-xs'>현재 데이터 기준을 충족한 강력 매수 후보가 없습니다.</div>";
      return;
    }
    el.innerHTML = stocks.map((s, i) => {
      const r = indicators[s.symbol];
      const sig = r?.signal || {};
      const price = r?.current_price;
      const rsiV = r?.current_rsi;
      const colorMap = { "강력 매수": "border-emerald-500/50 bg-emerald-500/10",
        "매수": "border-emerald-500/30 bg-emerald-500/5",
        "강력 매도": "border-red-500/50 bg-red-500/10",
        "매도": "border-red-500/30 bg-red-500/5",
        "관망": "border-white/10 bg-white/5" };
      const cls = colorMap[sig.action] || "border-white/10 bg-white/5";
      return `<div class="rounded-xl border p-2 ${cls}" role="button" tabindex="0" style="cursor:pointer;" data-quant-symbol="${escHtml(s.symbol)}" data-quant-name="${escHtml(s.name)}" aria-controls="quant-chart-container" aria-pressed="false" aria-label="${escHtml(s.name)} (${escHtml(s.symbol)}) 차트 보기">
        <div class="font-semibold">${escHtml(s.name)}</div>
        <div class="text-slate-400 text-xs">${escHtml(s.symbol)}</div>
        <div class="mt-1 font-medium">${fmt(price)}</div>
        <div class="mt-1 text-xs font-semibold">${escHtml(sig.action || "판단 불가")} · ${sig.score == null ? "-" : `${sig.score > 0 ? "+" : ""}${escHtml(sig.score)}점`}</div>
        <ul class="signal-reasons mt-2 text-xs" style="padding-left:14px;list-style:disc;line-height:1.5;">${(sig.components?.length ? sig.components.map(c => `${c.reason} (${c.points > 0 ? "+" : ""}${c.points}점)`) : sig.reasons || [r ? "판단 근거 없음" : "지표 데이터 없음"]).map(reason => `<li>${escHtml(reason)}</li>`).join("")}</ul>
        <div class="mt-2 text-xs" style="color:var(--text-dim);">${s.kis_order_allowed === false ? "KIS 주문 허용 목록 밖 · 배치 매수 제외" : s.kis_order_allowed === true ? "KIS 허용 종목 · 별도 전략·우선순위·위험관리 판단 필요" : "KIS 주문 대상 여부 미확인"}</div>
        <div class="text-xs text-slate-500">MA5 ${r?.ma5?.at(-1)?.toLocaleString("ko-KR", {maximumFractionDigits: 2}) ?? "-"} / MA20 ${r?.ma20?.at(-1)?.toLocaleString("ko-KR", {maximumFractionDigits: 2}) ?? "-"}</div>
        <div class="text-xs text-slate-500">RSI: ${rsiV?.toFixed(1) ?? "-"}</div><div class="text-xs text-slate-500">일봉 기준 ${r?.as_of ? escHtml(new Date(r.as_of * 1000).toISOString().slice(0,10)) : "-"} · ${escHtml(r?.source || "미수집")}</div>
      </div>`;
    }).join("");
    syncQuantSignalSelection();
  } catch (e) {
    el.innerHTML = `<div class='col-span-5 text-red-400 text-xs'>${escHtml(e.message)}</div>`;
  }
}

function syncQuantSignalSelection() {
  const symbol = document.getElementById("quant-symbol").value;
  document.querySelectorAll("#signal-cards [data-quant-symbol]").forEach(card => {
    const selected = card.dataset.quantSymbol === symbol;
    card.setAttribute("aria-pressed", String(selected));
    card.style.boxShadow = selected ? "inset 0 0 0 2px var(--accent)" : "";
  });
}

function selectQuantSignal(card) {
  document.getElementById("quant-symbol").value = card.dataset.quantSymbol;
  const button = document.getElementById("quant-symbol-btn");
  button.querySelector(".ssm-btn-label").textContent = card.dataset.quantName;
  button.querySelector(".ssm-btn-ticker").textContent = card.dataset.quantSymbol;
  syncQuantSignalSelection();
  document.getElementById("quant-chart-container").closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
  loadQuantChart();
}

const signalCards = document.getElementById("signal-cards");
signalCards.addEventListener("click", event => {
  const card = event.target.closest("[data-quant-symbol]");
  if (card) selectQuantSignal(card);
});
signalCards.addEventListener("keydown", event => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-quant-symbol]");
  if (card) { event.preventDefault(); selectQuantSignal(card); }
});

async function loadQuantChart() {
  const symbol = document.getElementById("quant-symbol").value;
  const name = document.querySelector("#quant-symbol-btn .ssm-btn-label").textContent;
  const container = document.getElementById("quant-chart-container");
  const request = ++quantChartRequest;
  quantChartAbort?.abort();
  quantChartAbort = new AbortController();
  if (quantChartInstance) { quantChartInstance.destroy(); quantChartInstance = null; }
  container.textContent = `${name} 차트 불러오는 중...`;
  container.setAttribute("aria-busy", "true");
  syncQuantSignalSelection();
  try {
    const data = await api(`/api/stocks/quant/indicators?symbol=${encodeURIComponent(symbol)}&period=2y`, { signal: quantChartAbort.signal });
    if (request !== quantChartRequest || symbol !== document.getElementById("quant-symbol").value) return;
    if (data.error) { container.textContent = data.error; return; }
    if (!data.times?.length || !data.closes?.length) { container.textContent = "차트 데이터가 없습니다."; return; }
    container.innerHTML = "";

    const times = data.times || [];
    const toMs = t => new Date(t * 1000).getTime();
    const mapS = (arr) => arr.map((v, i) => [toMs(times[i]), v != null ? +v.toFixed(2) : null]).filter(p => p[1] != null);

    quantChartInstance = new ApexCharts(container, {
      chart: { type: "line", height: 380, background: "#fff", toolbar: { show: true },
               animations: { enabled: false }, fontFamily: "Pretendard, sans-serif",
               zoom: { enabled: true } },
      series: [
        { name: "종가",   data: mapS(data.closes)   },
        { name: "MA20",   data: mapS(data.ma20 || []) },
        { name: "MA60",   data: mapS(data.ma60 || []) },
        { name: "BB 상단", data: mapS(data.bb_upper || []) },
        { name: "BB 하단", data: mapS(data.bb_lower || []) },
      ],
      colors: ["#6366f1", "#f59e0b", "#10b981", "#94a3b8", "#94a3b8"],
      stroke: { width: [2, 1.5, 1.5, 1, 1], dashArray: [0, 0, 0, 4, 4], curve: "smooth" },
      xaxis: { type: "datetime", labels: { style: { colors: "#434651" } } },
      yaxis: { labels: { style: { colors: "#434651" }, formatter: v => v?.toLocaleString() } },
      grid: { borderColor: "#e0e3eb" },
      legend: { show: true, position: "top" },
      tooltip: { shared: true, x: { format: "yyyy-MM-dd" }, theme: "light" },
    });
    await quantChartInstance.render();
  } catch (e) {
    if (request !== quantChartRequest || e.name === "AbortError") return;
    container.textContent = "차트 오류: " + e.message;
  } finally {
    if (request === quantChartRequest) container.setAttribute("aria-busy", "false");
  }
}

document.getElementById("quant-chart-load").addEventListener("click", loadQuantChart);
document.getElementById("refresh-market").addEventListener("click", loadQuantDashboard);

// ── 자동매매 ─────────────────────────────────────────────────────
async function loadAutoTradeStatus() {
  loadRiskStatus(); // 로그가 없어 아래에서 조기 return 되더라도 위험관리 상태는 항상 표시
  try {
    const s = await api("/api/auto-trade/status");
    setIconText(document.getElementById("auto-trade-status"), (s.running ? "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i> 실행 중" : "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i> 정지") + (s.scheduler ? ` · ${s.scheduler}` : "") + (s.last_cycle_at ? ` · 마지막 사이클 ${s.last_cycle_at} UTC` : ""));
    const el = document.getElementById("auto-trade-log");
    if (!s.log?.length) { el.innerHTML = "<div class='text-slate-400'>자동매매 로그가 없습니다.</div>"; return; }
    el.innerHTML = [...s.log].reverse().map(cycle => {
      const trades = (cycle.trades || []).map(t =>
        t.type === "risk" || t.status === "skipped"
          ? `<div class="ml-3 text-amber-400"><i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i> ${t.action === "buy" ? "매수" : "매도"} 생략 ${escHtml(t.name)} — ${escHtml(t.reason || "")}</div>`
          : `<div class="ml-3 text-slate-300">${t.action === "buy" ? "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i>매수" : "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i>매도"} ${escHtml(t.name)} ${fmt(t.quantity)}주 @${fmt(t.price)}</div>`
      ).join("");
      const risk = cycle.risk ? `<div class="ml-3 text-slate-500">위험관리: 당일 ${cycle.risk.day_pnl_pct ?? "-"}% · 주문 ${cycle.risk.orders_today ?? "-"}건${cycle.risk.halted ? ` · <span class="text-red-400"><i class="fa-solid fa-circle-stop" aria-hidden="true"></i> ${escHtml(cycle.risk.reason || "비상 정지")}</span>` : ""}</div>` : "";
      const sigs = (cycle.signals || []).map(sig =>
        `<div class="ml-3 text-slate-500">${escHtml(sig.name||sig.symbol)}: ${escHtml(sig.action||"-")} (점수:${sig.score??"-"})</div>`
      ).join("");
      return `<div class="rounded-xl border border-white/10 bg-black/20 p-2 mb-1">
        <div class="font-semibold text-indigo-300">${escHtml(cycle.time)}${cycle.note ? ` <span class="text-red-400 font-normal">· ${escHtml(cycle.note)}</span>` : ""}</div>
        ${risk}${sigs}${trades ? '<div class="mt-1 font-medium text-xs">체결/생략:</div>' + trades : ""}
      </div>`;
    }).join("");
  } catch {}
}

async function loadRiskStatus() {
  try {
    const r = await api("/api/quant/risk/status");
    const L = r.limits || {};
    const pnlCls = r.day_pnl_pct == null ? "" : (r.day_pnl_pct >= 0 ? "text-emerald-400" : "text-red-400");
    const tile = (label, value, cls = "") => `<div class="rounded-lg p-2" style="background:var(--surf3);"><div style="color:var(--text-mute);">${label}</div><div class="font-bold ${cls}">${value}</div></div>`;
    document.getElementById("risk-kpis").innerHTML = [
      tile("비상 정지", r.kill_switch ? `<span class="text-red-400"><i class="fa-solid fa-circle-stop" aria-hidden="true"></i> ON</span>` : `<span class="text-emerald-400">OFF</span>`),
      tile("당일 손익", r.day_pnl_pct == null ? "-" : `${r.day_pnl_pct > 0 ? "+" : ""}${r.day_pnl_pct}% <span class="font-normal" style="color:var(--text-mute)">/ 한도 -${L.daily_loss_limit_pct}%</span>`, r.daily_loss_breached ? "text-red-400" : pnlCls),
      tile("당일 자동 주문", `${r.orders_today ?? 0}건 <span class="font-normal" style="color:var(--text-mute)">/ 최대 ${L.max_orders_per_day || "∞"}</span>`),
      tile("종목 비중 한도", `${L.max_position_pct || "∞"}%`),
      tile("중복 주문 쿨다운", `${L.cooldown_min || 0}분`),
    ].join("");
    document.getElementById("risk-note").textContent = r.kill_switch
      ? `정지 사유: ${r.halt_reason || "수동 비상 정지"} — 해제 후 자동매매를 다시 시작할 수 있습니다.`
      : `시작 자산 ${r.day_start_equity != null ? fmt(r.day_start_equity) + "원" : "-"} · 현재 자산 ${r.current_equity != null ? fmt(r.current_equity) + "원" : "-"} (${r.date})`;
  } catch (e) { document.getElementById("risk-kpis").innerHTML = `<span class="text-red-400">${escHtml(e.message)}</span>`; }
}

document.getElementById("risk-kill-on").addEventListener("click", async () => {
  if (!confirm("자동매매를 즉시 중지하고 비상 정지 상태로 전환합니다. 계속할까요?")) return;
  try {
    await api("/api/quant/risk/kill-switch", { method: "POST", body: { enabled: true, reason: "사용자 수동 비상 정지" } });
    setToast("비상 정지 ON — 자동매매가 중지되었습니다.", "ok"); loadAutoTradeStatus();
  } catch (e) { setToast(e.message, "error"); }
});
document.getElementById("risk-kill-off").addEventListener("click", async () => {
  try {
    await api("/api/quant/risk/kill-switch", { method: "POST", body: { enabled: false } });
    setToast("비상 정지를 해제했습니다.", "ok"); loadAutoTradeStatus();
  } catch (e) { setToast(e.message, "error"); }
});

document.getElementById("auto-start").addEventListener("click", async () => {
  try {
    await api("/api/auto-trade/start", { method: "POST" });
    setToast("자동매매 시작", "ok");
    loadAutoTradeStatus();
  } catch (e) { setToast(e.message, "error"); }
});
document.getElementById("auto-stop").addEventListener("click", async () => {
  try {
    await api("/api/auto-trade/stop", { method: "POST" });
    setToast("자동매매 중지", "ok");
    loadAutoTradeStatus();
  } catch (e) { setToast(e.message, "error"); }
});
document.getElementById("auto-refresh").addEventListener("click", loadAutoTradeStatus);

// ── 백테스트 ─────────────────────────────────────────────────────
document.getElementById("bt-load").addEventListener("click", async () => {
  const symbol = document.getElementById("bt-symbol").value;
  const strategy = document.getElementById("bt-strategy")?.value || "composite";
  const cost = Math.max(0, parseFloat(document.getElementById("bt-cost")?.value) || 0);
  const el = document.getElementById("bt-result");
  el.innerHTML = "<div class='text-slate-400'>시스템 OHLCV 저장소 조회 → 없는 구간 적재 → 10년 백테스트 계산 중…</div>";
  const pct = v => (v == null || Number.isNaN(v)) ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
  const cls = v => v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-slate-300";
  try {
    const data = await api(`/api/quant/pipeline?symbol=${encodeURIComponent(symbol)}&period=10y&strategy=${encodeURIComponent(strategy)}&cost_bps=${cost}`);
    if (data.error) { el.innerHTML = `<div class='text-red-400'>${escHtml(data.error)}</div>`; return; }
    const ps = data.price_source || {};
    const srcLabel = f => f.source === "yahoo→store" ? "Yahoo→저장소 적재" : f.source === "yahoo" ? "Yahoo 직접" : escHtml(String(f.source));
    const fetched = (ps.fetched || []).filter(f => f.rows).map(f => `${srcLabel(f)} ${f.rows}봉 (${escHtml(f.from)}~${escHtml(f.to)})`).join(", ");
    const sourceLine = ps.source
      ? `데이터: ${ps.db_rows_before ? `시스템 OHLCV 저장소 ${ps.db_rows_before}봉` : "저장소에 없음"}${fetched ? ` · ${fetched}` : ""}${ps.note ? ` · ${escHtml(ps.note)}` : ""} → 총 ${ps.rows}봉 (${escHtml(ps.first_date || "")} ~ ${escHtml(ps.last_date || "")})`
      : `데이터: ${escHtml(data.source || "알 수 없음")} · ${data.data_points}봉`;
    const kpi = (label, value, c = "") => `<div class="rounded-lg p-2" style="background:var(--surf2);border:1px solid var(--border);"><div class="text-xs" style="color:var(--text-mute)">${label}</div><div class="font-semibold ${c}">${value}</div></div>`;
    const sig = data.latest_signal || "HOLD";
    const excess = (data.total_return_pct ?? 0) - (data.buy_hold_return_pct ?? 0);
    const explanation = typeof data.explanation === "string" ? data.explanation : (data.explanation?.summary || "");
    el.innerHTML = `
      <div class="rounded-xl border border-white/10 bg-black/20 p-4">
        <div class="flex flex-wrap items-center gap-2 mb-1">
          <span class="font-semibold text-indigo-300">${escHtml(symbol)} · ${escHtml(document.getElementById("bt-strategy")?.selectedOptions?.[0]?.textContent || strategy)} · 10년 백테스트</span>
          <span class="text-xs" style="color:var(--text-mute)">${escHtml((data.data_start || "").slice(0, 10))} ~ ${escHtml((data.data_end || "").slice(0, 10))} · ${data.data_points}봉 · 비용 ${data.cost_bps}bp</span>
          <span class="text-xs ${sig === "BUY" ? "text-emerald-400" : sig === "SELL" ? "text-red-400" : "text-slate-300"}">최근 신호 ${escHtml(sig)}</span>
        </div>
        <div class="text-xs mb-3" style="color:var(--text-mute)">${sourceLine}</div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
          ${kpi("전략 누적수익률", pct(data.total_return_pct), cls(data.total_return_pct))}
          ${kpi("단순 보유 수익률", pct(data.buy_hold_return_pct), cls(data.buy_hold_return_pct))}
          ${kpi("초과 수익 (vs 보유)", pct(excess), cls(excess))}
          ${kpi("샤프 비율", data.sharpe_ratio ?? "-")}
          ${kpi("최대 낙폭 (MDD)", `${data.mdd_pct}%`, "text-red-400")}
          ${kpi("승률 (보유일 기준)", `${data.win_rate_pct}%`)}
          ${kpi("매매 횟수 / 시장 노출", `${data.trade_count}회 / ${data.exposure_pct}%`)}
          ${kpi("비용 차감", `${data.cost_pct}%`)}
        </div>
        ${explanation ? `<div class="mt-3 text-xs text-slate-400">${escHtml(explanation)}</div>` : ""}
        <p class="text-xs mt-2" style="color:var(--text-mute)">t일 종가 신호를 t+1일부터 반영(룩어헤드 없음) · 수수료·슬리피지는 포지션 변동 시 차감 · 교육용 과거 검증이며 투자 권유가 아닙니다.</p>
        <button type="button" class="btn-secondary text-xs mt-3" onclick='compareTrayAdd(${JSON.stringify({
          source: "퀀트 전략 백테스트", label: `${symbol} · ${strategy}`, kind: "backtest", symbol, period: "10y",
          summary: `수익률 ${pct(data.total_return_pct)} · 보유 ${pct(data.buy_hold_return_pct)} · 샤프 ${data.sharpe_ratio ?? "-"} · MDD ${data.mdd_pct}%`,
          metrics: { return_pct: data.total_return_pct, bh_pct: data.buy_hold_return_pct, excess_pct: excess, sharpe: data.sharpe_ratio, mdd_pct: data.mdd_pct,
                     win_rate_pct: data.win_rate_pct, trade_count: data.trade_count, exposure_pct: data.exposure_pct, cost_bps: data.cost_bps, period: "10y" },
        }).replace(/'/g, "&#39;")})'><i class="fa-solid fa-scale-balanced" aria-hidden="true"></i> 비교에 추가</button>
      </div>
    `;
    renderCompareTrayAll();
  } catch (e) { el.innerHTML = `<div class='text-red-400'>${escHtml(e.message)}</div>`; }
});


export { loadAutoTradeStatus, loadQuantDashboard };
