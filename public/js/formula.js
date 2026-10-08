/* 자유 산식 커스텀 지표 화면 (indicator-formula)
 * DSL 편집기 · 함수 레퍼런스 · 검증/계산 · 저장(버전) · 결과 이력 · Pine/Python 내보내기 */
import { renderFormulaChartHelp } from "/js/formula-chart-help.js";
import { api, setToast, escHtml, fmt } from "/js/common.js";

const $ = (id) => document.getElementById(id);
const ts = (iso) => iso ? new Date(iso).toLocaleString("ko-KR", { hour12: false }) : "-";
let chart = null, current = null, reference = null;

function readDefinition() {
  let params = {};
  const raw = $("fx-params").value.trim();
  if (raw) { try { params = JSON.parse(raw); } catch { throw new Error('params 는 JSON 객체여야 합니다. 예) {"n": 20}'); } }
  return { indicator_expr: $("fx-indicator").value.trim(), buy_expr: $("fx-buy").value.trim(), sell_expr: $("fx-sell").value.trim(), params };
}
function readCosts() {
  const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
  return { symbol: $("fx-symbol").value.trim() || "005930.KS", period: $("fx-period").value,
    commission_bps: Number($("fx-cost").value || 0), slippage_bps: Number($("fx-slip").value || 0), stop_loss_pct: num("fx-sl"), take_profit_pct: num("fx-tp") };
}
function fillDefinition(d) {
  $("fx-name").value = d.name || ""; $("fx-desc").value = d.description || "";
  $("fx-indicator").value = d.indicator_expr || ""; $("fx-buy").value = d.buy_expr || ""; $("fx-sell").value = d.sell_expr || "";
  $("fx-params").value = Object.keys(d.params || {}).length ? JSON.stringify(d.params) : "";
}

async function loadReference() {
  if (reference) return;
  reference = await api("/api/formula-indicators/reference");
  $("fx-functions").innerHTML = reference.functions.map(f => `<div class="rounded p-2 text-xs cursor-pointer fx-fn" data-ex="${escHtml(f.example)}" style="background:var(--surf2);border:1px solid var(--border);" title="클릭하면 예시를 indicator 산식 끝에 추가">
    <code style="color:var(--accent);">${escHtml(f.signature)}</code><div style="color:var(--text-dim);">${escHtml(f.description)}</div><div style="color:var(--text-mute);">예) ${escHtml(f.example)}</div></div>`).join("");
  $("fx-functions").querySelectorAll(".fx-fn").forEach(el => el.addEventListener("click", () => { const t = $("fx-indicator"); t.value = (t.value.trim() ? t.value.trim() + " " : "") + el.dataset.ex; t.focus(); }));
  $("fx-templates").innerHTML = reference.templates.map((t, i) => `<button class="btn-secondary text-xs fx-tpl" data-i="${i}" title="${escHtml(t.description)}">${escHtml(t.name)}</button>`).join(" ");
  $("fx-templates").querySelectorAll(".fx-tpl").forEach(b => b.addEventListener("click", () => { current = null; fillDefinition(reference.templates[+b.dataset.i]); $("fx-save").textContent = "새 지표로 저장"; setToast("템플릿을 불러왔습니다. 수정 후 저장하세요.", "ok"); }));
  $("fx-rules").innerHTML = reference.rules.map(r => `<li>${escHtml(r)}</li>`).join("");
}

async function validate() {
  try {
    const body = { ...readDefinition(), symbol: $("fx-symbol").value.trim() || null };
    const r = await api("/api/formula-indicators/validate", { method: "POST", body });
    const tr = r.trial ? ` · 시험 계산(${escHtml(body.symbol)} 1y): 최신값 ${r.trial.latest_value ?? "-"}, 신호 ${r.trial.latest_signal}, buy ${r.trial.signal_counts.buy}회 / sell ${r.trial.signal_counts.sell}회` : (r.trial_error ? ` · <span class="text-red-500">시험 계산 실패: ${escHtml(String(r.trial_error))}</span>` : "");
    $("fx-validate-result").innerHTML = `<span class="badge-buy">문법 OK</span> 함수 ${r.functions.map(escHtml).join(", ") || "-"} · 변수 ${r.variables.map(escHtml).join(", ") || "-"} · 체크섬 <code>${r.checksum}</code>${tr}${r.warnings.length ? `<div class="text-amber-500 mt-1">⚠ ${r.warnings.map(escHtml).join(" / ")}</div>` : ""}`;
  } catch (e) { $("fx-validate-result").innerHTML = `<span class="badge-sell">오류</span> <span class="text-red-500">${escHtml(e.message)}</span>`; }
}

function renderResult(r, meta = "") {
  const p = r.payload || r; const s = p.series; const bt = p.backtest || {};
  const stats = p.indicator_stats || {};
  $("fx-result").classList.remove("hidden");
  $("fx-result-meta").innerHTML = `${meta}${r.from_cache ? ' <span class="badge-hold">저장된 결과 재사용</span>' : ""} · 기준일 ${escHtml(r.as_of || p.as_of)} · ${r.rows || p.rows}봉 · 최신값 <b>${(r.latest_value ?? p.latest_value) == null ? "-" : Number(r.latest_value ?? p.latest_value).toFixed(4)}</b> · 최신 신호 <span class="${/BUY/.test(r.latest_signal || p.latest_signal) ? "badge-buy" : /SELL/.test(r.latest_signal || p.latest_signal) ? "badge-sell" : "badge-hold"}">${escHtml(r.latest_signal || p.latest_signal)}</span>`;
  const cards = bt.total_return_pct != null ? [
    ["누적 수익률", `${bt.total_return_pct >= 0 ? "+" : ""}${bt.total_return_pct}%`, bt.total_return_pct >= 0 ? "var(--green)" : "var(--red)"],
    ["매수후보유", `${bt.buy_hold_return_pct}%`, "var(--text-mute)"], ["샤프", `${bt.sharpe_ratio}`, "var(--text)"], ["MDD", `${bt.mdd_pct}%`, "var(--red)"],
    ["승률", `${bt.win_rate_pct}%`, "var(--text)"], ["거래", `${bt.trade_count}회`, "var(--text)"], ["비용 차감", `-${bt.cost_pct ?? 0}%p`, "var(--text-mute)"],
    ["손절/익절", `${bt.stop_loss_exits ?? 0}/${bt.take_profit_exits ?? 0}회`, "var(--text-mute)"],
  ] : [["지표 평균(1y)", stats.mean?.toFixed(4) ?? "-", "var(--text)"], ["표준편차", stats.std?.toFixed(4) ?? "-", "var(--text)"], ["최소", stats.min?.toFixed(4) ?? "-", "var(--text)"], ["최대", stats.max?.toFixed(4) ?? "-", "var(--text)"]];
  $("fx-cards").innerHTML = cards.map(([l, v, c]) => `<div class="card" style="padding:12px;text-align:center;"><div class="text-xs" style="color:var(--text-mute);">${l}</div><div style="font-size:17px;font-weight:700;color:${c};">${v}</div></div>`).join("");
  renderFormulaChartHelp(r);
  if (window.ApexCharts && s) {
    const x = s.times.map(t => new Date(t).getTime());
    const buys = new Set(s.buy_dates), sells = new Set(s.sell_dates);
    const opts = { chart: { height: 380, toolbar: { show: false }, background: "transparent", animations: { enabled: false } },
      theme: { mode: document.documentElement.dataset.theme === "light" ? "light" : "dark" },
      series: [
        { name: "종가", type: "line", data: s.close.map((v, i) => [x[i], v]) },
        { name: "지표", type: "line", data: s.indicator.map((v, i) => [x[i], v]) },
        ...(s.position ? [{ name: "포지션(1=보유)", type: "area", data: s.position.map((v, i) => [x[i], v]) }] : []),
      ],
      yaxis: [{ seriesName: "종가", title: { text: "종가" }, labels: { formatter: v => fmt(v) } }, { seriesName: "지표", opposite: true, title: { text: "지표" }, labels: { formatter: v => v == null ? "" : Number(v).toFixed(2) } },
        ...(s.position ? [{ seriesName: "포지션(1=보유)", show: false, min: 0, max: 1 }] : [])],
      xaxis: { type: "datetime" }, stroke: { width: [2, 2, 0], curve: "straight" }, fill: { opacity: [1, 1, 0.12] }, dataLabels: { enabled: false },
      colors: ["#2962ff", "#f59e0b", "#089981"], legend: { position: "top" },
      annotations: { xaxis: [...s.buy_dates.slice(-40).map(d => ({ x: new Date(d).getTime(), borderColor: "#089981", label: { text: "B", style: { background: "#089981", color: "#fff" } } })),
                             ...s.sell_dates.slice(-40).map(d => ({ x: new Date(d).getTime(), borderColor: "#f23645", label: { text: "S", style: { background: "#f23645", color: "#fff" } } }))] },
      tooltip: { shared: true, x: { format: "yyyy-MM-dd" } } };
    if (chart) chart.destroy();
    chart = new ApexCharts($("fx-chart"), opts); chart.render();
  }
}

async function computeNow() {
  if (chart) { chart.destroy(); chart = null; }
  $("fx-chart").innerHTML = ""; $("fx-cards").innerHTML = "";
  if ($("fx-chart-help")) $("fx-chart-help").hidden = true;
  $("fx-result-meta").innerHTML = `<span style="color:var(--text-mute);">계산 중…</span>`; $("fx-result").classList.remove("hidden");
  try {
    const costs = readCosts();
    let r;
    if (current) {
      r = await api(`/api/formula-indicators/${current.id}/compute`, { method: "POST", body: { ...costs, use_cache: $("fx-use-cache").checked } });
      renderResult(r, `<b>${escHtml(current.name)}</b> v${r.version} · ${escHtml(costs.symbol)} ${costs.period}`);
      loadResults();
    } else {
      r = await api("/api/formula-indicators/compute", { method: "POST", body: { ...readDefinition(), ...costs } });
      renderResult(r, `<b>저장 안 된 산식</b> · ${escHtml(costs.symbol)} ${costs.period}`);
    }
  } catch (e) { $("fx-result-meta").innerHTML = `<span class="text-red-500">${escHtml(e.message)}</span>`; }
}

async function save() {
  try {
    const body = { ...readDefinition(), name: $("fx-name").value.trim(), description: $("fx-desc").value.trim(), note: $("fx-note").value.trim() };
    if (!body.name) return setToast("지표 이름을 입력하세요.", "error");
    let r;
    if (current) { r = await api(`/api/formula-indicators/${current.id}`, { method: "PUT", body }); setToast(r.version_bumped ? `산식이 바뀌어 v${r.current_version} 으로 저장했습니다.` : "이름/설명만 갱신했습니다(산식 동일).", "ok"); }
    else { r = await api("/api/formula-indicators", { method: "POST", body }); setToast(`'${r.name}' v1 저장`, "ok"); }
    current = r; $("fx-note").value = "";
    await loadList(); await loadVersions(); await loadResults(); $("fx-save").textContent = "저장 (변경 시 새 버전)";
  } catch (e) { setToast(e.message, "error"); }
}

async function loadList() {
  const r = await api("/api/formula-indicators");
  $("fx-list").innerHTML = r.indicators.length ? r.indicators.map(i => `<div class="flex items-center justify-between gap-2 p-2 rounded ${current && current.id === i.id ? "" : ""}" style="background:var(--surf2);border:1px solid ${current && current.id === i.id ? "var(--accent)" : "var(--border)"};">
      <div class="min-w-0"><div class="text-sm font-semibold truncate">${escHtml(i.name)} <span class="badge-hold" style="font-size:10px;">v${i.current_version}</span></div><div class="text-xs truncate" style="color:var(--text-mute);"><code>${escHtml(i.indicator_expr)}</code></div></div>
      <div class="flex gap-1 flex-none"><button class="btn-secondary text-xs fx-open" data-id="${i.id}">열기</button><button class="btn-secondary text-xs fx-del" data-id="${i.id}">삭제</button></div></div>`).join("")
    : `<div class="text-sm" style="color:var(--text-mute);">저장된 산식 지표가 없습니다. 템플릿으로 시작해 보세요.</div>`;
  $("fx-list").querySelectorAll(".fx-open").forEach(b => b.addEventListener("click", () => openIndicator(b.dataset.id)));
  $("fx-list").querySelectorAll(".fx-del").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("이 지표와 모든 버전·계산 결과를 삭제합니다. 계속할까요?")) return;
    try { await api(`/api/formula-indicators/${b.dataset.id}`, { method: "DELETE" }); if (current && current.id === b.dataset.id) newIndicator(); await loadList(); setToast("삭제했습니다.", "ok"); } catch (e) { setToast(e.message, "error"); }
  }));
}

async function openIndicator(id) {
  current = await api(`/api/formula-indicators/${id}`); fillDefinition(current);
  $("fx-save").textContent = "저장 (변경 시 새 버전)"; $("fx-current").textContent = `${current.name} · v${current.current_version} · ${current.checksum}`;
  await loadList(); await loadVersions(); await loadResults();
}
function newIndicator() {
  current = null; fillDefinition({}); $("fx-current").textContent = "새 지표 (미저장)"; $("fx-save").textContent = "새 지표로 저장";
  $("fx-versions").innerHTML = ""; $("fx-results").innerHTML = ""; $("fx-export").innerHTML = "";
}

async function loadVersions() {
  if (!current) return;
  const r = await api(`/api/formula-indicators/${current.id}/versions`);
  $("fx-versions").innerHTML = `<table><thead><tr><th>버전</th><th>indicator</th><th>buy / sell</th><th>params</th><th>메모</th><th>시각</th><th></th></tr></thead><tbody>${
    r.versions.map(v => `<tr${v.version === r.current_version ? ' style="font-weight:600"' : ""}><td>v${v.version}${v.version === r.current_version ? " ✔" : ""}</td><td class="text-xs"><code>${escHtml(v.indicator_expr)}</code></td>
      <td class="text-xs"><code>${escHtml(v.buy_expr || "-")}</code><br><code>${escHtml(v.sell_expr || "-")}</code></td><td class="text-xs">${escHtml(JSON.stringify(v.params))}</td><td class="text-xs">${escHtml(v.note)}</td><td class="text-xs">${ts(v.created_at)}</td>
      <td>${v.version !== r.current_version ? `<button class="btn-secondary text-xs fx-restore" data-v="${v.version}">복원</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
  $("fx-versions").querySelectorAll(".fx-restore").forEach(b => b.addEventListener("click", async () => {
    try { const res = await api(`/api/formula-indicators/${current.id}/versions/${b.dataset.v}/restore`, { method: "POST" }); setToast(res.restored ? `v${b.dataset.v} 산식을 v${res.current_version} 으로 복원` : res.message, "ok"); await openIndicator(current.id); } catch (e) { setToast(e.message, "error"); }
  }));
  $("fx-export").innerHTML = `<button id="fx-exp-pine" class="btn-secondary text-xs">Pine Script 내보내기</button> <button id="fx-exp-py" class="btn-secondary text-xs">Python 내보내기</button><pre id="fx-export-code" class="text-xs rounded-lg p-3 mt-2 overflow-x-auto hidden" style="background:var(--surf2);border:1px solid var(--border);"></pre>`;
  const show = async (f) => { const res = await fetch(`/api/formula-indicators/${current.id}/export?format=${f}`, { credentials: "include" }); const code = await res.text(); const pre = $("fx-export-code"); pre.textContent = code; pre.classList.remove("hidden"); navigator.clipboard?.writeText(code).catch(() => {}); setToast(`${f} 코드를 표시하고 클립보드에 복사했습니다.`, "ok"); };
  $("fx-exp-pine").addEventListener("click", () => show("pine")); $("fx-exp-py").addEventListener("click", () => show("python"));
}

async function loadResults() {
  if (!current) return;
  const r = await api(`/api/formula-indicators/${current.id}/results?limit=30`);
  $("fx-results").innerHTML = r.results.length ? `<table><thead><tr><th>시각</th><th>버전</th><th>종목</th><th>기간</th><th>기준일</th><th style="text-align:right">최신값</th><th>신호</th><th style="text-align:right">수익률</th><th style="text-align:right">샤프</th><th style="text-align:right">MDD</th><th></th></tr></thead><tbody>${
    r.results.map(x => `<tr><td class="text-xs">${ts(x.created_at)}</td><td>v${x.version}</td><td class="font-mono text-xs">${escHtml(x.symbol)}</td><td>${x.period}</td><td class="text-xs">${x.as_of}</td><td style="text-align:right">${x.latest_value == null ? "-" : Number(x.latest_value).toFixed(3)}</td>
      <td><span class="${/BUY/.test(x.latest_signal) ? "badge-buy" : /SELL/.test(x.latest_signal) ? "badge-sell" : "badge-hold"}" style="font-size:11px;">${escHtml(x.latest_signal)}</span></td>
      <td style="text-align:right">${x.metrics.total_return_pct ?? "-"}${x.metrics.total_return_pct != null ? "%" : ""}</td><td style="text-align:right">${x.metrics.sharpe_ratio ?? "-"}</td><td style="text-align:right">${x.metrics.mdd_pct ?? "-"}${x.metrics.mdd_pct != null ? "%" : ""}</td>
      <td><button class="btn-secondary text-xs fx-view" data-id="${x.id}">보기</button></td></tr>`).join("")}</tbody></table>`
    : `<div class="text-sm" style="color:var(--text-mute);">아직 저장된 계산 결과가 없습니다. '계산 실행'을 누르면 (버전·종목·기간) 별로 저장됩니다.</div>`;
  $("fx-results").querySelectorAll(".fx-view").forEach(b => b.addEventListener("click", async () => {
    // 저장된 결과는 목록 API 에 payload 가 없으므로 같은 조건으로 캐시 재사용 호출
    const row = r.results.find(x => x.id === b.dataset.id);
    $("fx-symbol").value = row.symbol; $("fx-period").value = row.period;
    $("fx-cost").value = row.costs.commission_bps ?? 0; $("fx-slip").value = row.costs.slippage_bps ?? 0; $("fx-sl").value = row.costs.stop_loss_pct ?? ""; $("fx-tp").value = row.costs.take_profit_pct ?? "";
    $("fx-use-cache").checked = true; await computeNow();
  }));
}

export function initFormulaView() {
  const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
  on("fx-validate", "click", validate); on("fx-compute", "click", computeNow); on("fx-save", "click", save); on("fx-new", "click", newIndicator);
  on("fx-toggle-ref", "click", () => $("fx-ref").classList.toggle("hidden"));
}
export function onFormulaViewActivated(view) {
  if (view !== "indicator-formula") return;
  loadReference().then(loadList).catch(e => setToast(e.message, "error"));
}
