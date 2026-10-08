/* 기업분석(지표 대시보드·비교·섹터) + 종목 검색 모달
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
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
      { field: "status", headerName: "조회 상태", width: 115, filter: "agTextColumnFilter" },
      ...groups.map(group => ({
        headerName: group,
        marryChildren: true,
        children: COMPARE_METRICS.filter(([g]) => g === group).map(([, label, key, unit]) => ({
          field: key, headerName: `${label} (${unit})`, headerTooltip: `${label} (${unit})`,
          width: label.length > 12 ? 200 : 145,
          filter: "agNumberColumnFilter", cellStyle: { textAlign: "right", fontVariantNumeric: "tabular-nums" },
          valueFormatter: ({ value, data }) => data.status === "조회 중" ? "…" :
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
      status.textContent = `${companies.length}개 종목 · ${COMPARE_METRICS.length}개 지표 · 조회 ${done}/${companies.length}` + (failed ? ` · 조회 실패 ${failed}개 (새로고침으로 재시도)` : "");
      compareGrid.setGridOption("rowData", companies.map(c => {
        const d = results[c.symbol];
        return { ...d, ...c, status: !d ? "조회 중" : d.error ? "조회 실패" : "완료" };
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
          companyFundCache[c.symbol] = results[c.symbol];
        } catch (_) { results[c.symbol] = { error: true }; failed++; }
        done++;
        if (run === compareRun) render();
      }
    }));
  } catch (e) {
    if (run === compareRun) status.textContent = `비교 대상 조회 실패: ${e.message}`;
  } finally { if (run === compareRun) refresh.disabled = false; }
}

function loadCompanySector() {
  const sectors = [
    { name:"반도체", companies:["삼성전자","SK하이닉스"], avgPer:22.3, avgPbr:1.82, outlook:"긍정" },
    { name:"IT 플랫폼", companies:["NAVER","카카오"], avgPer:38.7, avgPbr:2.94, outlook:"중립" },
    { name:"자동차", companies:["현대차","기아"], avgPer:6.2, avgPbr:0.81, outlook:"긍정" },
    { name:"금융", companies:["KB금융","신한지주"], avgPer:6.8, avgPbr:0.62, outlook:"중립" },
    { name:"에너지", companies:["LG에너지솔루션","포스코홀딩스"], avgPer:41.2, avgPbr:2.15, outlook:"부정" },
    { name:"소비재", companies:["LG생활건강","아모레퍼시픽"], avgPer:28.4, avgPbr:1.73, outlook:"중립" },
  ];
  const outlookColor = o => o==="긍정" ? "var(--green)" : o==="부정" ? "var(--red)" : "var(--text-mute)";
  document.getElementById("co-sector-cards").innerHTML = sectors.map(s => `
    <div class="card">
      <div class="flex items-center justify-between mb-3">
        <span class="font-semibold">${escHtml(s.name)}</span>
        <span style="font-size:11px; font-weight:700; color:${outlookColor(s.outlook)};">${s.outlook}</span>
      </div>
      <div class="text-xs" style="color:var(--text-mute); margin-bottom:8px;">${s.companies.join(" · ")}</div>
      <div class="flex gap-4">
        <div><div class="text-xs" style="color:var(--text-mute);">평균 PER</div><div style="font-size:16px; font-weight:700;">${s.avgPer}x</div></div>
        <div><div class="text-xs" style="color:var(--text-mute);">평균 PBR</div><div style="font-size:16px; font-weight:700;">${s.avgPbr}x</div></div>
      </div>
    </div>
  `).join("");
}

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
