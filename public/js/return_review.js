/* 수익율검증 (GNB 수익율검증) — data-view="return-review"
 * 데이터: GET /api/quant/return-review?environment=, POST /rows(붙여넣기 적재), POST /snapshot(게이트웨이 잔고), DELETE /rows/{date}
 * 일별 평가액(DB) × 매매 이력 × 전략설정 프로필(운영 모드) → 진단·권고 가이드. 권고 버튼은 전략설정의 해당 항목으로 이동해 강조한다. */
import { api, escHtml, fmt, setToast } from "/js/common.js";
import { navigate } from "/js/core.js";

const $ = (id) => document.getElementById(id);
let _data = null;

const won = (v) => v == null ? "-" : `${v > 0 ? "+" : ""}${fmt(Math.round(v))}원`;
const pct = (v, d = 2) => v == null ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const pp = (v) => v == null ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%p`;
const cls = (v) => v == null ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "";
const color = (v) => v == null ? "" : v < 0 ? "var(--down,#2962ff)" : v > 0 ? "var(--up,#e5484d)" : "";
const MODE_COLOR = { aggressive: "rgba(229,72,77,.10)", backtest: "rgba(23,201,100,.12)", custom: "rgba(41,98,255,.10)", "": "transparent" };

function env() { return $("rr-env")?.value || "paper"; }

function kpi(k, v, s = "", c = "") {
  return `<div class="rr-kpi"><div class="k">${escHtml(k)}</div><div class="v" style="${c ? `color:${c};` : ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
}

// 장중 실시간 평가액 타일 — 서버가 GET 마다 게이트웨이 잔고를 읽어 live 에 담는다(DB 기록 아님). 실패면 사유만 보인다.
function liveTile() {
  const l = _data.live;
  if (!l) return "";
  const at = l.as_of ? new Date(l.as_of).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" }) : "";
  const phase = l.market === "open" ? "장중" : "장외";
  if (!l.ok) return kpi("지금 평가액 (실시간)", "-", `${escHtml(l.error || "조회 실패")} · ${phase} ${at}`);
  const vs = l.last_snap_date ? `${l.last_snap_date} 스냅샷 대비 ${won(l.change_pnl)} (${pp(l.change_pct)})` : "비교할 스냅샷 없음";
  const rec = l.today_recorded ? "오늘 행 기록됨" : "오늘 행 미기록(15:40 자동)";
  return kpi("지금 평가액 (실시간)", `${fmt(Math.round(l.equity))}원`, `${vs} · 누적 ${pct(l.cum_return_pct)} · ${phase} ${at} · ${rec}`, color(l.change_pct));
}

function renderKpis() {
  const s = _data.summary || {}, t = _data.trade_stats || {};
  $("rr-kpis").innerHTML = liveTile() + (s.days ? [
    kpi("누적 수익률", pct(s.cum_return_pct), `${won(s.cum_pnl)} · 원금 ${fmt(s.principal)}원`, color(s.cum_return_pct)),
    kpi("최대 낙폭 (MDD)", pct(s.max_drawdown_pct), `최고 평가액 ${fmt(s.peak_equity)}원`, color(s.max_drawdown_pct)),
    kpi("최악 관측일", s.worst_day ? pp(s.worst_day.change_pct) : "-", s.worst_day ? `${s.worst_day.date} · ${won(s.worst_day.pnl)}` : "", color(s.worst_day?.change_pct)),
    kpi("상승 / 하락일", `${s.up_days} / ${s.down_days}`, `${s.days}관측일 · 평균 ${pp(s.avg_daily_change_pct)} · 변동성 ${Number(s.daily_volatility_pct).toFixed(2)}%p`),
    kpi("승률 · 손익비 (KIS 왕복)", t.round_trips ? `${t.win_rate_pct}% · ${t.payoff_ratio ?? "-"}` : "-", t.round_trips ? `왕복 ${t.round_trips}건 · 평균 이익 ${won(t.avg_win)} / 손실 ${won(t.avg_loss)}` : "완결 왕복 없음"),
    kpi("거래당 기대값", t.round_trips ? won(t.expectancy) : "-", t.orders ? `체결 ${t.fills}/주문 ${t.orders}건 · 체결률 ${t.fill_rate_pct ?? "-"}%` : "실주문 기록 없음", color(t.expectancy)),
  ].join("") : `<div class="text-xs" style="color:var(--text-mute);">일별 평가액 데이터가 없습니다. 아래 「붙여넣기 적재」 또는 「오늘 스냅샷 기록」 으로 시작하세요.</div>`);
  const modeLabel = _data.signal_mode ? ({ aggressive: "① 공격 모드", backtest: "② 백테스트 기반 규칙", custom: "③ 사용자 설정" })[_data.signal_mode] : (_data.aggressive ? "① 공격 모드(플래그)" : "일봉 모드(추정)");
  $("rr-note").textContent = `집계 ${new Date(_data.checked_at).toLocaleString("ko-KR")} · 환경 ${_data.environment} · 현재 운영 모드 ${modeLabel} · 전략설정 프로필 ${_data.profiles}건 · KIS 실주문 ${_data.live_orders}건(체결 ${_data.live_fills}) · 가상 체결 ${_data.virtual_orders}건 · 일손실 한도 ${_data.limits?.daily_loss_limit_pct ?? "-"}%`;
}

function drawChart() {
  const canvas = $("rr-chart"); if (!canvas) return;
  const rows = _data.rows || [];
  const rect = canvas.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(260 * dpr);
  const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = rect.width, H = 260, padL = 64, padR = 52, padT = 16, padB = 34;
  ctx.clearRect(0, 0, W, H);
  if (rows.length < 1) { ctx.fillStyle = "#888"; ctx.font = "12px sans-serif"; ctx.fillText("데이터 없음", W / 2 - 30, H / 2); return; }
  const n = rows.length, x = (i) => padL + (W - padL - padR) * (n === 1 ? 0.5 : i / (n - 1));
  const eqs = rows.map(r => r.equity), minE = Math.min(...eqs) * 0.995, maxE = Math.max(...eqs) * 1.003;
  const yE = (v) => padT + (H - padT - padB) * (1 - (v - minE) / Math.max(1, maxE - minE));
  const chg = rows.map(r => r.daily_change_pct ?? 0), maxC = Math.max(1, ...chg.map(Math.abs));
  const yC = (v) => padT + (H - padT - padB) * (0.5 - v / (2 * maxC));
  // 모드 음영
  for (let i = 0; i < n; i++) {
    const m = rows[i].mode?.mode || ""; if (!MODE_COLOR[m] || m === "") continue;
    const x0 = i === 0 ? padL : (x(i - 1) + x(i)) / 2, x1 = i === n - 1 ? W - padR : (x(i) + x(i + 1)) / 2;
    ctx.fillStyle = MODE_COLOR[m]; ctx.fillRect(x0, padT, x1 - x0, H - padT - padB);
  }
  // 축·격자
  ctx.strokeStyle = "rgba(128,128,128,.25)"; ctx.lineWidth = 1; ctx.font = "11px sans-serif"; ctx.fillStyle = "#888";
  for (let k = 0; k <= 4; k++) { const v = minE + (maxE - minE) * k / 4, y = yE(v); ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke(); ctx.textAlign = "right"; ctx.fillText(fmt(Math.round(v / 10000)) + "만", padL - 6, y + 4); }
  ctx.textAlign = "left"; for (const v of [maxC, 0, -maxC]) { ctx.fillText(`${v > 0 ? "+" : ""}${v.toFixed(1)}%p`, W - padR + 6, yC(v) + 4); }
  // 0 선
  ctx.strokeStyle = "rgba(128,128,128,.5)"; ctx.beginPath(); ctx.moveTo(padL, yC(0)); ctx.lineTo(W - padR, yC(0)); ctx.stroke();
  // 일변화 막대
  const bw = Math.max(4, Math.min(28, (W - padL - padR) / n * 0.5));
  rows.forEach((r, i) => { const v = r.daily_change_pct; if (v == null) return; ctx.fillStyle = v < 0 ? "rgba(41,98,255,.65)" : "rgba(229,72,77,.65)"; const y0 = yC(0), y1 = yC(v); ctx.fillRect(x(i) - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0)); });
  // 평가액 선
  ctx.strokeStyle = "#f5a524"; ctx.lineWidth = 2.5; ctx.beginPath(); rows.forEach((r, i) => i ? ctx.lineTo(x(i), yE(r.equity)) : ctx.moveTo(x(i), yE(r.equity))); ctx.stroke();
  ctx.fillStyle = "#f5a524"; rows.forEach((r, i) => { ctx.beginPath(); ctx.arc(x(i), yE(r.equity), 3, 0, Math.PI * 2); ctx.fill(); });
  // x 라벨
  ctx.fillStyle = "#888"; ctx.textAlign = "center"; const step = Math.ceil(n / Math.max(1, Math.floor((W - padL - padR) / 70)));
  rows.forEach((r, i) => { if (i % step === 0 || i === n - 1) ctx.fillText(r.snap_date.slice(5), x(i), H - padB + 16); });
  $("rr-legend").innerHTML = `<span style="color:#f5a524;">■</span> 평가액(만원) &nbsp; <span style="color:#e5484d;">■</span> 상승 / <span style="color:#2962ff;">■</span> 하락 (%p) &nbsp; 음영: <span style="background:${MODE_COLOR.aggressive};padding:0 6px;">① 공격</span> <span style="background:${MODE_COLOR.backtest};padding:0 6px;">② 백테스트 규칙</span> <span style="background:${MODE_COLOR.custom};padding:0 6px;">③ 사용자 설정</span> · 음영 없음 = 전략설정 프로필 이전`;
}

function goField(field, view) {
  if (view && view.startsWith("external:")) { const u = _data?.strategy_public_url; if (u) window.open(`${u}/?view=backtest`, "_blank", "noopener"); return; }
  navigate(view || "strategy-settings");
  if (!field) return;
  let tries = 0;
  const tick = () => {
    const el = document.querySelector(`.ss-field[data-field="${CSS.escape(field)}"]`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.remove("rr-flash"); void el.offsetWidth; el.classList.add("rr-flash"); return; }
    if (++tries < 30) setTimeout(tick, 150);
  };
  setTimeout(tick, 200);
}

const PLAN_LV = { observing: "관찰 중", improved: "개선", not_improved: "미개선" };
function fmtTo(v) { return v === true ? "ON" : v === false ? "OFF" : v === "" || v == null ? "(비움)" : String(v); }

function renderPlans() {
  const P = _data.plans || {}; const el = $("rr-plans"); if (!el) return;
  $("rr-plans-reason").innerHTML = P.recommended ? `권장: <b>방안 ${escHtml(P.recommended)}</b> — ${escHtml(P.reason || "")}` : "";
  $("rr-algo").innerHTML = (P.algorithm || []).map(a => `<li>${escHtml(a)}</li>`).join("");
  el.innerHTML = (P.plans || []).map(p => `<div class="rr-plan ${p.recommended ? "rec" : ""} ${p.applicable ? "" : "na"}">
      <h4>${p.recommended ? `<span class="rr-rec">권장</span>` : ""}${escHtml(p.title)}</h4>
      <div class="rr-stage">단계: ${escHtml(p.stage)} · 전제: ${escHtml(p.prereq || "-")}</div>
      <p>${escHtml(p.summary)}</p>
      ${p.changes?.length ? `<div class="rr-sec">전략설정 변경 ${p.changes.length}건 (현재 → 제안)</div><table><tbody>${p.changes.map(c => `<tr><td title="${escHtml(c.key)}">${escHtml(c.label)}</td><td style="color:var(--text-mute);">${escHtml(fmtTo(c.from))}</td><td class="to">→ ${escHtml(fmtTo(c.to))}</td><td style="color:var(--text-mute);">${escHtml(c.why || "")}</td></tr>`).join("")}</tbody></table>` : ""}
      ${p.expected?.length ? `<div class="rr-sec">기대 효과</div><ul>${p.expected.map(e => `<li>${escHtml(e)}</li>`).join("")}</ul>` : ""}
      ${p.risks?.length ? `<div class="rr-sec">리스크</div><ul>${p.risks.map(e => `<li>${escHtml(e)}</li>`).join("")}</ul>` : ""}
      ${p.blocker ? `<div class="rr-sec">적용 불가</div><div class="rr-blocker">${escHtml(p.blocker)}</div>` : ""}
      <div class="rr-approve">${p.applicable && p.changes?.length
        ? `<button class="btn-primary text-xs" data-plan="${p.id}"><i class="fa-solid fa-check" aria-hidden="true"></i> 승인 · 전략설정에 적용 (${p.changes.length}건)</button>`
        : p.applicable ? `<span class="text-xs" style="color:var(--text-mute);">현재 설정과 같아 바꿀 값이 없습니다.</span>`
        : `<button class="btn-secondary text-xs" data-plan-ext="1">↗ pr 에서 합격 전략 만들기</button>`}</div>
    </div>`).join("") || `<span class="text-xs" style="color:var(--text-mute);">데이터가 쌓이면 방안이 생성됩니다.</span>`;
  el.querySelectorAll("button[data-plan]").forEach(b => b.addEventListener("click", () => approvePlan(b.dataset.plan)));
  el.querySelectorAll("button[data-plan-ext]").forEach(b => b.addEventListener("click", () => goField(null, "external:pr")));
}

async function approvePlan(id) {
  const p = (_data.plans?.plans || []).find(x => x.id === id); if (!p) return;
  const lines = p.changes.map(c => `  · ${c.label}: ${fmtTo(c.from)} → ${fmtTo(c.to)}`).join("\n");
  const note = prompt(`방안 ${id} 를 승인합니다. 아래 ${p.changes.length}건이 전략설정에 저장되어 다음 사이클부터 전 시스템(배치·자동매매 사이클)에 적용됩니다.\n\n${lines}\n\n프로필 메모(선택):`, "");
  if (note === null) return;
  try {
    const r = await api(`/api/quant/return-review/plans/${id}/apply`, { method: "POST", body: { note, environment: env() } });
    setToast(r.note || `방안 ${id} 적용 완료`, "ok");
    await loadReturnReview();
  } catch (e) { setToast(`승인 실패: ${e?.message || e}`, "error"); }
}

function renderApplied() {
  const el = $("rr-applied"); const aps = [...(_data.applied_plans || [])].reverse();
  el.innerHTML = aps.map(a => {
    const pr = a.progress || {}, b = a.baseline || {};
    return `<div class="rr-ap ${a.verdict || "observing"}">
      <b class="t">${escHtml(a.title || a.plan_id)}</b> <span style="color:var(--text-mute);">· 승인 ${new Date(a.applied_at).toLocaleString("ko-KR")} · 프로필 ${escHtml(a.profile_id || "-")}${a.effective_mode ? ` · 모드 ${escHtml(a.effective_mode)}` : ""}${a.strategy_id ? ` · 전략 ${escHtml(a.strategy_id)}` : ""}${a.note ? ` · ${escHtml(a.note)}` : ""}</span><br/>
      <b>판정: ${escHtml(a.verdict_text || PLAN_LV[a.verdict] || "-")}</b>${a.next_plan ? ` → <button class="btn-secondary text-xs" data-next="${a.next_plan}">방안 ${a.next_plan} 승인</button>` : ""}<br/>
      기준(승인 시점): 누적 ${pct(b.cum_return_pct)} · 평균 일변화 ${pp(b.avg_daily_change_pct)} · 일 체결 ${b.fills_per_day ?? "-"}건 · 기대값 ${b.expectancy == null ? "-" : won(b.expectancy)}<br/>
      적용 후: ${a.observed_days}관측일 · 평균 일변화 ${pp(a.avg_daily_change_after_pct)} · 누적 변화 ${pct(a.cum_change_after_pct)} · 일 체결 ${a.fills_per_day_after ?? "-"}건<br/>
      진행(최근 사이클 캐시): 사이클 ${pr.cycles ?? 0}회 · 가상 체결 ${pr.virtual_fills ?? 0}건 · 위험관리 생략 ${pr.risk_skipped ?? 0}건 · 비상 정지 ${pr.halted_cycles ?? 0}회${pr.last_cycle ? ` · 마지막 ${escHtml(pr.last_cycle)}` : ""}${pr.last_note ? ` · ${escHtml(pr.last_note)}` : ""}<br/>
      <details><summary class="text-xs" style="cursor:pointer;color:var(--text-mute);">적용한 변경 ${a.changes?.length ?? 0}건</summary><ul style="margin:4px 0 0 16px;">${(a.changes || []).map(c => `<li>${escHtml(c.label)}: ${escHtml(fmtTo(c.from))} → <b>${escHtml(fmtTo(c.to))}</b></li>`).join("")}</ul></details>
    </div>`;
  }).join("") || `<span class="text-xs" style="color:var(--text-mute);">승인된 방안이 없습니다. 위 방안 카드에서 「승인」 을 누르면 여기에 진행과 평가가 쌓입니다.</span>`;
  el.querySelectorAll("button[data-next]").forEach(b => b.addEventListener("click", () => approvePlan(b.dataset.next)));
  const log = _data.progress_log || [];
  const EV = { approve: "승인", apply: "적용", evaluate: "평가", error: "오류" };
  $("rr-log").querySelector("thead").innerHTML = `<tr><th class="l">시각</th><th class="l">이벤트</th><th class="l">방안</th><th class="l">내용</th></tr>`;
  $("rr-log").querySelector("tbody").innerHTML = log.map(e => {
    const detail = e.event === "approve" ? `${(e.changes || []).length}건 변경 승인${e.note ? ` · ${e.note}` : ""} · 기준 누적 ${pct(e.baseline?.cum_return_pct)}`
      : e.event === "apply" ? `프로필 ${e.profile_id || "-"} · 모드 ${e.effective_mode || "-"}${e.strategy_id ? ` · 전략 ${e.strategy_id}` : ""} · ${(e.applied_keys || []).join(", ")}`
      : e.event === "evaluate" ? `${e.verdict_text || e.verdict} · ${e.observed_days}관측일 · 평균 일변화 ${pp(e.avg_daily_change_after_pct)}${e.next_plan ? ` · 다음 ${e.next_plan}` : ""}`
      : e.reason || "";
    return `<tr><td class="l">${new Date(e.time).toLocaleString("ko-KR")}</td><td class="l">${EV[e.event] || e.event}${e.by && e.by !== "system" ? ` <span style="color:var(--text-mute);">${escHtml(String(e.by).slice(0, 8))}</span>` : ""}</td><td class="l">${escHtml(e.plan_id || "-")}</td><td class="l" style="white-space:normal;">${escHtml(detail)}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="l" style="color:var(--text-mute);">진행 로그가 없습니다.</td></tr>`;
}

function renderGuide() {
  const el = $("rr-guide"); const g = _data.guide || [];
  const LV = { bad: "조치 필요", warn: "주의", ok: "양호", info: "참고" };
  el.innerHTML = g.map((f, i) => `<div class="rr-card ${f.level}">
      <h4><span class="rr-lv">${LV[f.level] || f.level}</span>${escHtml(f.title)}</h4>
      ${f.evidence?.length ? `<div class="rr-sec">근거</div><ul>${f.evidence.map(e => `<li>${escHtml(e)}</li>`).join("")}</ul>` : ""}
      ${f.actions?.length ? `<div class="rr-sec">권고 · 전략설정에서 바꾸기</div><div class="rr-actions">${f.actions.map((a, j) => `<button class="btn-secondary" data-g="${i}" data-a="${j}" title="${escHtml(a.field ? `전략설정 ${a.field} 로 이동` : a.view || "")}">${a.field ? "<i class=\"fa-solid fa-gears\" aria-hidden=\"true\"></i> " : a.view === "external:pr" ? "↗ " : "→ "}${escHtml(a.text)}</button>`).join("")}</div>` : ""}
      ${f.expected ? `<div class="rr-sec">기대 효과</div><ul><li>${escHtml(f.expected)}</li></ul>` : ""}
      ${f.verify ? `<div class="rr-sec">검증</div><ul><li>${escHtml(f.verify)}</li></ul>` : ""}
    </div>`).join("") || `<div class="text-xs" style="color:var(--text-mute);">가이드가 없습니다.</div>`;
  el.querySelectorAll("button[data-g]").forEach(b => b.addEventListener("click", () => { const a = g[+b.dataset.g]?.actions?.[+b.dataset.a]; if (a) goField(a.field, a.view); }));
}

function renderTable() {
  const rows = [...(_data.rows || [])].reverse();
  const head = ["날짜", "평가액", "누적 수익률", "누적 손익", "일변화", "일손익", "낙폭", "운영 모드 · 프로필", "체결 (매수/매도)", "실현손익", "주문/미체결", "가상 (매수/매도)", "건수", "출처", ""];
  $("rr-table").querySelector("thead").innerHTML = `<tr>${head.map((h, i) => `<th class="${i === 7 || i === 13 ? "l" : ""}">${escHtml(h)}</th>`).join("")}</tr>`;
  $("rr-table").querySelector("tbody").innerHTML = rows.map(r => {
    const t = r.trading || {}, m = r.mode || {};
    return `<tr>
      <td>${escHtml(r.snap_date)}${r.gap_days > 1 ? ` <span style="color:var(--text-mute);" title="앞 관측일과 ${r.gap_days}일 차이">(+${r.gap_days}d)</span>` : ""}</td>
      <td>${fmt(Math.round(r.equity))}</td>
      <td class="${cls(r.cum_return_pct)}">${pct(r.cum_return_pct)}</td>
      <td class="${cls(r.cum_pnl)}">${won(r.cum_pnl)}</td>
      <td class="${cls(r.daily_change_pct)}"><b>${pp(r.daily_change_pct)}</b></td>
      <td class="${cls(r.daily_pnl)}">${won(r.daily_pnl)}</td>
      <td class="${cls(r.drawdown_pct)}">${pct(r.drawdown_pct)}</td>
      <td class="l">${escHtml(m.mode_label || "-")}${m.profile && m.profile !== "before" ? ` <span style="color:var(--text-mute);">· ${escHtml(m.profile)}${m.profile_label ? ` ${escHtml(m.profile_label)}` : ""}</span>` : ""}${m.strategy_id ? ` <span style="color:var(--text-mute);">· ${escHtml(m.strategy_id)}</span>` : ""}</td>
      <td>${t.fills ? `${t.fills} (${t.buys}/${t.sells})` : "-"}</td>
      <td class="${cls(t.realized)}">${t.sells ? won(t.realized) : "-"}</td>
      <td>${t.orders ? `${t.orders} / ${t.open_or_cancelled}` : "-"}</td>
      <td>${t.virtual_buys || t.virtual_sells ? `${t.virtual_buys}/${t.virtual_sells}` : "-"}</td>
      <td>${r.raw_count == null ? "-" : fmt(r.raw_count)}</td>
      <td class="l" title="${escHtml(r.note || "")}">${escHtml(r.source || "")}</td>
      <td><span class="rr-del" data-date="${escHtml(r.snap_date)}" title="이 날짜 행 삭제"><i class="fa-solid fa-xmark" aria-hidden="true"></i></span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="15" style="text-align:center;color:var(--text-mute);">행이 없습니다.</td></tr>`;
  $("rr-table").querySelectorAll(".rr-del").forEach(x => x.addEventListener("click", async () => {
    if (!confirm(`${x.dataset.date} 행을 삭제할까요?`)) return;
    try { await api(`/api/quant/return-review/rows/${x.dataset.date}?environment=${env()}`, { method: "DELETE" }); setToast("삭제 완료", "ok"); await loadReturnReview(); }
    catch (e) { setToast(e?.message || String(e), "error"); }
  }));
}

export async function loadReturnReview() {
  try {
    _data = await api(`/api/quant/return-review?environment=${env()}`);
    renderKpis(); drawChart(); renderPlans(); renderApplied(); renderGuide(); renderTable();
  } catch (e) { $("rr-note").textContent = `불러오기 실패: ${e?.message || e}`; setToast(e?.message || String(e), "error"); }
}

async function importRows() {
  const text = ($("rr-paste")?.value || "").trim();
  if (!text) { setToast("붙여넣을 표가 없습니다.", "error"); return; }
  const btn = $("rr-import"); btn.disabled = true;
  try {
    const r = await api("/api/quant/return-review/rows", { method: "POST", body: { text, environment: env(), note: ($("rr-paste-note")?.value || "").trim() } });
    setToast(`적재 완료 — 신규 ${r.inserted}건 · 갱신 ${r.updated}건 (${r.dates[0]} ~ ${r.dates[r.dates.length - 1]})`, "ok");
    $("rr-paste").value = ""; await loadReturnReview();
  } catch (e) { setToast(e?.message || String(e), "error"); }
  finally { btn.disabled = false; }
}

async function snapshot() {
  if (!confirm(`게이트웨이(KIS ${env()}) 잔고의 총평가액으로 오늘 행을 기록합니다. 원금은 최근 행의 원금을 씁니다. 계속할까요?`)) return;
  const btn = $("rr-snapshot"); btn.disabled = true;
  try {
    const r = await api("/api/quant/return-review/snapshot", { method: "POST", body: { environment: env() } });
    setToast(`기록 완료 — ${r.row.snap_date} 평가액 ${fmt(Math.round(r.row.equity))}원 (${pct(r.row.cum_return_pct)})`, "ok");
    await loadReturnReview();
  } catch (e) { setToast(e?.message || String(e), "error"); }
  finally { btn.disabled = false; }
}

$("rr-reload")?.addEventListener("click", loadReturnReview);
$("rr-import")?.addEventListener("click", importRows);
$("rr-snapshot")?.addEventListener("click", snapshot);
$("rr-env")?.addEventListener("change", loadReturnReview);
window.addEventListener("resize", () => { if (_data && document.querySelector('.view[data-view="return-review"]')?.offsetParent) drawChart(); });
