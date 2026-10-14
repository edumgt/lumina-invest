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
  if (v == null || v === "") return f.type === "str" ? "(비움)" : f.key === "strategy_id" ? "(선택 안 함)" : f.key === "QUANT_SIGNAL_MODE" ? "(미저장 · 추정)" : "-";
  if (f.extra?.labels?.[v]) return f.extra.labels[v];
  if (f.type === "bool") return v ? "ON" : "OFF";
  if (f.type === "float" || f.type === "int") return `${fmt(v, f.type === "float" && !Number.isInteger(Number(v)) ? 2 : 0)}${f.unit || ""}`;
  return String(v);
}

// ── 설정 화면 ────────────────────────────────────────────────────
let _spec = null;           // GET /spec 결과 (현재 입력 중인 전략): {spec, versions, latest_version, health}
let _specFor = "";          // _spec 이 어느 strategy_id 의 것인지
const MODE_LABEL = (m) => (_cfg?.signal_modes || []).find(x => x.value === m)?.label || m || "-";
function currentMode() { return _draft.QUANT_SIGNAL_MODE || _cfg?.signal_mode || "custom"; }

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
      opts = [`<option value="">(선택 안 함)</option>`, ...list.map(st => `<option value="${escHtml(st.strategy_id)}" ${st.strategy_id === v ? "selected" : ""}>${escHtml(st.name || st.strategy_id)} v${st.version} · 종목 ${st.universe_size ?? "-"} · 거래 ${st.backtest_result?.trade_count ?? "-"}건 · MDD ${st.backtest_result?.max_drawdown_pct ?? "-"}%</option>`)];
      if (v && !list.some(st => st.strategy_id === v)) opts.push(`<option value="${escHtml(v)}" selected>${escHtml(v)} (목록에 없음)</option>`);
    } else opts = f.choices.map(c => `<option value="${escHtml(c)}" ${c === v ? "selected" : ""}>${escHtml(f.extra?.labels?.[c] || c)}</option>`);
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

// ── 전략설정 vs 하이퍼파라미터 설명 카드 ──
function renderHpHelp() {
  const el = $("ss-hp"); if (!el || !_cfg?.hp_help) return;
  const h = _cfg.hp_help;
  el.innerHTML = `<h3 class="font-semibold text-sm mb-1"><i class="fa-solid fa-ruler-combined" aria-hidden="true"></i> ${escHtml(h.title)}</h3>
    <p class="text-xs" style="color:var(--text-dim);line-height:1.55;">${escHtml(h.summary)}</p>
    <table class="ss-hp-table"><thead><tr><th>구분</th><th>전략설정 (이 화면)</th><th>하이퍼파라미터 (ML 모델 학습)</th></tr></thead>
    <tbody>${(h.rows || []).map(r => `<tr><td>${escHtml(r.aspect)}</td><td>${escHtml(r.settings)}</td><td>${escHtml(r.hyper)}</td></tr>`).join("")}</tbody></table>
    <p class="text-xs mt-2" style="color:var(--text-mute);"><b>${escHtml(h.hp_like_note)}</b> 예: <span class="ss-hp-tag">HP 유사</span> ${(_cfg.fields || []).filter(f => f.hp_like).map(f => escHtml(f.label)).join(" · ")}</p>`;
}

// ── 운영 모드 3분할 카드 ──
function renderModes() {
  const el = $("ss-modes"); if (!el || !_cfg) return;
  const mode = currentMode();
  const explicit = !!_cfg.signal_mode_explicit;
  el.innerHTML = `<h3 class="font-semibold text-sm mb-1"><i class="fa-solid fa-compass" aria-hidden="true"></i> 운영 모드 <span class="text-xs" style="color:var(--text-mute);font-weight:400;">· 세 모드는 배타적 — 고른 모드의 그룹만 매매에 쓰입니다</span></h3>
    <div class="ss-mode-cards">${(_cfg.signal_modes || []).map(m => `
      <label class="ss-mode-card ${m.value === mode ? "selected" : ""}">
        <div class="ss-mode-title"><input type="radio" name="ss-mode" value="${m.value}" ${m.value === mode ? "checked" : ""}>${escHtml(m.label)}${m.value === _cfg.signal_mode ? `<span class="ss-mode-now">지금 적용 중</span>` : ""}</div>
        <div class="ss-mode-desc">${escHtml(m.description)}</div>
      </label>`).join("")}</div>
    <p class="text-xs mt-2" style="color:var(--text-mute);">${explicit ? `저장된 운영 모드: <b>${escHtml(MODE_LABEL(_cfg.signal_mode))}</b>.`
      : `아직 운영 모드를 명시 저장한 적이 없어 이전 호환 규칙(공격 플래그 → 전략 유무)으로 <b>${escHtml(MODE_LABEL(_cfg.signal_mode))}</b>로 추정 중입니다. 한 번 저장하면 고정됩니다.`}
      ${mode === "backtest" ? " ② 는 아래 전략 스펙을 선택해야 저장됩니다." : mode === "custom" ? " ③ 은 전략 스펙을 무시합니다." : " ① 은 전략 스펙을 종목 교집합·사유 표시에만 씁니다."}</p>`;
  el.querySelectorAll('input[name="ss-mode"]').forEach(r => r.addEventListener("change", (e) => {
    _draft.QUANT_SIGNAL_MODE = e.target.value;
    renderModes(); renderGroups(); renderDirty();
  }));
}

function renderGroups() {
  const el = $("ss-groups"); if (!el || !_cfg) return;
  const byGroup = new Map((_cfg.groups || []).map(g => [g.name, { ...g, fields: [] }]));
  for (const f of _cfg.fields || []) { if (!f.hidden && f.key !== "QUANT_SIGNAL_MODE") byGroup.get(f.group)?.fields.push(f); }
  const mode = currentMode();
  el.innerHTML = [...byGroup.values()].map(g => {
    const inactive = Array.isArray(g.modes) && g.modes.length > 0 && !g.modes.includes(mode);
    const isBt = Array.isArray(g.modes) && g.modes.length === 1 && g.modes[0] === "backtest";
    return `<div class="card" style="${inactive ? "opacity:.7;" : ""}" data-group="${escHtml(g.name)}">
      <h3 class="font-semibold text-sm mb-1">${escHtml(g.name)} ${inactive ? badge(`현재 운영 모드(${MODE_LABEL(mode)})에서는 매매에 쓰이지 않음`) : (g.modes?.length ? badge("현재 모드에서 적용 중", "ok") : "")}</h3>
      <p class="text-xs mb-2" style="color:var(--text-mute);">${escHtml(g.help)}</p>
      <div>${g.fields.map(f => {
        const changed = JSON.stringify(_draft[f.key] ?? null) !== JSON.stringify(currentValue(f) ?? null);
        const overridden = f.source === "env" && Object.prototype.hasOwnProperty.call(_cfg.overrides || {}, f.key);
        return `<div class="ss-field ${changed ? "changed" : ""} ${overridden ? "overridden" : ""} ${f.hp_like ? "hp" : ""}" data-field="${f.key}">
          <div><div class="ss-label">${f.hp_like ? `<b>${escHtml(f.label)}</b><span class="ss-hp-tag" title="하이퍼파라미터처럼 결과 품질을 좌우하는 조정값 — 감으로 바꾸지 말고 백테스트·설정별 성과로 검증하세요">HP 유사</span>` : escHtml(f.label)}</div><div class="ss-meta" style="font-family:var(--font-code);">${escHtml(f.key)}</div></div>
          <div>${inputFor(f)}</div>
          <div><div class="ss-desc">${escHtml(f.description)}</div><div class="ss-meta">${metaFor(f)}</div></div>
        </div>`;
      }).join("")}</div>
      ${isBt ? `<div id="ss-spec-card"></div>` : ""}
    </div>`;
  }).join("");
  el.querySelectorAll("[data-key]").forEach(input => {
    input.addEventListener("input", onInput); input.addEventListener("change", onInput);
  });
  renderSpecCard();
  renderDirty();
}

// ── 백테스트 기반 규칙: 전략 스펙 카드 (규칙·백테스트 결과·버전·상태 점검·재검증·pr 링크) ──
function ruleText(rule) {
  if (!rule) return "-";
  const params = Object.entries(rule.params || {}).map(([k, v]) => `${k}=${v}`).join(", ");
  return `<code>${escHtml(rule.indicator || "-")}</code> ${escHtml(rule.condition || "")}${params ? ` (${escHtml(params)})` : ""}`;
}
function prBacktestUrl(spec) {
  const base = _cfg?.strategy_public_url || ""; if (!base) return "";
  const e = spec?.entry || {}, p = e.params || {}, period = spec?.backtest_result?.period || {};
  const q = new URLSearchParams({ strategy: e.indicator || "ma_cross", ticker: (spec?.universe || [])[0] || "", short: p.short_window ?? "", long: p.long_window ?? "", breakout: p.breakout_window ?? "", interval: p.interval_days ?? "", start: period.start || "", from_fd: spec?.strategy_id || "" });
  q.set('view', 'backtest');
  return `${base}/?${q.toString()}`;
}
function renderSpecCard() {
  const el = $("ss-spec-card"); if (!el || !_cfg) return;
  const sid = (_draft.strategy_id || "").trim();
  const pub = _cfg.strategy_public_url || "";
  const prLink = pub ? `<a class="btn-secondary text-xs" href="${pub}/?view=backtest" target="_blank" rel="noopener">pr LEAN 백테스트 열기 ↗</a>` : "";
  if (!_cfg.strategy_api_configured) { el.innerHTML = `<div class="ss-spec-card"><ul class="ss-health"><li class="warn">domain-rag-lab(pr) 이 연결되지 않아 전략 스펙을 받을 수 없습니다 (DOMAIN_RAG_LAB_BASE_URL).</li></ul></div>`; return; }
  if (!sid) {
    el.innerHTML = `<div class="ss-spec-card"><h4>전략 스펙 미선택</h4><ul class="ss-health"><li class="warn">② 모드에서 매매하려면 합격 전략이 필요합니다. 합격 전략 ${(_cfg.strategies || []).length}개. 새 전략은 pr 백테스트 화면에서 「이 조건을 전략 스펙으로 저장」 → 합격 → 여기서 선택.</li></ul><div class="ss-spec-actions">${prLink}</div></div>`;
    return;
  }
  if (_specFor !== sid) { el.innerHTML = `<div class="ss-spec-card"><span class="text-xs" style="color:var(--text-mute);">스펙 불러오는 중…</span></div>`; fetchSpec(sid); return; }
  const b = _spec || {}, sp = b.spec;
  if (!sp) { el.innerHTML = `<div class="ss-spec-card"><h4>${escHtml(sid)}</h4><ul class="ss-health">${(b.health || []).map(h => `<li class="${h.level}">${escHtml(h.text)}</li>`).join("") || `<li class="warn">스펙을 받지 못했습니다.</li>`}</ul><div class="ss-spec-actions">${prLink}</div></div>`; return; }
  const br = sp.backtest_result || {}, acc = sp.acceptance || {}, cr = acc.criteria || {}, ps = sp.position_sizing || {}, sw = sp.signal_weights || {};
  const appliedV = Number(_cfg.row?.strategy_version || 0), latestV = Number(b.latest_version || sp.version || 0);
  const sameAsApplied = sid === (_cfg.row?.strategy_id || "");
  const names = _cfg.stock_names || {};
  const universe = (sp.universe || []).map(c => `${escHtml(c)}${names[c] ? ` ${escHtml(names[c])}` : ""}`).join(", ");
  const vers = (b.versions?.versions || []);
  el.innerHTML = `<div class="ss-spec-card">
    <h4><i class="fa-solid fa-file-lines" aria-hidden="true"></i> ${escHtml(sp.name || sid)} <span style="font-weight:400;color:var(--text-mute);">· ${escHtml(sid)} v${sp.version}${sameAsApplied && appliedV ? ` · 적용 중 v${appliedV}` : ""}${latestV ? ` · 최신 합격 v${latestV}` : ""}</span></h4>
    <dl class="ss-spec-grid">
      <div><dt>진입 규칙 (entry)</dt><dd>${ruleText(sp.entry)}</dd></div>
      <div><dt>청산 규칙 (exit)</dt><dd>${ruleText(sp.exit)}</dd></div>
      <div><dt>universe</dt><dd>${universe || "-"}</dd></div>
      <div><dt>백테스트 기간 · 엔진</dt><dd>${escHtml(br.period?.start || "-")} ~ ${escHtml(br.period?.end || "-")} · ${escHtml(br.engine || "-")}</dd></div>
      <div><dt>연환산 수익 / MDD / 샤프</dt><dd style="color:${pnlColor(br.annualized_return_pct)}">${pct(br.annualized_return_pct)}</dd><dd>MDD ${pct(br.max_drawdown_pct)} · 샤프 ${br.sharpe_ratio ?? "-"}</dd></div>
      <div><dt>거래 수 / 승률</dt><dd>${br.trade_count ?? "-"}건 · ${br.win_rate_pct == null ? "-" : br.win_rate_pct + "%"}</dd></div>
      <div><dt>합격 기준</dt><dd>${acc.passed ? badge("합격", "ok") : badge("불합격", "bad")} MDD ≤ ${cr.max_drawdown_pct_lte ?? "-"}% · 거래 ≥ ${cr.trade_count_gte ?? "-"}건 · 연수익 > ${cr.annualized_return_pct_gt ?? "-"}%</dd></div>
      <div><dt>position_sizing / 가중</dt><dd>최대 ${ps.max_symbols ?? "-"}종목 · 비중 ${ps.max_position_pct ?? "-"}% · 매수≥${sw.buy_threshold ?? "-"} / 매도≤${sw.sell_threshold ?? "-"}${Number(sw.lightgbm || 0) > 0 ? ` · ML 가중 ${sw.lightgbm}` : ""}</dd></div>
      <div><dt>생성</dt><dd>${escHtml(String(sp.created_at || "").replace("T", " ").slice(0, 16))}</dd></div>
    </dl>
    ${(b.health || []).length ? `<ul class="ss-health">${b.health.map(h => `<li class="${h.level}">${escHtml(h.text)}</li>`).join("")}</ul>` : ""}
    <div class="ss-spec-actions">
      <button class="btn-secondary text-xs" id="ss-spec-refresh">스펙 새로고침</button>
      <button class="btn-secondary text-xs" id="ss-spec-revalidate" title="pr 에서 이 스펙의 규칙으로 오늘까지 백테스트를 다시 돌려 새 버전을 만듭니다(수십 초~수 분)">최근 기간으로 재검증</button>
      ${sameAsApplied && appliedV && latestV > appliedV ? `<button class="btn-primary text-xs" id="ss-spec-latest">최신 버전 v${latestV} 적용</button>` : ""}
      ${pub ? `<a class="btn-secondary text-xs" href="${prBacktestUrl(sp)}" target="_blank" rel="noopener">pr 에서 이 전략 백테스트 열기 ↗</a>` : ""}
    </div>
    ${vers.length ? `<details><summary class="text-xs" style="cursor:pointer;color:var(--text-mute);">버전 이력 ${vers.length}건 (불합격 포함)</summary>
      <table class="ss-versions"><thead><tr><th>버전</th><th>판정 · 미달 사유</th><th>연수익</th><th>MDD</th><th>거래</th><th>승률</th><th>기간 종료</th><th>생성</th></tr></thead><tbody>
      ${vers.map(v => `<tr><td>v${v.version}${v.version === appliedV && sameAsApplied ? " <i class=\"fa-solid fa-star\" aria-hidden=\"true\"></i>" : ""}</td><td>${v.passed ? badge("합격", "ok") : badge("불합격", "bad")} ${escHtml((v.failed || []).join("; "))}</td><td>${pct(v.backtest_result?.annualized_return_pct)}</td><td>${pct(v.backtest_result?.max_drawdown_pct)}</td><td>${v.backtest_result?.trade_count ?? "-"}</td><td>${v.backtest_result?.win_rate_pct ?? "-"}</td><td>${escHtml(v.backtest_result?.period?.end || "-")}</td><td>${escHtml(String(v.created_at || "").slice(0, 10))}</td></tr>`).join("")}
      </tbody></table></details>` : ""}
  </div>`;
  $("ss-spec-refresh")?.addEventListener("click", () => { _specFor = ""; renderSpecCard(); });
  $("ss-spec-revalidate")?.addEventListener("click", () => revalidate(sid));
  $("ss-spec-latest")?.addEventListener("click", () => save({ force: true, label: `전략 ${sid} 최신 버전 v${latestV} 적용` }));
}

async function fetchSpec(sid) {
  try {
    const r = await api(`/api/quant/strategy/spec?strategy_id=${encodeURIComponent(sid)}`);
    _spec = r; _specFor = sid;
  } catch (e) {
    _spec = { spec: null, health: [{ level: "warn", text: e?.message || String(e) }] }; _specFor = sid;
  }
  if ((_draft.strategy_id || "").trim() === sid) renderSpecCard();
}

async function revalidate(sid) {
  const today = new Date().toISOString().slice(0, 10);
  if (!confirm(`pr 에서 「${sid}」 의 규칙으로 오늘(${today})까지 백테스트를 다시 돌립니다. LEAN 실행에 수십 초~수 분 걸립니다.\n합격하면 새 버전이 저장되고, 불합격이면 이전 합격 버전이 유지됩니다. 계속할까요?`)) return;
  const btn = $("ss-spec-revalidate"); if (btn) { btn.disabled = true; btn.textContent = "재검증 실행 중…"; }
  try {
    const r = await api("/api/quant/strategy/revalidate", { method: "POST", body: { strategy_id: sid, end_date: today } });
    setToast(r.note || "재검증 합격", "ok");
    await loadStrategySettings();
  } catch (e) { setToast(`재검증: ${e?.message || e}`, "error"); _specFor = ""; renderSpecCard(); }
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
  if (key === "strategy_id") renderSpecCard();   // 스펙 카드 갱신(필요 시 /spec 조회)
  renderDirty();
}

function dirtyKeys() {
  return (_cfg?.fields || []).filter(f => !f.hidden && JSON.stringify(_draft[f.key] ?? null) !== JSON.stringify(currentValue(f) ?? null)).map(f => f.label);
}
function renderDirty() {
  const d = dirtyKeys(); const el = $("ss-dirty"); if (!el) return;
  el.textContent = d.length ? `변경 ${d.length}건: ${d.slice(0, 4).join(", ")}${d.length > 4 ? " …" : ""}` : "변경 없음";
  const save = $("ss-save"); if (save) save.disabled = !d.length && !($("ss-label")?.value || "").trim();
}

function renderHeader() {
  const el = $("ss-badges"), st = $("ss-status"); if (!el || !_cfg) return;
  const eff = _cfg.effective || {}, prof = _cfg.profile, mode = _cfg.signal_mode;
  const n = Object.keys(_cfg.overrides || {}).length;
  const modeDetail = mode === "aggressive" ? `${eff.QUANT_AGGRESSIVE_CANDLE_INTERVAL} · 익절 +${eff.QUANT_AGGRESSIVE_TAKE_PROFIT_PCT}% / 손절 -${eff.QUANT_AGGRESSIVE_STOP_LOSS_PCT}% · ${eff.QUANT_AGGRESSIVE_ORDER_TYPE}`
    : mode === "backtest" ? `전략 ${_cfg.row?.strategy_id || "(미선택!)"}${_cfg.row?.strategy_version ? ` v${_cfg.row.strategy_version}` : ""} · ${eff.STOCK_COIN_TRADE_ORDER_TYPE}`
    : `매수≥${eff.QUANT_CUSTOM_BUY_SCORE} / 매도≤${eff.QUANT_CUSTOM_SELL_SCORE}${eff.QUANT_CUSTOM_TREND_FILTER ? " · 추세 필터" : ""} · ${eff.STOCK_COIN_TRADE_ORDER_TYPE}`;
  el.innerHTML = [
    badge(`운영 모드 ${MODE_LABEL(mode)} · ${modeDetail}`, mode === "aggressive" ? "" : "ok"),
    _cfg.signal_mode_explicit ? "" : badge("운영 모드 미저장(추정)", "bad"),
    badge(`사이클 ${eff.QUANT_CYCLE_SEC}초 (beat ${_cfg.beat_cycle_sec}초)`),
    badge(`시세 ${eff.MARKET_DATA_SOURCE === "kis" ? "KIS 실시간" : "Yahoo(지연)"}`),
    badge(_cfg.batch_running ? "배치 실행 중" : "배치 대기/OFF", _cfg.batch_running ? "ok" : "bad"),
    _cfg.kill_switch ? badge("비상 정지 상태", "bad") : "",
    badge(n ? `env 덮어쓰기 ${n}개` : "env 덮어쓰기 없음(서버 기본값)"),
    _cfg.strategy_api_configured ? badge(`pr 합격 전략 ${(_cfg.strategies || []).length}개`) : badge("domain-rag-lab 미연결 — 전략 스펙 선택 불가", "bad"),
  ].filter(Boolean).join(" ");
  st.textContent = prof ? `현재 프로필 ${prof.id}${prof.label ? ` (${prof.label})` : ""} · 적용 ${ts(prof.applied_at)} (KST) · 누적 프로필 ${_cfg.profile_count}건 · 마지막 사이클 ${ts(_cfg.last_run_at)}` : "아직 저장한 적이 없습니다. 모든 값이 서버 기본값(env)입니다.";
}

function buildDraft() {
  _draft = {};
  for (const f of _cfg.fields || []) _draft[f.key] = currentValue(f);
  // 운영 모드는 추정값이라도 드래프트에 채워 첫 저장에서 명시 고정되게 한다
  if (!_draft.QUANT_SIGNAL_MODE) _draft.QUANT_SIGNAL_MODE = _cfg.signal_mode || "custom";
  if (_cfg.row?.strategy_id && _cfg.spec) { _spec = { spec: _cfg.spec, versions: _cfg.spec_versions, latest_version: _cfg.spec_latest_version, health: _cfg.spec_health }; _specFor = _cfg.row.strategy_id; }
  else { _spec = null; _specFor = ""; }
}

export async function loadStrategySettings() {
  const st = $("ss-status");
  try {
    _cfg = await api("/api/quant/strategy/settings");
    buildDraft(); renderHeader(); renderHpHelp(); renderModes(); renderGroups();
  } catch (e) {
    if (st) st.textContent = `불러오기 실패: ${e?.message || e}`;
    setToast(e?.message || String(e), "error");
  }
}

async function save(opts = {}) {
  const d = dirtyKeys();
  const label = opts.label || ($("ss-label")?.value || "").trim();
  const mode = currentMode();
  if (mode === "backtest" && !(_draft.strategy_id || "").trim()) { setToast("② 백테스트 기반 규칙은 전략 스펙을 선택해야 저장할 수 있습니다.", "error"); return; }
  const modeChange = mode !== _cfg.signal_mode || !_cfg.signal_mode_explicit;
  let warn = "";
  if (modeChange && mode !== "aggressive" && !(Number(_draft.QUANT_BASE_TAKE_PROFIT_PCT) > 0 || Number(_draft.QUANT_BASE_STOP_LOSS_PCT) > 0)) warn += "\n\n주의: 일봉 모드 익절·손절이 0 이라 " + (mode === "backtest" ? "스펙 exit 규칙" : "매도 임계 점수") + "과 일손실 한도만으로 청산합니다.";
  if (mode === "custom" && (_draft.strategy_id || "").trim()) warn += "\n\nℹ ③ 사용자 설정에서는 선택된 전략 스펙이 무시됩니다.";
  const msg = opts.force ? `${label}\n다음 사이클부터 적용합니다. 계속할까요?`
    : `${d.length}개 설정을 저장하고 다음 사이클부터 적용합니다.${modeChange ? `\n운영 모드: ${MODE_LABEL(_cfg.signal_mode)} → ${MODE_LABEL(mode)}` : ""}${warn}\n계속할까요?`;
  if (!confirm(msg)) return;
  const btn = $("ss-save"); btn.disabled = true;
  try {
    const values = {};
    for (const f of _cfg.fields || []) { const v = _draft[f.key]; if (v !== null && v !== undefined) values[f.key] = v; }
    values.QUANT_SIGNAL_MODE = mode;
    const r = await api("/api/quant/strategy/settings", { method: "POST", body: { values, label } });
    setToast(`저장 완료 — ${r.created ? `프로필 ${r.profile?.id} 기록. ` : "값 변화 없음. "}${r.note || ""}`, "ok");
    $("ss-label").value = "";
    await loadStrategySettings();
  } catch (e) { setToast(e?.message || String(e), "error"); }
  finally { btn.disabled = false; renderDirty(); }
}

async function reset() {
  if (!confirm("env 덮어쓰기를 모두 지우고 서버 기본값으로 되돌립니다(운영 모드 포함 — 이전 호환 규칙으로 추정). 배치 행 값(전략·비율·위험관리)은 유지됩니다. 계속할까요?")) return;
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
  $("sp-note").textContent = `집계 ${ts(_perf.checked_at)} (KST) · 사이클 수는 캐시된 최근 ${_perf.cycles_in_cache}개 사이클 안에서만 셉니다 · 현재 ${_perf.signal_mode ? MODE_LABEL(_perf.signal_mode) : (_perf.aggressive_now ? "공격 모드" : "일봉 모드")}`;
  const head = ["프로필", "메모 / 적용 기간", "경과", "최근 사이클", "매수/매도 (가상)", "가상 실현손익", "KIS 매수/매도", "KIS 실현손익", "KIS 승률", "KIS 매도금액", "가상 평가액 변화", "KIS 평가액 변화", "모드"];
  $("sp-table").querySelector("thead").innerHTML = `<tr>${head.map(h => `<th>${escHtml(h)}</th>`).join("")}</tr>`;
  $("sp-table").querySelector("tbody").innerHTML = rows.map(r => {
    const v = r.values || {};
    const mode = r.id === "before" ? "-" : v.QUANT_SIGNAL_MODE ? `${MODE_LABEL(v.QUANT_SIGNAL_MODE)}${v.QUANT_SIGNAL_MODE === "backtest" ? ` · ${r.row?.strategy_id || "?"}` : v.QUANT_SIGNAL_MODE === "aggressive" ? ` ${v.QUANT_AGGRESSIVE_CANDLE_INTERVAL || ""}` : ""}` : (v.QUANT_AGGRESSIVE_MODE ? `공격 ${v.QUANT_AGGRESSIVE_CANDLE_INTERVAL || ""}` : `일봉 · ${r.row?.strategy_id || "규칙"}`);
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
$("ss-save")?.addEventListener("click", () => save());
$("ss-reset")?.addEventListener("click", reset);
$("ss-label")?.addEventListener("input", renderDirty);
$("sp-reload")?.addEventListener("click", loadStrategyPerformance);
