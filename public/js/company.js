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

const CO_DATA = {
  "005930": {
    price:73400, chg:1.43, cap:4378000, per:16.2, pbr:1.45, eps:4532, bps:50600,
    roe:9.1, roa:5.2, debt:36.4, div:1416, divYield:1.93, opMargin:12.4,
    revenue:[302300,258940,305100,320400], op:[35200,8490,25400,34900],
    net:[29600,7280,21800,28700], quarters:["23Q2","23Q3","23Q4","24Q1"],
    assets:4263000, equity:3012000, liabilities:1251000, cash:98400,
  },
  "000660": {
    price:192800, chg:2.11, cap:1403000, per:28.4, pbr:2.18, eps:6789, bps:88400,
    roe:7.8, roa:4.9, debt:41.2, div:1200, divYield:0.62, opMargin:8.7,
    revenue:[102300,91240,108700,119600], op:[8900,3280,9870,12400],
    net:[7200,2800,8100,10300], quarters:["23Q2","23Q3","23Q4","24Q1"],
    assets:963000, equity:663000, liabilities:300000, cash:42300,
  },
  "035420": {
    price:198500, chg:-0.91, cap:325000, per:32.1, pbr:3.42, eps:6180, bps:58000,
    roe:10.6, roa:6.8, debt:22.1, div:948, divYield:0.48, opMargin:18.2,
    revenue:[22100,23400,25600,27800], op:[3980,4310,4740,5120],
    net:[3100,3380,3720,4010], quarters:["23Q2","23Q3","23Q4","24Q1"],
    assets:263000, equity:188000, liabilities:75000, cash:31800,
  },
  "005380": {
    price:214500, chg:1.61, cap:457000, per:5.8, pbr:0.72, eps:37000, bps:297000,
    roe:12.4, roa:5.9, debt:148.3, div:8000, divYield:3.73, opMargin:9.1,
    revenue:[403800,422100,465200,498700], op:[36400,38400,42700,45400],
    net:[33800,35600,40100,43200], quarters:["23Q2","23Q3","23Q4","24Q1"],
    assets:2743000, equity:630000, liabilities:2113000, cash:78200,
  },
  "105560": {
    price:78200, chg:0.38, cap:330000, per:6.4, pbr:0.58, eps:12200, bps:134000,
    roe:9.2, roa:0.6, debt:872.1, div:3060, divYield:3.91, opMargin:null,
    revenue:[154200,162300,178400,189700], op:[18400,19100,21200,22800],
    net:[14200,14900,16700,18100], quarters:["23Q2","23Q3","23Q4","24Q1"],
    assets:6824000, equity:407000, liabilities:6417000, cash:null,
  },
};

// 대시보드의 종목 선택은 위 5개 목업 데이터에 묶여있지 않고, 검색으로 아무 종목이나
// 추가해 실제 데이터(Yahoo Finance)를 조회한다. COMPANIES/CO_DATA는 "지표 비교 분석"
// 탭에서만 계속 쓴다.
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

function loadCompanyCompare() {
  const cols = ["PER","PBR","ROE","ROA","부채비율","배당수익률","영업이익률"];
  const getVal = (d, col) => ({
    "PER":`${d.per}x`, "PBR":`${d.pbr}x`,
    "ROE":`${d.roe}%`, "ROA":`${d.roa}%`,
    "부채비율":`${d.debt}%`, "배당수익률":`${d.divYield}%`,
    "영업이익률": d.opMargin ? `${d.opMargin}%` : "N/A",
  }[col]);
  const el = document.getElementById("co-compare-table");
  el.innerHTML = `<table>
    <thead><tr><th>지표</th>${COMPANIES.map(c=>`<th style="text-align:right;">${escHtml(c.name)}</th>`).join("")}</tr></thead>
    <tbody>${cols.map(col=>`
      <tr><td style="color:var(--text-dim);font-size:12px;font-weight:600;">${col}</td>
      ${COMPANIES.map(c=>`<td style="text-align:right;font-weight:600;">${getVal(CO_DATA[c.code],col)}</td>`).join("")}
      </tr>`).join("")}
    </tbody>
  </table>`;
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
