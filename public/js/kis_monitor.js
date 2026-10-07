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
function ts(s) { return s ? escHtml(String(s).replace("T", " ").slice(0, 19)) : "-"; }

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

function renderHoldings(d) {
  const el = document.getElementById("kism-holdings"), note = document.getElementById("kism-holdings-note"); if (!el) return;
  const a = d.account || {};
  if (!a.connected) { el.innerHTML = `<div style="color:var(--text-mute);">KIS 계좌 미연동 ${escHtml(a.error || "")}</div>`; return; }
  const rows = (a.holdings || []).slice().sort((x, y) => (y.bot_managed - x.bot_managed) || (y.eval_amount - x.eval_amount));
  if (note) note.textContent = `· ${rows.length}종목 (★ = 봇 관리)`;
  el.innerHTML = rows.length ? `<table class="w-full"><thead><tr style="color:var(--text-mute);"><th class="text-left">종목</th><th class="text-right">수량</th><th class="text-right">봇수량</th><th class="text-right">평균단가</th><th class="text-right">현재가</th><th class="text-right">평가</th><th class="text-right">손익</th></tr></thead><tbody>${
    rows.map(h => `<tr style="border-top:1px solid var(--border);${h.bot_managed ? "" : "opacity:.65;"}">
      <td>${h.bot_managed ? "★ " : ""}${escHtml(h.name || h.symbol)} <span style="color:var(--text-mute);">${escHtml(h.symbol)}</span></td>
      <td class="text-right">${fmt(h.quantity)}</td><td class="text-right">${h.bot_quantity ? fmt(h.bot_quantity) : "-"}</td>
      <td class="text-right">${fmt(h.avg_price)}</td><td class="text-right">${fmt(h.current_price)}</td><td class="text-right">${fmt(h.eval_amount)}</td>
      <td class="text-right" style="color:${pnlColor(h.profit_loss)}">${fmt(h.profit_loss)} (${Number(h.profit_loss_rate || 0).toFixed(2)}%)</td></tr>`).join("")
  }</tbody></table>` : `<div style="color:var(--text-mute);">보유 종목 없음</div>`;
}

function renderRecon(d) {
  const el = document.getElementById("kism-recon"), note = document.getElementById("kism-recon-note"); if (!el) return;
  const r = d.reconcile;
  if (!r) { el.innerHTML = `<div style="color:var(--text-mute);">아직 점검 결과가 없습니다 (10분마다 실행).</div>`; return; }
  if (note) note.textContent = `· ${ts(r.checked_at)} · 기준선 ${escHtml(String(r.summary?.baseline || "-"))}`;
  if (r.ok === null) { el.innerHTML = `<div style="color:var(--text-mute);">${escHtml(r.summary?.note || "점검 불가")}</div>`; return; }
  if (r.ok) { el.innerHTML = `<div>${badge("로그와 KIS 실거래 일치", "ok")} <span style="color:var(--text-mute);">가상 ${escHtml(JSON.stringify(r.summary?.virtual || {}))} · 봇 체결 ${escHtml(JSON.stringify(r.summary?.bot_net_filled || {}))}</span></div>`; return; }
  el.innerHTML = (r.issues || []).slice(0, 20).map(i => {
    const rest = Object.entries(i).filter(([k]) => !["type", "symbol", "detail"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
    return `<div class="rounded p-2" style="background:var(--surf2);border:1px solid var(--border);">${badge(i.type, "bad")} <b>${escHtml(i.symbol || "")}</b> <span style="color:var(--text-dim);">${escHtml(rest)}</span>${i.detail ? `<div style="color:var(--text-mute);">${escHtml(i.detail)}</div>` : ""}</div>`;
  }).join("");
}

function renderOrders(d) {
  const el = document.getElementById("kism-orders"), note = document.getElementById("kism-orders-note"); if (!el) return;
  const o = d.orders || {}; const rows = o.recent || [];
  if (note) note.textContent = `· 최근 ${rows.length}건 · 당일 상태 ${Object.entries(o.today_counts || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "없음"}`;
  el.innerHTML = rows.length ? `<table class="w-full"><thead><tr style="color:var(--text-mute);"><th class="text-left">시각</th><th class="text-left">종목</th><th>방향</th><th>유형</th><th class="text-right">수량</th><th class="text-right">가상가</th><th class="text-right">체결가</th><th>상태</th><th class="text-left">메모</th></tr></thead><tbody>${
    rows.map(r => `<tr style="border-top:1px solid var(--border);">
      <td>${ts(r.created_at)}${r.owner === "batch" ? "" : " <span style='color:var(--text-mute);'>(내 계정)</span>"}</td>
      <td>${escHtml(r.name || r.symbol)} <span style="color:var(--text-mute);">${escHtml(r.symbol)}</span></td>
      <td class="text-center"><span class="${r.side === "BUY" ? "badge-buy" : "badge-sell"}">${escHtml(r.side)}</span></td>
      <td class="text-center">${escHtml(r.order_type || "")}</td>
      <td class="text-right">${fmt(r.quantity)}${r.filled_quantity ? ` <span style="color:var(--text-mute);">/${fmt(r.filled_quantity)}</span>` : ""}</td>
      <td class="text-right">${fmt(r.price)}</td><td class="text-right">${r.avg_filled_price ? fmt(r.avg_filled_price) : "-"}</td>
      <td class="text-center">${badge(r.status, STATUS_KIND[r.status] ?? "")}</td>
      <td style="color:var(--text-dim);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(r.message || "")}">${escHtml(r.message || "")}</td></tr>`).join("")
  }</tbody></table>` : `<div style="color:var(--text-mute);">실주문 기록이 없습니다.</div>`;
}

function renderCycles(d) {
  const el = document.getElementById("kism-cycles"), note = document.getElementById("kism-cycles-note"); if (!el) return;
  const c = d.cycles || {}; const rows = c.recent || [];
  if (note) note.textContent = `· 누적 ${c.count ?? 0} · 마지막 ${ts(c.last_time)}${d.last_beat_run ? ` · beat ${ts(d.last_beat_run.time)} (실행 ${d.last_beat_run.ran}, 실패 ${d.last_beat_run.failed})` : ""}`;
  el.innerHTML = rows.length ? rows.map(cy => {
    const trades = (cy.trades || []).map(t => `<span class="${t.action === "buy" ? "badge-buy" : "badge-sell"}">${escHtml(t.action === "buy" ? "매수" : "매도")} ${escHtml(t.name || t.symbol)} ${fmt(t.quantity)}주 @${fmt(t.price)}</span> <span style="color:${t.live === "submitted" ? "var(--text-dim)" : "var(--down, #e5484d)"};">실주문 ${escHtml(t.live || "없음")}${t.live_reason ? `(${escHtml(t.live_reason)})` : ""}</span>`).join(" · ");
    return `<div class="rounded p-2" style="background:var(--surf2);border:1px solid var(--border);">
      <div><b>${ts(cy.time)}</b> <span style="color:var(--text-mute);">대상 ${(cy.symbols || []).length}종목 · 매수계획 ${(cy.buy || []).length} · 매도계획 ${Object.keys(cy.sell || {}).length} · 생략 ${cy.skipped}${cy.halted ? ` · <span class="badge-sell">비상 정지: ${escHtml(cy.halt_reason || "")}</span>` : ""}${cy.equity != null ? ` · 가상 평가 ${fmt(cy.equity)}원` : ""}</span></div>
      ${trades ? `<div class="mt-1">${trades}</div>` : `<div class="mt-1" style="color:var(--text-mute);">거래 없음</div>`}
      ${(cy.notes || []).length ? `<div class="mt-1" style="color:var(--text-dim);">${cy.notes.map(escHtml).join(" · ")}</div>` : ""}
    </div>`;
  }).join("") : `<div style="color:var(--text-mute);">사이클 기록이 없습니다.</div>`;
}

export async function loadKisMonitor() {
  if (_loading) return; _loading = true;
  try {
    const d = await api("/api/quant/kis/monitor");
    renderBadges(d); renderKpis(d); renderHoldings(d); renderRecon(d); renderOrders(d); renderCycles(d);
    const u = document.getElementById("kism-updated"); if (u) u.textContent = `갱신 ${ts(d.checked_at)} (UTC) · 사이클 ${Math.round((d.cycle_sec || 180) / 60)}분`;
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
