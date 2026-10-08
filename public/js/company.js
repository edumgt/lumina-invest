/* 기업분석(지표 대시보드·비교·섹터) + 종목 검색 모달
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { attachIndicatorHelp } from "/js/company-indicator-help.js";
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";

// ── 기업분석 (Mockup) ────────────────────────────────────────────
const COMPANIES = [
  { code:"005930", name:"삼성전자",      sector:"반도체/전자",  market:"KOSPI" },
  { code:"000660", name:"SK하이닉스",    sector:"반도체",       market:"KOSPI" },
  { code:"035420", name:"NAVER",         sector:"IT플랫폼",     market:"KOSPI" },
  { code:"005380", name:"현대자동차",    sector:"자동차",       market:"KOSPI" },
  { code:"105560", name:"KB금융",        sector:"금융",         market:"KOSPI" },
];

// 대시보드 기본 종목. 비교 화면은 서버의 KIS 분석 후보 목록을 사용한다.
let dashboardStocks = COMPANIES.map(c => ({ symbol: `${c.code}.KS`, name: c.name }));
let selectedCompanySymbol = dashboardStocks[0].symbol;
const companyFundCache = {};

function loadCompanyDashboard() {
  attachIndicatorHelp();
  renderCompanyTabs();
  fetchAndRenderCompany(selectedCompanySymbol);
}

function renderCompanyTabs() {
  const el = document.getElementById("company-tabs");
  if (!el) return;
  el.innerHTML = dashboardStocks.map(c => `
    <button onclick="selectCompany('${c.symbol}')"
      style="padding:5px 14px; border-radius:20px; font-size:12px; font-weight:600; cursor:pointer; border:1px solid ${selectedCompanySymbol===c.symbol?"var(--accent)":"var(--border)"}; background:${selectedCompanySymbol===c.symbol?"var(--accent)":"var(--surf)"}; color:${selectedCompanySymbol===c.symbol?"#fff":"var(--text-dim)"}; transition:all .15s;">
      ${escHtml(c.name)}
    </button>
  `).join("");
}

function selectCompany(symbol) {
  selectedCompanySymbol = symbol;
  renderCompanyTabs();
  fetchAndRenderCompany(symbol);
}
window.selectCompany = selectCompany;

function addAndSelectCompany(symbol, name) {
  if (!dashboardStocks.some(c => c.symbol === symbol)) {
    dashboardStocks.push({ symbol, name });
  }
  selectCompany(symbol);
}

// null-safe 포맷: 값 없으면 "N/A" (Yahoo가 국내 상장사 일부 지표를 제공하지 않는 경우가 있음)
function nfmt(v, suffix = "") {
  return (v === null || v === undefined) ? "N/A" : `${fmt(v)}${suffix}`;
}

async function fetchAndRenderCompany(symbol) {
  const overviewEl = document.getElementById("co-overview");
  overviewEl.innerHTML = `<div class="text-xs" style="color:var(--text-mute);grid-column:1/-1;">불러오는 중…</div>`;
  try {
    let d = companyFundCache[symbol];
    if (!d) {
      d = await api(`/api/stocks/fundamentals?symbol=${encodeURIComponent(symbol)}`);
      companyFundCache[symbol] = d;
    }
    if (selectedCompanySymbol === symbol) renderCompanyData(d);
  } catch (e) {
    overviewEl.innerHTML = `<div class="text-xs" style="color:var(--red);grid-column:1/-1;">데이터 조회 실패: ${escHtml(e.message)}</div>`;
  }
}

function renderCompanyData(d) {
  const pctColor = (d.chg ?? 0) >= 0 ? "var(--green)" : "var(--red)";
  const sign = (d.chg ?? 0) >= 0 ? "+" : "";

  // 개요 카드 4개
  document.getElementById("co-overview").innerHTML = [
    { label:"현재가", value:nfmt(d.price, "원"), sub:d.chg!=null?`${sign}${d.chg}%`:"", subColor:pctColor },
    { label:"시가총액", value:d.cap!=null?`${fmt(Math.round(d.cap/10000))}조원`:"N/A", sub:d.name||"" },
    { label:"EPS", value:nfmt(d.eps, "원"), sub:`PER ${d.per!=null?d.per.toFixed(1)+"x":"N/A"}` },
    { label:"BPS", value:nfmt(d.bps, "원"), sub:`PBR ${d.pbr!=null?d.pbr.toFixed(2)+"x":"N/A"}` },
  ].map(c => `
    <div class="card" style="padding:16px;">
      <div class="text-xs" style="color:var(--text-mute);">${c.label}</div>
      <div style="font-size:20px; font-weight:700; margin:4px 0;">${c.value}</div>
      <div style="font-size:12px; color:${c.subColor||"var(--text-dim)"};">${escHtml(c.sub)}</div>
    </div>
  `).join("");

  // 밸류에이션
  const valRows = [
    ["PER (주가수익비율)", d.per!=null ? `${d.per.toFixed(2)}x` : "N/A"],
    ["PBR (주가순자산비율)", d.pbr!=null ? `${d.pbr.toFixed(2)}x` : "N/A"],
    ["배당수익률", d.divYield!=null ? `${d.divYield}%` : "N/A"],
    ["주당배당금 (DPS)", nfmt(d.div, "원")],
  ];
  document.getElementById("co-valuation").innerHTML = valRows.map(([k,v]) => `
    <div class="flex justify-between items-center" style="padding:7px 0; border-bottom:1px solid var(--border);">
      <span style="font-size:12px; color:var(--text-dim);">${k}</span>
      <span style="font-size:13px; font-weight:600;">${v}</span>
    </div>
  `).join("");

  // 수익성
  const profRows = [
    ["ROE (자기자본이익률)", d.roe!=null ? `${d.roe}%` : "N/A"],
    ["ROA (총자산이익률)", d.roa!=null ? `${d.roa}%` : "N/A"],
    ["영업이익률", d.opMargin!=null ? `${d.opMargin}%` : "N/A"],
    ["부채비율(D/E)", d.debt!=null ? `${d.debt}` : "N/A"],
  ];
  document.getElementById("co-profitability").innerHTML = profRows.map(([k,v]) => `
    <div class="flex justify-between items-center" style="padding:7px 0; border-bottom:1px solid var(--border);">
      <span style="font-size:12px; color:var(--text-dim);">${k}</span>
      <span style="font-size:13px; font-weight:600;">${v}</span>
    </div>
  `).join("");

  // 분기 실적
  const qs = d.quarters || [];
  document.getElementById("co-quarterly").innerHTML = qs.length ? `
    <table>
      <thead><tr><th>구분</th>${qs.map(q=>`<th style="text-align:right;">${escHtml(q)}</th>`).join("")}</tr></thead>
      <tbody>
        <tr><td style="color:var(--text-dim);font-size:12px;">매출액 (억원)</td>${d.revenue.map(v=>`<td style="text-align:right;font-weight:600;">${nfmt(v)}</td>`).join("")}</tr>
        <tr><td style="color:var(--text-dim);font-size:12px;">영업이익 (억원)</td>${d.op.map(v=>`<td style="text-align:right;color:var(--green);font-weight:600;">${nfmt(v)}</td>`).join("")}</tr>
        <tr><td style="color:var(--text-dim);font-size:12px;">순이익 (억원)</td>${d.net.map(v=>`<td style="text-align:right;color:var(--accent);font-weight:600;">${nfmt(v)}</td>`).join("")}</tr>
      </tbody>
    </table>` : `<div class="text-xs" style="color:var(--text-mute);">분기 실적 데이터 없음</div>`;

  // 재무상태표
  const hasBalance = d.assets != null;
  document.getElementById("co-balance").innerHTML = hasBalance ? `
    <table>
      <thead><tr><th>항목</th><th style="text-align:right;">금액 (억원)</th><th style="text-align:right;">비중</th></tr></thead>
      <tbody>
        <tr><td style="color:var(--text-dim);font-size:12px;">총자산</td><td style="text-align:right;font-weight:700;">${fmt(d.assets)}</td><td style="text-align:right;">100%</td></tr>
        <tr><td style="color:var(--text-dim);font-size:12px;">자기자본</td><td style="text-align:right;font-weight:600;color:var(--green);">${nfmt(d.equity)}</td><td style="text-align:right;">${d.equity!=null?(d.equity/d.assets*100).toFixed(1)+"%":"N/A"}</td></tr>
        <tr><td style="color:var(--text-dim);font-size:12px;">부채총계</td><td style="text-align:right;font-weight:600;color:var(--red);">${nfmt(d.liabilities)}</td><td style="text-align:right;">${d.liabilities!=null?(d.liabilities/d.assets*100).toFixed(1)+"%":"N/A"}</td></tr>
        ${d.cash!=null ? `<tr><td style="color:var(--text-dim);font-size:12px;">현금및현금성자산</td><td style="text-align:right;">${fmt(d.cash)}</td><td style="text-align:right;">-</td></tr>` : ""}
      </tbody>
    </table>` : `<div class="text-xs" style="color:var(--text-mute);">Yahoo Finance가 이 종목의 재무상태표 상세를 제공하지 않습니다.</div>`;
  attachIndicatorHelp();
}

const COMPARE_METRICS = [
  ["시장", "현재가", "price", "원"], ["시장", "시가총액", "cap", "억원"],
  ["가치평가", "PER", "per", "배"], ["가치평가", "선행 PER", "forwardPer", "배"],
  ["가치평가", "PBR", "pbr", "배"], ["가치평가", "PSR", "psr", "배"],
  ["가치평가", "EV/EBITDA", "evEbitda", "배"], ["가치평가", "EPS", "eps", "원"],
  ["가치평가", "BPS", "bps", "원"],
  ["수익성", "ROE", "roe", "%"], ["수익성", "ROA", "roa", "%"],
  ["수익성", "매출총이익률", "grossMargin", "%"],
  ["수익성", "영업이익률", "opMargin", "%"], ["수익성", "순이익률", "netMargin", "%"],
  ["성장성", "매출 성장률 (전년 대비)", "revenueGrowth", "%"],
  ["성장성", "이익 성장률 (전년 대비)", "earningsGrowth", "%"],
  ["배당", "주당배당금", "div", "원"], ["배당", "배당수익률", "divYield", "%"],
  ["배당", "배당성향", "payoutRatio", "%"],
  ["건전성", "차입금/자기자본 (D/E)", "debt", "%"],
  ["건전성", "유동비율", "currentRatio", "배"], ["건전성", "당좌비율", "quickRatio", "배"],
  ["재무규모", "매출액 (TTM)", "totalRevenue", "억원"],
  ["재무규모", "총자산 (최근 연도)", "assets", "억원"],
  ["재무규모", "자기자본 (최근 연도)", "equity", "억원"],
  ["재무규모", "부채총계 (최근 연도)", "liabilities", "억원"],
  ["현금흐름", "현금 및 단기투자", "totalCash", "억원"],
  ["현금흐름", "총차입금", "totalDebt", "억원"],
  ["현금흐름", "영업현금흐름 (TTM)", "operatingCashflow", "억원"],
  ["현금흐름", "잉여현금흐름 (TTM)", "freeCashflow", "억원"],
];
let compareRun = 0;
let compareGrid = null;
async function loadCompanyCompare() {
  const run = ++compareRun;
  const el = document.getElementById("co-compare-table");
  const status = document.getElementById("co-compare-status");
  const refresh = document.getElementById("co-compare-refresh");
  refresh.onclick = () => { Object.keys(companyFundCache).forEach(k => delete companyFundCache[k]); loadCompanyCompare(); };
  refresh.disabled = true;
  status.textContent = "비교 대상 조회 중…";
  compareGrid?.destroy();
  compareGrid = null;
  el.innerHTML = "";
  try {
    const { stocks } = await api("/api/stocks/quant/list");
    if (run !== compareRun) return;
    const companies = stocks.slice(0, 30);
    const results = {};
    let done = 0, failed = 0;
    if (!window.agGrid) throw new Error("AG Grid를 불러오지 못했습니다. 페이지를 새로고침하세요.");
    const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
    const groups = [...new Set(COMPARE_METRICS.map(([group]) => group))];
    const columnDefs = [
      { field: "name", headerName: "종목", pinned: "left", lockPinned: true, width: 165, minWidth: 130, filter: "agTextColumnFilter" },
      { field: "symbol", headerName: "종목코드", width: 135, filter: "agTextColumnFilter" },
      { field: "sector", headerName: "섹터", width: 110, filter: "agTextColumnFilter" },
      { field: "status", headerName: "조회 상태", width: 150, filter: "agTextColumnFilter", tooltipValueGetter: ({ data }) => data?.cached_at ? `${data.warning} · 자료 조회 시각 ${new Date(data.cached_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} (KST)` : data?.warning || data?.status || "" },
      ...groups.map(group => ({
        headerName: group,
        marryChildren: true,
        children: COMPARE_METRICS.filter(([g]) => g === group).map(([, label, key, unit]) => ({
          field: key, headerName: `${label} (${unit})`, headerTooltip: `${label} (${unit})`,
          width: label.length > 12 ? 200 : 145,
          filter: "agNumberColumnFilter", cellStyle: { textAlign: "right", fontVariantNumeric: "tabular-nums" },
          valueFormatter: ({ value, data }) => data?.status === "조회 중" ? "…" :
            (typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "N/A"),
        })),
      })),
    ];
    const css = getComputedStyle(document.documentElement);
    const theme = agGrid.themeQuartz.withParams({
      backgroundColor: css.getPropertyValue("--surf").trim() || "#ffffff",
      foregroundColor: css.getPropertyValue("--text").trim() || "#1f2937",
      borderColor: css.getPropertyValue("--border").trim() || "#d1d5db",
      accentColor: css.getPropertyValue("--accent").trim() || "#2563eb",
      fontFamily: "inherit", fontSize: 12, spacing: 6,
    });
    compareGrid = agGrid.createGrid(el, {
      theme, columnDefs, rowData: [],
      defaultColDef: { sortable: true, resizable: true, filter: true },
      getRowId: ({ data }) => data.symbol,
      rowHeight: 38, headerHeight: 54, groupHeaderHeight: 32,
      suppressMovableColumns: true,
      localeText: { noRowsToShow: "표시할 종목이 없습니다", loadingOoo: "조회 중…", filterOoo: "필터…", equals: "같음", notEqual: "다름", contains: "포함", notContains: "미포함", startsWith: "시작", endsWith: "끝", lessThan: "미만", greaterThan: "초과", lessThanOrEqual: "이하", greaterThanOrEqual: "이상", inRange: "범위", blank: "빈 값", notBlank: "값 있음", andCondition: "그리고", orCondition: "또는" },
    });
    const render = () => {
      status.textContent = `${companies.length}개 종목 · ${COMPARE_METRICS.length}개 지표 · 조회 ${done}/${companies.length}` + (failed ? ` · 재무 조회 제한 ${failed}개 (조회 상태에 마우스를 올려 상세 확인 · 새로고침으로 재시도)` : "");
      compareGrid.setGridOption("rowData", companies.map(c => {
        const d = results[c.symbol];
        const labels = { stale: "이전 자료", partial: "가격만 조회", unavailable: "조회 불가" };
        return { ...d, ...c, status: !d ? "조회 중" : d.error ? "조회 실패" : labels[d.data_status] || "완료" };
      }));
    };
    render();
    // 외부 재무 데이터 요청은 최대 4개 동시 실행. 실패한 종목도 행을 유지한다.
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(4, companies.length) }, async () => {
      while (next < companies.length && run === compareRun) {
        const c = companies[next++];
        try {
          results[c.symbol] = companyFundCache[c.symbol] || await api(`/api/stocks/fundamentals?symbol=${encodeURIComponent(c.symbol)}`);
          if (!results[c.symbol].data_status || results[c.symbol].data_status === "complete") companyFundCache[c.symbol] = results[c.symbol];
          if (["stale", "partial", "unavailable"].includes(results[c.symbol].data_status)) failed++;
        } catch (_) { results[c.symbol] = { error: true }; failed++; }
        done++;
        if (run === compareRun) render();
      }
    }));
  } catch (e) {
    if (run === compareRun) status.textContent = `비교 대상 조회 실패: ${e.message}`;
  } finally { if (run === compareRun) refresh.disabled = false; }
}

// ── 섹터별 투자 인디케이터 ────────────────────────────────────────
// 모든 수치는 /api/stocks/sectors 가 실제 펀더멘털(Yahoo quoteSummary)·일봉에서 계산한 값이다.
// 값이 없으면 N/A 로 두고 커버리지를 함께 적는다 — 지어내지 않는다.
const SEC_VERDICT_COLOR = { "비중확대": "var(--green)", "비중축소": "var(--red)", "중립": "var(--text-mute)", "판단 불가": "var(--red)" };
const SEC_FACTOR_LABEL = { momentum: "모멘텀", profitability: "수익성", growth: "성장", valuation: "밸류에이션", stability: "안정성" };

const secNum = (v, suffix = "", digits = 2) =>
  (v === null || v === undefined || Number.isNaN(v)) ? "N/A" : `${Number(v).toFixed(digits)}${suffix}`;
const secPct = (v, digits = 2) => {
  if (v === null || v === undefined) return `<span style="color:var(--text-mute);">N/A</span>`;
  const c = v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--text-mute)";
  return `<span style="color:${c};">${v > 0 ? "+" : ""}${Number(v).toFixed(digits)}%</span>`;
};
const secEok = v => (v === null || v === undefined) ? "N/A"
  : v >= 10000 ? `${(v / 10000).toFixed(1)}조원` : `${Math.round(v).toLocaleString("ko-KR")}억원`;

function secMetric(label, value, hint) {
  return `<div>
    <div class="text-xs" style="color:var(--text-mute);">${escHtml(label)}${hint ? ` <span title="${escHtml(hint)}" style="cursor:help;">ⓘ</span>` : ""}</div>
    <div style="font-size:15px;font-weight:700;">${value}</div>
  </div>`;
}

function secFactorBars(score) {
  return Object.entries(SEC_FACTOR_LABEL).map(([k, label]) => {
    const v = score?.[k];
    const w = score?.weights?.[k];
    const pct = v === null || v === undefined ? 0 : v;
    return `<div class="mb-1">
      <div class="flex justify-between text-xs" style="color:var(--text-mute);">
        <span>${escHtml(label)} <span style="opacity:.6;">가중 ${w}%</span></span>
        <span>${v === null || v === undefined ? "N/A" : v.toFixed(1)}</span>
      </div>
      <div style="height:6px;border-radius:3px;background:var(--surf2);overflow:hidden;">
        <div style="height:100%;width:${pct}%;background:${pct >= 60 ? "var(--green)" : pct <= 40 ? "var(--red)" : "var(--accent)"};"></div>
      </div>
    </div>`;
  }).join("");
}

function secBenchRow(b) {
  const cells = [["1개월", b.ret_1m], ["3개월", b.ret_3m], ["6개월", b.ret_6m], ["12개월", b.ret_12m]];
  return `<div class="rounded-lg p-3 text-xs" style="background:var(--surf2);border:1px solid var(--border);">
    <span class="font-semibold">벤치마크 ${escHtml(b.name || "")} (${escHtml(b.symbol || "")})</span>
    <span style="color:var(--text-mute);"> · 섹터 수익률은 이 지수와 비교해 초과분을 계산합니다</span>
    <div class="flex gap-5 mt-2">${cells.map(([k, v]) => `<div><div style="color:var(--text-mute);">${k}</div><div style="font-weight:700;">${secPct(v)}</div></div>`).join("")}</div>
  </div>`;
}

function secComparisonTable(sectors) {
  const head = ["섹터", "상대점수", "판정", "시가총액", "PER", "ROE", "매출성장", "3개월", "vs KOSPI(3M)", "20일선 위", "RSI(14)"];
  return `
    <thead><tr style="color:var(--text-mute);text-align:right;">
      ${head.map((h, i) => `<th class="py-2 px-2" style="text-align:${i < 3 ? "left" : "right"};white-space:nowrap;">${h}</th>`).join("")}
    </tr></thead>
    <tbody>${sectors.map(s => `
      <tr style="border-top:1px solid var(--border);">
        <td class="py-2 px-2 font-semibold">${escHtml(s.sector)} <span style="color:var(--text-mute);font-weight:400;">${s.count}종목</span></td>
        <td class="py-2 px-2" style="font-weight:700;">${s.score?.total == null ? "N/A" : s.score.total.toFixed(1)}</td>
        <td class="py-2 px-2" style="font-weight:700;color:${SEC_VERDICT_COLOR[s.verdict] || "var(--text-mute)"};">${escHtml(s.verdict)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secEok(s.market_cap)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(s.valuation?.per, "x")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(s.profitability?.roe, "%")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(s.growth?.revenue)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(s.price?.ret_3m)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(s.relative?.excess_3m)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(s.breadth?.above_ma20_pct, "%", 0)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(s.price?.rsi14, "", 1)}</td>
      </tr>`).join("")}
    </tbody>`;
}

function secHoldingsTable(holdings) {
  const head = ["종목", "비중", "현재가", "등락", "PER", "ROE", "영업이익률", "매출성장", "1개월", "3개월", "12개월", "RSI", "추세"];
  return `<div class="overflow-x-auto"><table class="w-full text-xs">
    <thead><tr style="color:var(--text-mute);">
      ${head.map((h, i) => `<th class="py-2 px-2" style="text-align:${i === 0 ? "left" : "right"};white-space:nowrap;">${h}</th>`).join("")}
    </tr></thead>
    <tbody>${holdings.map(h => {
      const trend = h.errors ? `<span style="color:var(--red);">데이터 없음</span>`
        : [h.above_ma20 ? "20일선↑" : "20일선↓", h.above_ma60 ? "60일선↑" : "60일선↓"]
            .map((t, i) => `<span style="color:${(i === 0 ? h.above_ma20 : h.above_ma60) ? "var(--green)" : "var(--red)"};">${t}</span>`).join(" ");
      return `<tr style="border-top:1px solid var(--border);">
        <td class="py-2 px-2">${escHtml(h.name || h.symbol)}<div style="color:var(--text-mute);">${escHtml(h.symbol)}</div></td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(h.weight_pct, "%", 1)}</td>
        <td class="py-2 px-2" style="text-align:right;">${h.price == null ? "N/A" : Number(h.price).toLocaleString("ko-KR")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(h.chg)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(h.per, "x")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(h.roe, "%")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(h.op_margin, "%")}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(h.revenue_growth)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(h.ret_1m)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(h.ret_3m)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secPct(h.ret_12m)}</td>
        <td class="py-2 px-2" style="text-align:right;">${secNum(h.rsi14, "", 1)}</td>
        <td class="py-2 px-2" style="text-align:right;white-space:nowrap;">${trend}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

function secBasisLine(s) {
  const b = s.basis || {};
  const cov = b.coverage || {};
  const missing = Object.entries(cov).filter(([, n]) => n < (b.members || 0))
    .map(([k, n]) => `${k} ${n}/${b.members}`);
  const perBasis = s.valuation?.per_basis;
  const perNote = perBasis && (perBasis.trailing || perBasis.forward)
    ? ` · PER 근거: 실적 ${perBasis.trailing}종목 / 전망 ${perBasis.forward}종목` : "";
  return `<div class="text-xs mt-3 rounded-lg p-3" style="background:var(--surf2);border:1px solid var(--border);color:var(--text-mute);">
    📡 ${escHtml(b.source || "출처 미기록")} · 구성 ${b.members}종목 중 가격 ${b.priced}종목 · 일봉 ${b.index_days}일 · ${escHtml(b.price_as_of || "기준일 미기록")} 기준${escHtml(perNote)}
    <div class="mt-1">집계: ${escHtml(b.method || "")}</div>
    ${missing.length ? `<div class="mt-1">결측(지어내지 않고 N/A 처리): ${escHtml(missing.join(" · "))}</div>` : ""}
    ${(b.errors || []).length ? `<div class="mt-1" style="color:var(--red);">제외: ${escHtml(b.errors.join(" · "))}</div>` : ""}
  </div>`;
}

function secCard(s) {
  const v = s.valuation || {}, p = s.profitability || {}, g = s.growth || {}, st = s.stability || {}, pr = s.price || {}, br = s.breadth || {}, rel = s.relative || {};
  return `<div class="card">
    <div class="flex items-start justify-between gap-3 flex-wrap mb-3">
      <div>
        <span class="font-semibold" style="font-size:15px;">${escHtml(s.sector)}</span>
        <span class="text-xs" style="color:var(--text-mute);"> · ${s.count}종목 · 시총 ${secEok(s.market_cap)} · 모멘텀 ${s.momentum_rank ?? "-"}위</span>
      </div>
      <div class="text-right">
        <div style="font-weight:700;color:${SEC_VERDICT_COLOR[s.verdict] || "var(--text-mute)"};">${escHtml(s.verdict)}</div>
        <div class="text-xs" style="color:var(--text-mute);">상대점수 ${s.score?.total == null ? "N/A" : s.score.total.toFixed(1)} / 100</div>
      </div>
    </div>

    <div class="grid md:grid-cols-2 gap-4 mb-4">
      <div>${secFactorBars(s.score)}</div>
      <div class="grid grid-cols-3 gap-3">
        ${secMetric("3개월", secPct(pr.ret_3m))}
        ${secMetric("vs KOSPI", secPct(rel.excess_3m), "섹터 3개월 수익률 − KOSPI 3개월 수익률")}
        ${secMetric("12개월", secPct(pr.ret_12m))}
      </div>
    </div>

    <div class="grid md:grid-cols-4 gap-4 mb-4">
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-dim);">밸류에이션</div>
        <div class="grid grid-cols-2 gap-3">
          ${secMetric("PER", secNum(v.per, "x"), "시총가중 조화평균. 적자(0 이하)는 제외")}
          ${secMetric("PER 중위", secNum(v.per_median, "x"), "대형주 쏠림을 걸러낸 중위값")}
          ${secMetric("PBR", secNum(v.pbr, "x"))}
          ${secMetric("PSR", secNum(v.psr, "x"))}
          ${secMetric("EV/EBITDA", secNum(v.ev_ebitda, "x"))}
          ${secMetric("배당수익률", secNum(v.div_yield, "%"))}
        </div>
      </div>
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-dim);">수익성</div>
        <div class="grid grid-cols-2 gap-3">
          ${secMetric("ROE", secNum(p.roe, "%"))}
          ${secMetric("ROA", secNum(p.roa, "%"))}
          ${secMetric("영업이익률", secNum(p.op_margin, "%"))}
          ${secMetric("순이익률", secNum(p.net_margin, "%"))}
        </div>
      </div>
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-dim);">성장 · 재무안정성</div>
        <div class="grid grid-cols-2 gap-3">
          ${secMetric("매출 성장", secPct(g.revenue), "최근 분기, 전년 동기 대비")}
          ${secMetric("이익 성장", secPct(g.earnings), "최근 분기, 전년 동기 대비")}
          ${secMetric("부채비율 D/E", secNum(st.debt_to_equity, "%"))}
          ${secMetric("유동비율", secNum(st.current_ratio, "x"))}
        </div>
      </div>
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-dim);">가격 · 시장폭</div>
        <div class="grid grid-cols-2 gap-3">
          ${secMetric("1개월", secPct(pr.ret_1m))}
          ${secMetric("6개월", secPct(pr.ret_6m))}
          ${secMetric("변동성", secNum(pr.vol_20d_annual, "%", 1), "20일 일간수익률 표준편차의 연율")}
          ${secMetric("52주 위치", secNum(pr.pos_52w, "%", 0), "0=52주 최저, 100=52주 최고")}
          ${secMetric("20일선 위", secNum(br.above_ma20_pct, "%", 0), "구성종목 중 20일 이평선 위에 있는 비중")}
          ${secMetric("1개월 상승", secNum(br.up_1m_pct, "%", 0), "구성종목 중 1개월 수익률이 플러스인 비중")}
        </div>
      </div>
    </div>

    <div class="rounded-lg p-3 text-xs" style="background:rgba(41,98,255,0.08);border:1px solid rgba(41,98,255,0.2);">
      <strong style="color:var(--accent);">📌 판단 근거</strong>
      <ul class="mt-2 space-y-1" style="color:var(--text-dim);list-style:disc;padding-left:16px;">
        ${(s.rationale || []).length ? s.rationale.map(r => `<li>${escHtml(r)}</li>`).join("") : `<li>계산된 근거가 없습니다 (데이터 부족)</li>`}
      </ul>
    </div>

    ${secBasisLine(s)}

    <details class="mt-3">
      <summary class="text-xs cursor-pointer" style="color:var(--accent);">구성종목 ${s.count}개 상세 지표 보기</summary>
      <div class="mt-2">${secHoldingsTable(s.holdings || [])}</div>
    </details>
  </div>`;
}

async function loadCompanySector(force = false) {
  const cards = document.getElementById("co-sector-cards");
  const meta = document.getElementById("co-sector-meta");
  if (!cards) return;
  cards.innerHTML = `<div class="card text-xs" style="color:var(--text-mute);">섹터 지표를 계산하는 중… (실제 펀더멘털·일봉 조회, 최초 수십 초)</div>`;
  try {
    const d = await api(`/api/stocks/sectors${force ? "?force=true" : ""}`);
    if (meta) {
      meta.innerHTML = `유니버스 ${d.universe?.symbols ?? "-"}종목 · 섹터 ${(d.universe?.sectors || []).map(escHtml).join(" · ")}`
        + ` · 계산 ${escHtml(String(d.as_of || "").replace("T", " ").replace("+00:00", " UTC"))}`
        + (d.from_cache ? " · 캐시(1시간) 사용 — 최신값은 재계산" : " · 방금 재계산");
    }
    document.getElementById("co-sector-bench").innerHTML = d.benchmark ? secBenchRow(d.benchmark) : "";
    document.getElementById("co-sector-table").innerHTML = secComparisonTable(d.sectors || []);
    document.getElementById("co-sector-rule").textContent = d.verdict_rule || "";
    cards.innerHTML = (d.sectors || []).map(secCard).join("");
  } catch (e) {
    cards.innerHTML = `<div class="card text-xs" style="color:var(--red);">섹터 지표 조회 실패: ${escHtml(e.message)}</div>`;
  }
}

document.getElementById("co-sector-refresh")?.addEventListener("click", () => loadCompanySector(false));
document.getElementById("co-sector-recalc")?.addEventListener("click", () => {
  setToast("섹터 지표를 재계산합니다 — 수십 초 걸릴 수 있습니다", "ok");
  loadCompanySector(true);
});

// ── 종목 검색 모달 ────────────────────────────────────────────────
(function () {
  let _resolve = null;
  let _debounceTimer = null;

  const modal = document.getElementById("stock-search-modal");
  const inputEl = document.getElementById("ssm-query");
  const resultsEl = document.getElementById("ssm-results");

  function openModal(callback) {
    _resolve = callback;
    inputEl.value = "";
    resultsEl.innerHTML = `<div class="ssm-empty">종목명 또는 코드를 입력하세요</div>`;
    modal.classList.add("open");
    setTimeout(() => inputEl.focus(), 60);
  }

  function closeModal() {
    modal.classList.remove("open");
    _resolve = null;
  }

  function selectItem(symbol, name) {
    if (_resolve) _resolve({ symbol, name });
    closeModal();
  }

  async function doSearch(q) {
    if (!q.trim()) {
      resultsEl.innerHTML = `<div class="ssm-empty">종목명 또는 코드를 입력하세요</div>`;
      return;
    }
    resultsEl.innerHTML = `<div class="ssm-loading">검색 중…</div>`;
    try {
      const data = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`);
      if (!data.results?.length) {
        resultsEl.innerHTML = `<div class="ssm-empty">검색 결과가 없습니다</div>`;
        return;
      }
      resultsEl.innerHTML = data.results.map(r => `
        <div class="ssm-item" data-symbol="${escHtml(r.symbol)}" data-name="${escHtml(r.name)}">
          <div>
            <div class="ssm-item-name">${escHtml(r.name)}</div>
            <div class="ssm-item-meta">${escHtml(r.exchange)} · ${escHtml(r.type)}</div>
          </div>
          <span class="ssm-item-ticker">${escHtml(r.symbol)}</span>
        </div>
      `).join("");
      resultsEl.querySelectorAll(".ssm-item").forEach(el => {
        el.addEventListener("click", () => selectItem(el.dataset.symbol, el.dataset.name));
      });
    } catch (e) {
      resultsEl.innerHTML = `<div class="ssm-empty" style="color:var(--red);">오류: ${escHtml(e.message)}</div>`;
    }
  }

  inputEl.addEventListener("input", () => {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => doSearch(inputEl.value), 320);
  });
  inputEl.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  document.getElementById("ssm-close").addEventListener("click", closeModal);

  // 퀀트 대시보드 차트 종목 버튼
  document.getElementById("quant-symbol-btn").addEventListener("click", () => {
    openModal(({ symbol, name }) => {
      document.getElementById("quant-symbol").value = symbol;
      const btn = document.getElementById("quant-symbol-btn");
      btn.querySelector(".ssm-btn-label").textContent = name;
      btn.querySelector(".ssm-btn-ticker").textContent = symbol;
    });
  });

  // 주가 차트 종목 버튼
  document.getElementById("chart-symbol-btn").addEventListener("click", () => {
    openModal(({ symbol, name }) => {
      document.getElementById("chart-symbol").value = symbol;
      const btn = document.getElementById("chart-symbol-btn");
      btn.querySelector(".ssm-btn-label").textContent = name;
      btn.querySelector(".ssm-btn-ticker").textContent = symbol;
    });
  });

  document.getElementById("pt-search")?.addEventListener("click", () => {
    openModal(({ symbol, name }) => {
      document.getElementById("pt-symbol").value = symbol;
      document.getElementById("pt-selected").textContent = name;
      document.getElementById("pt-symbol").dispatchEvent(new Event("change"));
    });
  });

  // 지표 대시보드 종목 검색 (기존 5개 목업 종목 외 임의 종목 추가)
  document.getElementById("company-search-btn")?.addEventListener("click", () => {
    openModal(({ symbol, name }) => addAndSelectCompany(symbol, name));
  });
})();


export { loadCompanyCompare, loadCompanyDashboard, loadCompanySector };
