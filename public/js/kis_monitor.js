/* KIS 모의투자결과 — 로보 어드바이저 > KIS 모의투자결과 (data-view="kis-monitor"). 데이터: GET /api/quant/kis/monitor */
import { api, escHtml, fmt } from "/js/common.js";

let _timer = null;
let _loading = false;

function badge(text, kind) {
  const cls = kind === "ok" ? "badge-buy" : kind === "bad" ? "badge-sell" : "";
  const style = cls ? "" : "background:var(--surf2);border:1px solid var(--border);padding:2px 8px;border-radius:999px;";
  return `<span class="${cls}" style="${style}">${escHtml(text)}</span>`;
}

function kpi(label, value, sub = "", color = "") {
  return `<div class="rounded-lg p-3" style="background:var(--surf2);border:1px solid var(--border);">
    <div class="text-xs" style="color:var(--text-mute);">${escHtml(label)}</div>
    <div class="text-lg font-semibold" style="${color ? `color:${color};` : ""}">${value}</div>
    ${sub ? `<div class="text-xs" style="color:var(--text-dim);">${sub}</div>` : ""}
  </div>`;
}

const STATUS_KIND = { FILLED: "ok", ACCEPTED: "", PARTIALLY_FILLED: "", PENDING: "", CANCEL_REQUESTED: "", CANCELLED: "", UNKNOWN: "bad", LOST: "bad", ERROR: "bad", REJECTED: "bad" };

function pnlColor(v) { return v > 0 ? "var(--up, #e5484d)" : v < 0 ? "var(--down, #2962ff)" : ""; }
function won(v) { return v == null ? "-" : `${fmt(v)}원`; }
const kisTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});
function kisDate(value) {
  if (value == null || value === "") return null;
  // Backend timestamps without an explicit offset are UTC as well.
  const raw = String(value).trim();
  const normalized = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(raw) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? `${raw}Z` : raw;
  const date = new Date(normalized);
  return Number.isFinite(date.getTime()) ? date : null;
}
export function formatKisTime(value) {
  const date = kisDate(value); if (!date) return "-";
  const parts = Object.fromEntries(kisTimeFormatter.formatToParts(date).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function ts(s) { return escHtml(formatKisTime(s)); }
const timeColumn = (field) => textColumn(field, "시각 (KST)", {
  width: 190, valueFormatter: ({ value }) => formatKisTime(value),
  tooltipValueGetter: ({ value }) => `${formatKisTime(value)} (KST, UTC+9)`,
  comparator: (a, b) => (kisDate(a)?.getTime() ?? 0) - (kisDate(b)?.getTime() ?? 0),
});

function renderBadges(d) {
  const el = document.getElementById("kism-badges"); if (!el) return;
  const b = d.batch || {}, ag = d.aggressive || {};
  const hb = d.heartbeat_age_sec;
  const items = [
    badge(`환경 ${d.environment === "paper" ? "모의(Testbed)" : "실전"}`, d.environment === "paper" ? "ok" : "bad"),
    badge(b.running ? "배치 실행 중" : (b.enabled ? "배치 대기" : "배치 OFF"), b.running ? "ok" : "bad"),
    b.kill_switch ? badge(`비상 정지: ${b.kill_reason || ""}`, "bad") : "",
    badge(ag.enabled ? `공격 모드 ${ag.interval} · 익절 +${ag.take_profit_pct}% / 손절 -${ag.stop_loss_pct}% · 시장가` : "기본 모드(일봉)", ag.enabled ? "ok" : ""),
    badge(hb == null ? "heartbeat 없음" : hb <= 180 ? `beat 정상 (${Math.round(hb)}s 전)` : `beat 지연 ${Math.round(hb)}s`, hb != null && hb <= 180 ? "ok" : "bad"),
    badge(`시세 ${d.market_data_source === "kis" ? "KIS 실시간" : "Yahoo"}`),
    badge(`유니버스 ${d.universe?.count ?? "-"}종목 · ${(d.universe?.sectors || []).join("/")}`),
    d.gateway_configured ? "" : badge("게이트웨이 미설정", "bad"),
  ];
  el.innerHTML = items.filter(Boolean).join(" ");
}

function renderKpis(d) {
  const el = document.getElementById("kism-kpis"); if (!el) return;
  const a = d.account || {}, o = d.orders || {}, rp = o.realized_pnl || {};
  const recon = d.reconcile;
  el.innerHTML = [
    kpi("KIS 총평가", a.connected ? won(a.total) : "미연동", a.connected ? `현금 ${won(a.cash)} · 평가 ${won(a.invested)}` : (a.error || "")),
    kpi("계좌 평가손익", a.connected ? won(a.profit_loss) : "-", "KIS 기준(기존 보유 포함)", pnlColor(a.profit_loss)),
    kpi("봇 실현손익", won(rp.total), `체결 완료 매도 기준 · 종목 ${Object.keys(rp.by_symbol || {}).length}`, pnlColor(rp.total)),
    kpi("당일 실주문", `${o.today_total ?? 0}건`, `체결 ${o.today_filled ?? 0} · 체결률 ${o.fill_rate_pct == null ? "-" : o.fill_rate_pct + "%"}`),
    kpi("당일 매수/매도", `${won(o.today_buy_amount)}`, `매도 ${won(o.today_sell_amount)} · 슬리피지 ${o.avg_slippage_pct == null ? "-" : o.avg_slippage_pct + "%"}`),
    kpi("미해소 주문", `${(o.unresolved || []).length}건`, "UNKNOWN / LOST / ERROR", (o.unresolved || []).length ? "var(--down, #e5484d)" : ""),
    kpi("봇 보유 종목", a.connected ? `${a.bot_positions ?? 0} / ${a.positions ?? 0}` : "-", "봇 관리 / 전체 보유"),
    kpi("정합성", recon == null ? "미점검" : recon.ok ? "일치" : `불일치 ${(recon.issues || []).length}`, recon?.checked_at ? ts(recon.checked_at) : "", recon && recon.ok === false ? "var(--down, #e5484d)" : ""),
  ].join("");
}

const grids = new Map();
const numberFormat = ({ value }) => value == null ? "-" : fmt(value);
const numericColumn = (field, headerName, extra = {}) => ({ field, headerName, filter: "agNumberColumnFilter", valueFormatter: numberFormat, cellStyle: { textAlign: "right" }, ...extra });
const textColumn = (field, headerName, extra = {}) => ({ field, headerName, filter: "agTextColumnFilter", ...extra });
function textCell(value, color = "") {
  const el = document.createElement("span"); el.textContent = value ?? "-"; if (color) el.style.color = color; return el;
}
function updateGrid(id, columns, rows, emptyMessage, resetPage = false) {
  const el = document.getElementById(id); if (!el) return null;
  let grid = grids.get(id);
  if (!grid) {
    if (!window.agGrid) throw new Error("AG Grid를 불러오지 못했습니다. 페이지를 새로고침하세요.");
    const css = getComputedStyle(document.documentElement);
    grid = agGrid.createGrid(el, {
      theme: agGrid.themeQuartz.withParams({ backgroundColor: css.getPropertyValue("--surf").trim() || "#fff", foregroundColor: css.getPropertyValue("--text").trim() || "#0f172a", borderColor: css.getPropertyValue("--border").trim() || "#e0e3eb", accentColor: css.getPropertyValue("--accent").trim() || "#2962ff", fontFamily: "inherit", fontSize: 12, spacing: 6 }),
      columnDefs: columns, rowData: [],
      defaultColDef: { sortable: true, resizable: true, minWidth: 110, width: 145, tooltipValueGetter: ({ value }) => value == null ? "" : String(value) },
      pagination: true, paginationPageSize: 10, paginationPageSizeSelector: false,
      paginationPanels: ["rowSummary", "pageSummary"],
      domLayout: "autoHeight", rowHeight: 40, headerHeight: 44,
      suppressMovableColumns: true,
      overlayNoRowsTemplate: "<span></span>",
      localeText: { noRowsToShow: "데이터 없음", page: "페이지", of: "/", to: "–", more: "더 보기", firstPage: "첫 페이지", lastPage: "마지막 페이지", nextPage: "다음 페이지", previousPage: "이전 페이지", pageSizeSelectorLabel: "페이지당", filterOoo: "필터…", contains: "포함", notContains: "미포함", equals: "같음", notEqual: "다름", startsWith: "시작", endsWith: "끝", lessThan: "미만", greaterThan: "초과", lessThanOrEqual: "이하", greaterThanOrEqual: "이상", inRange: "범위", blank: "빈 값", notBlank: "값 있음", andCondition: "그리고", orCondition: "또는" },
    });
    grids.set(id, grid);
  }
  const page = resetPage ? 0 : grid.paginationGetCurrentPage();
  grid.setGridOption("overlayNoRowsTemplate", `<span>${escHtml(emptyMessage)}</span>`);
  grid.setGridOption("rowData", rows);
  grid.paginationGoToPage(Math.min(page, Math.max(0, grid.paginationGetTotalPages() - 1)));
  return grid;
}

let currentHoldings = null;
function holdingSymbol(symbol) {
  const raw = String(symbol || "").trim().toUpperCase();
  return raw.match(/^(?:KRX:)?(\d{6})(?:\.(?:KS|KQ))?$/)?.[1] || raw;
}
function currentQuantity(symbol) {
  if (currentHoldings === null) return null;
  const key = holdingSymbol(symbol);
  return currentHoldings.has(key) ? currentHoldings.get(key) : 0;
}
function renderHoldings(d) {
  const a = d.account || {}, note = document.getElementById("kism-holdings-note");
  currentHoldings = a.connected && Array.isArray(a.holdings) ? new Map(a.holdings.map(h => [holdingSymbol(h.symbol), h.quantity == null || !Number.isFinite(Number(h.quantity)) ? null : Number(h.quantity)])) : null;
  const rows = a.connected ? (a.holdings || []).slice().sort((x, y) => (y.bot_managed - x.bot_managed) || (y.eval_amount - x.eval_amount)) : [];
  if (note) note.textContent = `· ${rows.length}종목 · 페이지당 10건 (★ = 봇 관리)`;
  updateGrid("kism-holdings", [
    textColumn("name", "종목", { width: 190, cellRenderer: ({ data }) => textCell(`${data.bot_managed ? "★ " : ""}${data.name || data.symbol}`) }),
    textColumn("symbol", "종목코드"), numericColumn("quantity", "수량"), numericColumn("bot_quantity", "봇수량"),
    numericColumn("avg_price", "평균단가"), numericColumn("current_price", "현재가"), numericColumn("eval_amount", "평가금액"),
    numericColumn("profit_loss", "평가손익", { cellStyle: ({ value }) => ({ textAlign: "right", color: pnlColor(value) }) }),
    numericColumn("profit_loss_rate", "손익률 (%)", { valueFormatter: ({ value }) => value == null ? "-" : Number(value).toFixed(2) }),
  ], rows, a.connected ? "보유 종목 없음" : `KIS 계좌 미연동 ${a.error || ""}`);
}

function renderRecon(d) {
  const r = d.reconcile, note = document.getElementById("kism-recon-note"), summary = document.getElementById("kism-recon-summary");
  if (note) note.textContent = r ? `· ${ts(r.checked_at)} (KST) · 기준선 ${String(r.summary?.baseline || "-")}` : "";
  if (summary) summary.textContent = !r ? "아직 점검 결과가 없습니다 (10분마다 실행)." : r.ok === null ? (r.summary?.note || "점검 불가") : r.ok ? `로그와 KIS 실거래 일치 · 가상 ${JSON.stringify(r.summary?.virtual || {})} · 봇 체결 ${JSON.stringify(r.summary?.bot_net_filled || {})}` : `정합성 불일치 ${(r.issues || []).length}건 · 페이지당 10건`;
  const rows = (r?.issues || []).map(i => ({ ...i, values: Object.entries(i).filter(([k]) => !["type", "symbol", "detail"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ") }));
  updateGrid("kism-recon", [textColumn("type", "문제 유형", { width: 180 }), textColumn("symbol", "종목코드"), textColumn("values", "비교 값", { width: 300 }), textColumn("detail", "상세", { width: 300 })], rows, !r ? "점검 결과 없음" : r.ok === null ? "점검 불가" : "정합성 문제 없음");
}

function renderOrdersSummary(d) {
  const note = document.getElementById("kism-orders-note"); if (!note) return;
  const o = d.orders || {}; const oc = o.today_owner_counts || {};
  const statusText = Object.entries(o.today_counts || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "없음";
  note.textContent = `· 당일 ${o.today_total ?? 0}건 (봇 ${oc.batch ?? 0} · 사용자 ${oc.me ?? 0}) · 상태 ${statusText}`;
}

/* ── 실주문 검색 그리드: GET /api/quant/kis/orders ─────────────────────────────── */
const ORDER_COLUMNS = [
  { ...timeColumn("created_at"), sort: "desc" },
  textColumn("owner_label", "구분", { width: 115, cellRenderer: ({ data }) => textCell(data.owner_label || (data.owner === "batch" ? "봇(배치)" : "사용자"), data.owner === "batch" ? "#6366f1" : "#15803d") }),
  textColumn("name", "종목", { width: 175, valueGetter: ({ data }) => data?.name || data?.symbol }), textColumn("symbol", "종목코드"),
  textColumn("side", "방향", { width: 110, cellRenderer: ({ value }) => textCell(value, value === "BUY" ? "#15803d" : "#dc2626") }),
  textColumn("order_type", "유형"), numericColumn("quantity", "주문수량"), numericColumn("filled_quantity", "체결수량"),
  numericColumn("current_quantity", "현 보유수량", { headerTooltip: "최근 KIS 계좌 조회 기준의 종목 전체 보유수량 (기존 보유 포함). 미보유는 0, 계좌 조회 불가는 -입니다.", tooltipValueGetter: ({ value }) => value == null ? "계좌 보유수량 조회 불가" : `최근 KIS 계좌 조회 기준 ${fmt(value)}주 (기존 보유 포함)` }),
  numericColumn("price", "가상가"), numericColumn("avg_filled_price", "체결가"),
  textColumn("status", "상태", { width: 165, cellRenderer: ({ value }) => textCell(value, STATUS_KIND[value] === "bad" ? "#dc2626" : STATUS_KIND[value] === "ok" ? "#15803d" : "") }),
  textColumn("order_no", "주문번호", { width: 180, tooltipValueGetter: ({ data }) => data.client_order_id || data.order_no || "" }),
  textColumn("message", "메모", { width: 300 }),
];
const ordersState = { rows: [], total: 0, counts_by_owner: {}, counts_by_status: {}, truncated: false };
let ordersRun = 0;
function readOrderFilters() {
  const v = id => document.getElementById(id)?.value ?? "";
  return { owner: v("kism-o-owner") || "all", status: v("kism-o-status"), side: v("kism-o-side"), date_from: v("kism-o-from"), date_to: v("kism-o-to"), q: v("kism-o-q").trim() };
}
function sortedOrderRows() {
  const rows = [], grid = grids.get("kism-orders");
  if (grid) grid.forEachNodeAfterFilterAndSort(node => rows.push(node.data));
  return rows;
}
function renderOrdersTable(resetPage = false) {
  const st = ordersState, result = document.getElementById("kism-orders-result");
  const oc = st.counts_by_owner || {}, statusText = Object.entries(st.counts_by_status || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "없음";
  if (result) result.textContent = `검색 결과 ${fmt(st.total)}건 (봇 ${fmt(oc.batch ?? 0)} · 사용자 ${fmt(oc.me ?? 0)}) · 상태 ${statusText} · 페이지당 10건${st.truncated ? " · 최근 2,000건까지만 검색" : ""}`;
  updateGrid("kism-orders", ORDER_COLUMNS, st.rows.map(r => ({ ...r, current_quantity: currentQuantity(r.symbol) })), "조건에 맞는 실주문이 없습니다.", resetPage);
}
export async function loadKisOrders({ resetPage = true } = {}) {
  const run = ++ordersRun, f = readOrderFilters(), collected = [];
  const result = document.getElementById("kism-orders-result");
  if (result) result.textContent = "실주문 검색 중…";
  if (resetPage) updateGrid("kism-orders", ORDER_COLUMNS, [], "검색 중…", true);
  try {
    let offset = 0, first;
    do {
      const params = new URLSearchParams({ ...f, limit: "500", offset: String(offset) });
      const res = await api(`/api/quant/kis/orders?${params}`);
      if (run !== ordersRun) return;
      if (!first) first = res;
      const rows = res.rows || [];
      collected.push(...rows); offset += rows.length;
      if (!rows.length) break;
    } while (offset < Math.min(first.total || 0, 2000));
    if (run !== ordersRun) return;
    // Pagination and sorting apply to the complete search result, not a single API page.
    const rows = [...new Map(collected.map(r => [r.id || r.client_order_id || JSON.stringify(r), r])).values()];
    Object.assign(ordersState, { rows, total: first.total || 0, counts_by_owner: first.counts_by_owner || {}, counts_by_status: first.counts_by_status || {}, truncated: Boolean(first.truncated) });
    renderOrdersTable(resetPage);
  } catch (e) {
    if (run !== ordersRun) return;
    ordersState.rows = [];
    updateGrid("kism-orders", ORDER_COLUMNS, [], "실주문 검색 실패", true);
    if (result) result.textContent = `실주문 검색 실패: ${e?.message || e}`;
  }
}

function downloadOrdersCsv() {
  const rows = sortedOrderRows(); if (!rows.length) return;
  const cols = ["created_at", "owner_label", "symbol", "name", "side", "order_type", "quantity", "filled_quantity", "current_quantity", "price", "avg_filled_price", "status", "order_no", "client_order_id", "environment", "message"];
  const cell = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = "\ufeff" + [cols.map(c => c === "created_at" ? "created_at_kst" : c).join(","), ...rows.map(r => cols.map(c => cell(c === "created_at" ? formatKisTime(r[c]) : r[c])).join(","))].join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = `kis-orders-${formatKisTime(new Date().toISOString()).slice(0, 10)}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

document.getElementById("kism-orders-form")?.addEventListener("submit", e => { e.preventDefault(); loadKisOrders({ resetPage: true }); });
["kism-o-owner", "kism-o-status", "kism-o-side", "kism-o-from", "kism-o-to"].forEach(id => document.getElementById(id)?.addEventListener("change", () => loadKisOrders({ resetPage: true })));
document.getElementById("kism-o-reset")?.addEventListener("click", () => {
  ["kism-o-status", "kism-o-side", "kism-o-from", "kism-o-to", "kism-o-q"].forEach(id => { const n = document.getElementById(id); if (n) n.value = ""; });
  const owner = document.getElementById("kism-o-owner"); if (owner) owner.value = "all";
  loadKisOrders({ resetPage: true });
});
document.getElementById("kism-o-csv")?.addEventListener("click", downloadOrdersCsv);

function renderCycles(d) {
  const c = d.cycles || {}, note = document.getElementById("kism-cycles-note");
  const rows = (c.recent || []).map(cy => ({ ...cy,
    symbol_count: (cy.symbols || []).length, buy_count: (cy.buy || []).length, sell_count: Object.keys(cy.sell || {}).length,
    halt_status: cy.halted ? `비상 정지: ${cy.halt_reason || ""}` : "정상",
    trade_details: (cy.trades || []).map(t => `${t.action === "buy" ? "매수" : "매도"} ${t.name || t.symbol} ${fmt(t.quantity)}주 @${fmt(t.price)} · 실주문 ${t.live || "없음"}${t.live_reason ? ` (${t.live_reason})` : ""}`).join(" · ") || "거래 없음",
    note_text: (cy.notes || []).join(" · "),
  }));
  if (note) note.textContent = `· 누적 ${c.count ?? 0} · 최근 ${rows.length}건 표시 · 페이지당 10건 · 마지막 ${ts(c.last_time)} (KST)${d.last_beat_run ? ` · beat ${ts(d.last_beat_run.time)} (KST) (실행 ${d.last_beat_run.ran}, 실패 ${d.last_beat_run.failed})` : ""}`;
  updateGrid("kism-cycles", [
    timeColumn("time"),
    numericColumn("symbol_count", "대상 종목 수"), numericColumn("buy_count", "매수계획"), numericColumn("sell_count", "매도계획"), numericColumn("skipped", "생략"),
    textColumn("halt_status", "실행 상태", { width: 200 }), numericColumn("equity", "가상 평가 (원)"),
    textColumn("trade_details", "거래 내역", { width: 420 }), textColumn("note_text", "비고", { width: 350 }),
  ], rows, "사이클 기록이 없습니다.");
}

export async function loadKisMonitor() {
  if (_loading) return; _loading = true;
  try {
    const d = await api("/api/quant/kis/monitor?cycles=48");
    renderBadges(d); renderKpis(d); renderHoldings(d); renderRecon(d); renderOrdersSummary(d); renderCycles(d);
    await loadKisOrders({ resetPage: false });   // 현재 검색 조건·페이지 유지한 채 그리드 갱신
    const u = document.getElementById("kism-updated"); if (u) u.textContent = `갱신 ${ts(d.checked_at)} (KST, UTC+9) · 사이클 ${Math.round((d.cycle_sec || 180) / 60)}분`;
  } catch (e) {
    const el = document.getElementById("kism-badges"); if (el) el.innerHTML = badge(`불러오기 실패: ${e?.message || e}`, "bad");
  } finally { _loading = false; }
}

function setAuto(on) {
  if (_timer) { clearInterval(_timer); _timer = null; }
  if (on) _timer = setInterval(() => { if (location.hash.replace("#", "") === "kis-monitor") loadKisMonitor(); }, 60_000);
}

document.getElementById("kism-refresh")?.addEventListener("click", loadKisMonitor);
document.getElementById("kism-auto")?.addEventListener("change", e => setAuto(e.target.checked));
document.addEventListener("lumina:view-changed", e => {
  const on = e.detail?.view === "kis-monitor" && (document.getElementById("kism-auto")?.checked ?? true);
  setAuto(on);
});
