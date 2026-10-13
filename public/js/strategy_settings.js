/* 전략설정 (GNB 전략설정) — data-view="strategy-settings" / "strategy-performance"
 * 데이터: GET/POST /api/quant/strategy/settings, POST /api/quant/strategy/settings/reset, GET /api/quant/strategy/performance
 * 대상은 항상 KIS 모의투자 배치(시스템 사용자) 행. 저장하면 서버 env 를 덮어써(data_cache) 다음 사이클부터 적용된다. */
import { api, escHtml, fmt, setToast } from "/js/common.js";
import { formatKisTime } from "/js/kis_monitor.js";

const $ = (id) => document.getElementById(id);
let _cfg = null;            // GET /settings 응답
let _draft = {};            // 화면 입력값 (키 → 값)
let _perf = null;

// ── 공통 ──────────────────────────────────────────────────────────
function badge(text, kind) {
  const cls = kind === "ok" ? "badge-buy" : kind === "bad" ? "badge-sell" : "";
  const style = cls ? "" : "background:var(--surf2);border:1px solid var(--border);padding:2px 8px;border-radius:999px;";
  return `<span class="${cls}" style="${style}">${escHtml(text)}</span>`;
}
function pnlColor(v) { return v > 0 ? "var(--up, #e5484d)" : v < 0 ? "var(--down, #2962ff)" : ""; }
function won(v) { return v == null ? "-" : `${fmt(v)}원`; }
function pct(v) { return v == null ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`; }
function ts(v) { return v ? formatKisTime(v) : "-"; }
function fmtVal(f, v) {
  if (v == null || v === "") return f.type === "str" ? "(비움)" : f.key === "strategy_id" ? "기본 규칙" : "-";
  if (f.type === "bool") return v ? "ON" : "OFF";
  if (f.type === "float" || f.type === "int") return `${fmt(v, f.type === "float" && !Number.isInteger(Number(v)) ? 2 : 0)}${f.unit || ""}`;
  return String(v);
}

// ── 설정 화면 ────────────────────────────────────────────────────
function currentValue(f) {
  if (f.source === "row") return _cfg.row?.[f.key];
  return _cfg.effective?.[f.key];
}

function inputFor(f) {
  const v = _draft[f.key];
  const id = `ss-in-${f.key}`;
  if (f.type === "bool") return `<label class="ss-toggle"><input type="checkbox" id="${id}" data-key="${f.key}" ${v ? "checked" : ""}> <span>${v ? "ON" : "OFF"}</span></label>`;
  if (f.type === "enum") {
    let opts;
    if (f.key === "strategy_id") {
      const list = _cfg.strategies || [];
      opts = [`<option value="">기본 규칙 (일봉 지표 점수)</option>`, ...list.map(st => `<option value="${escHtml(st.strategy_id)}" ${st.strategy_id === v ? "selected" : ""}>${escHtml(st.name || st.strategy_id)} v${st.version} · 종목 ${st.universe_size ?? "-"} · 거래 ${st.backtest_result?.trade_count ?? "-"}건</option>`)];
      if (v && !list.some(st => st.strategy_id === v)) opts.push(`<option value="${escHtml(v)}" selected>${escHtml(v)} (목록에 없음)</option>`);
    } else opts = f.choices.map(c => `<option value="${escHtml(c)}" ${c === v ? "selected" : ""}>${escHtml(c)}</option>`);
    return `<select id="${id}" class="input text-xs" style="padding:6px 8px;" data-key="${f.key}">${opts.join("")}</select>`;
  }
  const step = f.type === "int" ? 1 : (f.max != null && f.max <= 1 ? 0.05 : 0.1);
  if (f.type === "str") return `<input id="${id}" class="input text-xs" style="padding:6px 8px;" data-key="${f.key}" value="${escHtml(v ?? "")}" placeholder="비우면 AI 추천">`;
  return `<input id="${id}" type="number" class="input text-xs" style="padding:6px 8px;" data-key="${f.key}" value="${v ?? ""}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} step="${step}">`;
}

function metaFor(f) {
  const parts = [];
  if (f.source === "env") {
    const overridden = Object.prototype.hasOwnProperty.call(_cfg.overrides || {}, f.key);
    parts.push(`서버 기본값 <b>${escHtml(fmtVal(f, f.default))}</b>${overridden ? " · <b>화면값으로 덮어쓰는 중</b>" : ""}`);
  } else parts.push("저장 위치: 배치 행(broker_settings)");
  if (f.min != null || f.max != null) parts.push(`범위 ${f.min ?? ""}~${f.max ?? ""}${f.unit || ""}`);
  if (f.note) parts.push(escHtml(f.note));
  return parts.join(" · ");
}

function renderGroups() {
  const el = $("ss-groups"); if (!el || !_cfg) return;
  const byGroup = new Map((_cfg.groups || []).map(g => [g.name, { ...g, fields: [] }]));
  for (const f of _cfg.fields || []) byGroup.get(f.group)?.fields.push(f);
  const aggressiveOn = !!_draft.QUANT_AGGRESSIVE_MODE;
  el.innerHTML = [...byGroup.values()].map(g => {
    const inactive = (g.name.startsWith("공격 모드") && !aggressiveOn) || (g.name.startsWith("기본 모드") && aggressiveOn);
    return `<div class="card" style="${inactive ? "opacity:.75;" : ""}">
      <h3 class="font-semibold text-sm mb-1">${escHtml(g.name)} ${inactive ? badge(aggressiveOn ? "공격 모드 ON — 이 그룹은 지금 매매에 쓰이지 않음" : "공격 모드 OFF — 이 그룹은 지금 매매에 쓰이지 않음") : ""}</h3>
      <p class="text-xs mb-2" style="color:var(--text-mute);">${escHtml(g.help)}</p>
      <div>${g.fields.map(f => {
        const changed = JSON.stringify(_draft[f.key] ?? null) !== JSON.stringify(currentValue(f) ?? null);
        const overridden = f.source === "env" && Object.prototype.hasOwnProperty.call(_cfg.overrides || {}, f.key);
        return `<div class="ss-field ${changed ? "changed" : ""} ${overridden ? "overridden" : ""}" data-field="${f.key}">
          <div><div class="ss-label">${escHtml(f.label)}</div><div class="ss-meta" style="font-family:monospace;">${escHtml(f.key)}</div></div>
          <div>${inputFor(f)}</div>
          <div><div class="ss-desc">${escHtml(f.description)}</div><div class="ss-meta">${metaFor(f)}</div></div>
        </div>`;
      }).join("")}</div>
    </div>`;
  }).join("");
  el.querySelectorAll("[data-key]").forEach(input => {
    input.addEventListener("input", onInput); input.addEventListener("change", onInput);
  });
  renderDirty();
}

function onInput(e) {
  const key = e.target.dataset.key; const f = (_cfg.fields || []).find(x => x.key === key); if (!f) return;
  let v;
  if (f.type === "bool") { v = e.target.checked; e.target.nextElementSibling.textContent = v ? "ON" : "OFF"; }
  else if (f.type === "int") v = e.target.value === "" ? null : parseInt(e.target.value, 10);
  else if (f.type === "float") v = e.target.value === "" ? null : parseFloat(e.target.value);
  else v = e.target.value;
  _draft[key] = v;
  const row = e.target.closest(".ss-field");
  if (row) row.classList.toggle("changed", JSON.stringify(v ?? null) !== JSON.stringify(currentValue(f) ?? null));
  if (key === "QUANT_AGGRESSIVE_MODE") renderGroups();   // 그룹 활성 표시 갱신
  renderDirty();
}

function dirtyKeys() {
  return (_cfg?.fields || []).filter(f => JSON.stringify(_draft[f.key] ?? null) !== JSON.stringify(currentValue(f) ?? null)).map(f => f.label);
}
function renderDirty() {
  const d = dirtyKeys(); const el = $("ss-dirty"); if (!el) return;
  el.textContent = d.length ? `변경 ${d.length}건: ${d.slice(0, 4).join(", ")}${d.length > 4 ? " …" : ""}` : "변경 없음";
  const save = $("ss-save"); if (save) save.disabled = !d.length && !($("ss-label")?.value || "").trim();
}

function renderHeader() {
  const el = $("ss-badges"), st = $("ss-status"); if (!el || !_cfg) return;
  const eff = _cfg.effective || {}, prof = _cfg.profile;
  const n = Object.keys(_cfg.overrides || {}).length;
  el.innerHTML = [
    badge(eff.QUANT_AGGRESSIVE_MODE ? `공격 모드 ON · ${eff.QUANT_AGGRESSIVE_CANDLE_INTERVAL} · 익절 +${eff.QUANT_AGGRESSIVE_TAKE_PROFIT_PCT}% / 손절 -${eff.QUANT_AGGRESSIVE_STOP_LOSS_PCT}% · ${eff.QUANT_AGGRESSIVE_ORDER_TYPE}` : `기본 모드(일봉) · 전략 ${_cfg.row?.strategy_id || "기본 규칙"} · ${eff.STOCK_COIN_TRADE_ORDER_TYPE}`, eff.QUANT_AGGRESSIVE_MODE ? "" : "ok"),
    badge(`사이클 ${eff.QUANT_CYCLE_SEC}초 (beat ${_cfg.beat_cycle_sec}초)`),
    badge(`시세 ${eff.MARKET_DATA_SOURCE === "kis" ? "KIS 실시간" : "Yahoo(지연)"}`),
    badge(_cfg.batch_running ? "배치 실행 중" : "배치 대기/OFF", _cfg.batch_running ? "ok" : "bad"),
    _cfg.kill_switch ? badge("비상 정지 상태", "bad") : "",
    badge(n ? `env 덮어쓰기 ${n}개` : "env 덮어쓰기 없음(서버 기본값)"),
    _cfg.strategy_api_configured ? badge(`합격 전략 ${(_cfg.strategies || []).length}개`) : badge("domain-rag-lab 미연결 — 전략 스펙 선택 불가", "bad"),
  ].filter(Boolean).join(" ");
  st.textContent = prof ? `현재 프로필 ${prof.id}${prof.label ? ` (${prof.label})` : ""} · 적용 ${ts(prof.applied_at)} (KST) · 누적 프로필 ${_cfg.profile_count}건 · 마지막 사이클 ${ts(_cfg.last_run_at)}` : "아직 저장한 적이 없습니다. 모든 값이 서버 기본값(env)입니다.";
}

function buildDraft() {
  _draft = {};
  for (const f of _cfg.fields || []) _draft[f.key] = currentValue(f);
}

export async function loadStrategySettings() {
  const st = $("ss-status");
  try {
    _cfg = await api("/api/quant/strategy/settings");
    buildDraft(); renderHeader(); renderGroups();
  } catch (e) {
    if (st) st.textContent = `불러오기 실패: ${e?.message || e}`;
    setToast(e?.message || String(e), "error");
  }
}

async function save() {
  const d = dirtyKeys();
  const label = ($("ss-label")?.value || "").trim();
  const turningOff = _cfg.effective?.QUANT_AGGRESSIVE_MODE && !_draft.QUANT_AGGRESSIVE_MODE;
  const msg = `${d.length}개 설정을 저장하고 다음 사이클부터 적용합니다.${turningOff ? "\n\n⚠ 공격 모드를 끕니다. 기본 모드 익절·손절이 0 이면 스펙 exit 규칙과 일손실 한도만 남습니다." : ""}\n계속할까요?`;
  if (!confirm(msg)) return;
  const btn = $("ss-save"); btn.disabled = true;
  try {
    const values = {};
    for (const f of _cfg.fields || []) { const v = _draft[f.key]; if (v !== null && v !== undefined) values[f.key] = v; }
    const r = await api("/api/quant/strategy/settings", { method: "POST", body: { values, label } });
    setToast(`저장 완료 — ${r.created ? `프로필 ${r.profile?.id} 기록. ` : "값 변화 없음. "}${r.note || ""}`, "ok");
    $("ss-label").value = "";
    await loadStrategySettings();
  } catch (e) { setToast(e?.message || String(e), "error"); }
  finally { btn.disabled = false; renderDirty(); }
}

async function reset() {
  if (!confirm("env 덮어쓰기를 모두 지우고 서버 기본값으로 되돌립니다. 배치 행 값(전략·비율·위험관리)은 유지됩니다. 계속할까요?")) return;
  try {
    const r = await api("/api/quant/strategy/settings/reset", { method: "POST", body: {} });
    setToast(`초기화 완료 — 프로필 ${r.profile?.id || ""}`, "ok");
    await loadStrategySettings();
  } catch (e) { setToast(e?.message || String(e), "error"); }
}

// ── 성과 화면 ────────────────────────────────────────────────────
function kpi(label, value, sub = "", color = "") {
  return `<div class="rounded-lg p-3" style="background:var(--surf2);border:1px solid var(--border);">
    <div class="text-xs" style="color:var(--text-mute);">${escHtml(label)}</div>
    <div class="text-lg font-semibold" style="${color ? `color:${color};` : ""}">${value}</div>
    ${sub ? `<div class="text-xs" style="color:var(--text-dim);">${sub}</div>` : ""}</div>`;
}

function renderPerf() {
  if (!_perf) return;
  const rows = _perf.profiles || [];
  const active = rows.find(r => r.active) || rows[0];
  const totalLive = rows.reduce((a, r) => a + (r.live?.realized || 0), 0);
  const totalVirtual = rows.reduce((a, r) => a + (r.virtual?.realized || 0), 0);
  $("sp-kpis").innerHTML = [
    kpi("현재 프로필", active ? `${active.id}${active.label ? ` · ${escHtml(active.label)}` : ""}` : "-", active ? `적용 ${ts(active.start)} · ${fmt(active.duration_min)}분 경과` : ""),
    kpi("현재 프로필 KIS 실현손익", won(active?.live?.realized), `매도 ${active?.live?.sells ?? 0}건 · 승률 ${active?.live?.win_rate_pct ?? "-"}%`, pnlColor(active?.live?.realized)),
    kpi("전체 KIS 실현손익 (FILLED)", won(totalLive), `실체결 ${_perf.live_fills}건`, pnlColor(totalLive)),
    kpi("전체 가상 실현손익", won(totalVirtual), `가상 체결 ${_perf.virtual_orders}건`, pnlColor(totalVirtual)),
  ].join("");
  $("sp-note").textContent = `집계 ${ts(_perf.checked_at)} (KST) · 사이클 수는 캐시된 최근 ${_perf.cycles_in_cache}개 사이클 안에서만 셉니다 · 현재 ${_perf.aggressive_now ? "공격 모드" : "기본 모드"}`;
  const head = ["프로필", "메모 / 적용 기간", "경과", "최근 사이클", "매수/매도 (가상)", "가상 실현손익", "KIS 매수/매도", "KIS 실현손익", "KIS 승률", "KIS 매도금액", "가상 평가액 변화", "KIS 평가액 변화", "모드"];
  $("sp-table").querySelector("thead").innerHTML = `<tr>${head.map(h => `<th>${escHtml(h)}</th>`).join("")}</tr>`;
  $("sp-table").querySelector("tbody").innerHTML = rows.map(r => {
    const v = r.values || {};
    const mode = r.id === "before" ? "-" : (v.QUANT_AGGRESSIVE_MODE ? `공격 ${v.QUANT_AGGRESSIVE_CANDLE_INTERVAL || ""}` : `기본 · ${r.row?.strategy_id || "규칙"}`);
    return `<tr class="ss-perf-row ${r.active ? "active" : ""}" data-id="${escHtml(r.id)}">
      <td>${escHtml(r.id)}</td>
      <td>${escHtml(r.label || "")}<div class="text-xs" style="color:var(--text-mute);">${ts(r.start)} ~ ${r.end ? ts(r.end) : "현재"}</div></td>
      <td>${r.duration_min == null ? "-" : `${fmt(r.duration_min)}분`}</td>
      <td>${r.cycles_recent}${r.risk_skipped_recent ? ` <span style="color:var(--text-mute);">(생략 ${r.risk_skipped_recent})</span>` : ""}</td>
      <td>${r.virtual.buys} / ${r.virtual.sells}</td>
      <td style="color:${pnlColor(r.virtual.realized)}">${won(r.virtual.realized)}</td>
      <td>${r.live.buys} / ${r.live.sells}</td>
      <td style="color:${pnlColor(r.live.realized)}">${won(r.live.realized)}</td>
      <td>${r.live.win_rate_pct == null ? "-" : `${r.live.win_rate_pct}% (${r.live.wins}승 ${r.live.losses}패)`}</td>
      <td>${won(r.live.sell_amount)}</td>
      <td style="color:${pnlColor(r.equity_change_pct_virtual)}">${pct(r.equity_change_pct_virtual)}</td>
      <td style="color:${pnlColor(r.equity_change_pct_live)}">${pct(r.equity_change_pct_live)}</td>
      <td>${escHtml(mode)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="13" style="text-align:center;color:var(--text-mute);">프로필이 없습니다. 「봇 전략 · 파라미터 설정」에서 한 번 저장하면 여기부터 집계됩니다.</td></tr>`;
  $("sp-table").querySelectorAll(".ss-perf-row").forEach(tr => tr.addEventListener("click", () => showDetail(tr.dataset.id)));
}

function showDetail(id) {
  const r = (_perf?.profiles || []).find(x => x.id === id); if (!r) return;
  const fields = _perf.fields || [];
  $("sp-detail").style.display = "";
  $("sp-detail-title").textContent = `${r.id}${r.label ? ` · ${r.label}` : ""} — 설정값 (${ts(r.start)} 적용${r.by ? `, by ${r.by.slice(0, 8)}` : ""})`;
  const src = (f) => f.source === "row" ? r.row?.[f.key] : r.values?.[f.key];
  $("sp-detail-body").innerHTML = r.id === "before"
    ? `<div><span>서버 기본값(env)으로 돌던 구간입니다. 값은 「봇 전략 · 파라미터 설정」의 서버 기본값 표기를 참고하세요.</span></div>`
    : fields.map(f => `<div><span title="${escHtml(f.description)}">${escHtml(f.label)}</span><span>${escHtml(fmtVal(f, src(f)))}</span></div>`).join("")
      + `<div><span>프로필 시작 평가액</span><span>가상 ${won(r.equity_start?.virtual)} · KIS ${won(r.equity_start?.live)}</span></div>`
      + `<div><span>프로필 종료(또는 현재) 평가액</span><span>가상 ${won(r.equity_end?.virtual)} · KIS ${won(r.equity_end?.live)}</span></div>`;
  $("sp-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export async function loadStrategyPerformance() {
  try { _perf = await api("/api/quant/strategy/performance"); renderPerf(); }
  catch (e) { $("sp-note").textContent = `불러오기 실패: ${e?.message || e}`; setToast(e?.message || String(e), "error"); }
}

$("ss-reload")?.addEventListener("click", loadStrategySettings);
$("ss-save")?.addEventListener("click", save);
$("ss-reset")?.addEventListener("click", reset);
$("ss-label")?.addEventListener("input", renderDirty);
$("sp-reload")?.addEventListener("click", loadStrategyPerformance);
