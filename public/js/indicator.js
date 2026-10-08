/* 투자 인디케이터: 기본 전략, 커스텀 인디케이터(Pine/Python 생성·저장), 성과 검증, 증권사 API 자동화
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { checkPineV6 } from "/js/pine-lint.js";
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";
import { compareTrayAdd, renderCompareTrayAll, tt } from "/js/core.js";
import { renderXaiBlock } from "/js/robo.js";

// ── 데이터 대기 모달 (모래시계 + 경과 시간) ────────────────────────
// 조회가 몇 초 걸리는지 사용자가 알 수 있어야 "멈춘 화면"으로 오해하지 않는다.
let _loadingTimer = null;
function showLoadingModal(title, desc = "") {
  const modal = document.getElementById("app-loading-modal");
  if (!modal) return;
  document.getElementById("app-loading-title").textContent = title;
  document.getElementById("app-loading-desc").textContent = desc;
  const elapsedEl = document.getElementById("app-loading-elapsed");
  const started = performance.now();
  elapsedEl.textContent = "0.0초";
  modal.classList.add("open");
  clearInterval(_loadingTimer);
  _loadingTimer = setInterval(() => {
    elapsedEl.textContent = `${((performance.now() - started) / 1000).toFixed(1)}초`;
  }, 100);
}
function hideLoadingModal() {
  clearInterval(_loadingTimer);
  _loadingTimer = null;
  document.getElementById("app-loading-modal")?.classList.remove("open");
}

// ── KIS 모의투자 주문 모달 ─────────────────────────────────────────
// 전략 분석 결과(종목·현재가·신호)를 그대로 받아 몇 주 거래할지 입력받고,
// 자동매매와 같은 경로(/api/stocks/quant/manual-order)로 KIS 모의계좌에 주문을 보낸다.
let _tradeCtx = null;   // { symbol, name, price, signal }
let _tradeSide = "buy";

function tradeModalEl(id) { return document.getElementById(id); }

function renderTradeAmount() {
  const qty = Math.max(0, parseInt(tradeModalEl("kis-trade-qty").value, 10) || 0);
  const amount = (_tradeCtx?.price || 0) * qty;
  tradeModalEl("kis-trade-amount").innerHTML = qty
    ? `예상 ${_tradeSide === "buy" ? "매수" : "매도"} 금액 <strong>${fmt(Math.round(amount))}원</strong> <span class="text-xs" style="color:var(--text-mute);">(${fmt(Math.round(_tradeCtx?.price || 0))}원 × ${qty}주)</span>`
    : `<span class="text-xs" style="color:var(--red);">수량을 1주 이상 입력하세요</span>`;
}

function setTradeSide(side) {
  _tradeSide = side;
  document.querySelectorAll(".kis-side-btn").forEach(b => b.classList.toggle("active", b.dataset.side === side));
  renderTradeAmount();
}

async function openTradeModal() {
  if (!_tradeCtx) { setToast("전략 분석을 먼저 실행하세요", "error"); return; }
  const modal = tradeModalEl("kis-trade-modal");
  const note = tradeModalEl("kis-trade-note");
  const submit = tradeModalEl("kis-trade-submit");
  tradeModalEl("kis-trade-stock").innerHTML = `
    <div class="font-semibold">${escHtml(_tradeCtx.name)} <span class="text-xs" style="color:var(--text-mute);">${escHtml(_tradeCtx.symbol)}</span></div>
    <div class="text-xs mt-1" style="color:var(--text-mute);">현재가 ${fmt(Math.round(_tradeCtx.price))}원 · 전략 신호 ${escHtml(_tradeCtx.signal || "HOLD")}</div>`;
  tradeModalEl("kis-trade-qty").value = 1;
  setTradeSide(_tradeCtx.signal === "SELL" ? "sell" : "buy");
  submit.disabled = true;
  note.innerHTML = "주문 가능 여부를 확인하는 중…";
  modal.classList.add("open");

  try {
    const r = await api("/api/stocks/quant/order-readiness");
    const lines = [`경로: ${escHtml(r.route_detail || r.route || "미연동")}`];
    if (!r.can_order) {
      lines.push(`<span style="color:var(--red);">주문 불가 — ${escHtml({
        not_connected: "KIS 연동이 되어 있지 않습니다.",
        real_environment: "KIS 경로가 실전(real)이라 화면 주문을 막습니다.",
        kill_switch: "비상 정지 상태입니다. 자동매매 현황에서 해제하세요.",
      }[r.reason] || r.reason)}</span>`);
    } else if (r.enforce_market_hours && !r.market_open) {
      lines.push(`<span style="color:var(--red);">지금은 장 운영시간이 아닙니다 — 주문이 전송되지 않고 건너뜁니다.</span>`);
    } else {
      lines.push(`KIS 모의(Testbed) 계좌로 실제 모의주문이 전송됩니다. 실전계좌가 아닙니다.`);
    }
    note.innerHTML = lines.join("<br>");
    submit.disabled = !r.can_order;
  } catch (e) {
    note.innerHTML = `<span style="color:var(--red);">주문 가능 여부 확인 실패: ${escHtml(e.message)}</span>`;
    submit.disabled = true;
  }
}

function closeTradeModal() { tradeModalEl("kis-trade-modal")?.classList.remove("open"); }
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && tradeModalEl("kis-trade-modal")?.classList.contains("open")) closeTradeModal();
});

document.getElementById("ind-trade-btn")?.addEventListener("click", openTradeModal);
document.getElementById("kis-trade-close")?.addEventListener("click", closeTradeModal);
document.getElementById("kis-trade-cancel")?.addEventListener("click", closeTradeModal);
document.getElementById("kis-trade-modal")?.addEventListener("click", e => { if (e.target.id === "kis-trade-modal") closeTradeModal(); });
document.getElementById("kis-trade-qty")?.addEventListener("input", renderTradeAmount);
document.querySelectorAll(".kis-qty-btn").forEach(b => b.addEventListener("click", () => {
  tradeModalEl("kis-trade-qty").value = b.dataset.qty;
  renderTradeAmount();
}));
document.querySelectorAll(".kis-side-btn").forEach(b => b.addEventListener("click", () => setTradeSide(b.dataset.side)));

document.getElementById("kis-trade-submit")?.addEventListener("click", async () => {
  const qty = parseInt(tradeModalEl("kis-trade-qty").value, 10);
  if (!Number.isInteger(qty) || qty < 1) { setToast("수량을 1주 이상 입력하세요", "error"); return; }
  const sideLabel = _tradeSide === "buy" ? "매수" : "매도";
  if (!confirm(`${_tradeCtx.name} ${qty}주를 KIS 모의투자로 ${sideLabel} 주문합니다.\n예상 금액 ${fmt(Math.round(_tradeCtx.price * qty))}원\n\n계속하시겠습니까?`)) return;
  const submit = tradeModalEl("kis-trade-submit");
  submit.disabled = true;
  showLoadingModal("주문 전송 중…", `${_tradeCtx.name} ${qty}주 ${sideLabel}`);
  try {
    const r = await api("/api/stocks/quant/manual-order", {
      method: "POST",
      body: { symbol: _tradeCtx.symbol, name: _tradeCtx.name, side: _tradeSide, quantity: qty },
    });
    if (r.status === "submitted") {
      setToast(`${sideLabel} 주문 전송됨 — ${r.quantity}주 · ${fmt(r.amount)}원${r.order_no ? ` (주문번호 ${r.order_no})` : ""}`, "ok");
      closeTradeModal();
    } else if (r.status === "skipped") {
      setToast(`주문이 전송되지 않았습니다: ${r.reason === "market_closed" ? "장 운영시간이 아닙니다" : r.reason}`, "error");
    } else {
      setToast(`주문 실패: ${r.error || r.status}`, "error");
    }
  } catch (e) {
    setToast(e.message, "error");
  } finally {
    hideLoadingModal();
    submit.disabled = false;
  }
});

// ── 투자 인디케이터: 기본 인디케이터 전략 ──────────────────────────
document.getElementById("ind-load-btn").addEventListener("click", async () => {
  const symbol = document.getElementById("ind-symbol").value;
  const period = document.getElementById("ind-period").value;
  const symbolLabel = document.getElementById("ind-symbol").selectedOptions[0]?.textContent?.trim() || symbol;
  const useMA5  = document.getElementById("ind-ma5").checked;
  const useMA20 = document.getElementById("ind-ma20").checked;
  const useRsi  = document.getElementById("ind-rsi").checked;
  const loadBtn = document.getElementById("ind-load-btn");
  const tradeBtn = document.getElementById("ind-trade-btn");

  // 결과가 뜨기 전까지는 대기 모달로 경과 시간을 보여 주고, 거래 버튼은 잠가 둔다.
  loadBtn.disabled = true;
  if (tradeBtn) { tradeBtn.disabled = true; tradeBtn.title = "전략 분석을 먼저 실행하세요"; }
  showLoadingModal("전략 분석 중…", `${symbolLabel} · ${period} 지표 계산`);
  try {
    const ind = await api(`/api/stocks/quant/indicators?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`);
    if (ind.error) throw new Error(ind.error);
    const last = (values) => values?.[values.length - 1];
    const rsi = ind.current_rsi ?? 50;
    const price = ind.current_price ?? 0;
    const chgPct = ind.closes?.length > 1 ? ((price / ind.closes[ind.closes.length - 2]) - 1) * 100 : 0;
    const ma5  = last(ind.ma5);
    const ma20 = last(ind.ma20);
    const ma60 = last(ind.ma60);
    const golden = ma5 > ma20;
    const sig = { signal: ind.signal?.action?.includes("매수") ? "BUY" : ind.signal?.action?.includes("매도") ? "SELL" : "HOLD" };

    document.getElementById("ind-summary-cards").innerHTML = [
      { label:"현재가",   value:`${fmt(price)}원`, sub:`${chgPct>=0?"+":""}${chgPct.toFixed(2)}%`, color: chgPct>=0?"var(--green)":"var(--red)" },
      { label:"RSI(14)",  value:rsi.toFixed(1),    sub: rsi>70?"과매수":rsi<30?"과매도":"중립", color: rsi>70?"var(--red)":rsi<30?"var(--green)":"var(--text-dim)" },
      { label:"MA 교차",  value: golden?"골든크로스":"데드크로스", sub:`MA5 ${golden?">":" <"} MA20`, color: golden?"var(--green)":"var(--red)" },
      { label:"종합 신호", value: sig.signal || "HOLD", sub:"AI 판단", color: sig.signal==="BUY"?"var(--green)":sig.signal==="SELL"?"var(--red)":"var(--text-mute)" },
    ].map(c => `<div class="card" style="padding:14px;"><div class="text-xs" style="color:var(--text-mute);">${c.label}</div><div style="font-size:20px;font-weight:700;margin:4px 0;color:${c.color};">${c.value}</div><div style="font-size:12px;color:${c.color};">${c.sub}</div></div>`).join("");

    const rows = [];
    if (useMA5 && useMA20) rows.push(["MA5 vs MA20", `${fmt(Math.round(ma5))} vs ${fmt(Math.round(ma20))}`, golden?"골든크로스 ✅":"데드크로스 ❌", golden?"매수":"매도"]);
    if (useRsi)  rows.push(["RSI(14)", rsi.toFixed(1), rsi<30?"과매도(매수기회)":rsi>70?"과매수(매도주의)":"중립", rsi<30?"매수":rsi>70?"매도":"홀드"]);
    rows.push(["볼린저밴드", "±2σ", chgPct>2?"상단 돌파":chgPct<-2?"하단 이탈":"밴드 내", chgPct>2?"매도":chgPct<-2?"매수":"홀드"]);

    document.getElementById("ind-analysis-table").innerHTML = `<table>
      <thead><tr><th>인디케이터</th><th>현재값</th><th>상태</th><th>신호</th></tr></thead>
      <tbody>${rows.map(([ind,val,st,sig]) => `<tr><td style="font-weight:600;">${ind}</td><td>${val}</td><td>${st}</td><td><span class="${sig==="매수"?"badge-buy":sig==="매도"?"badge-sell":"badge-hold"}">${sig}</span></td></tr>`).join("")}</tbody>
    </table>`;

    const buyCount = rows.filter(r=>r[3]==="매수").length;
    const sellCount = rows.filter(r=>r[3]==="매도").length;
    document.getElementById("ind-signal-summary").innerHTML = `
      <div class="flex gap-3">
        <span class="badge-buy">매수 신호: ${buyCount}개</span>
        <span class="badge-sell">매도 신호: ${sellCount}개</span>
        <span class="badge-hold">중립: ${rows.length-buyCount-sellCount}개</span>
      </div>
      <p class="text-xs mt-2" style="color:var(--text-dim);">종합: ${buyCount > sellCount ? "📈 매수 우위 — 진입 고려" : sellCount > buyCount ? "📉 매도 우위 — 익절 고려" : "⏸ 중립 — 관망 권장"}</p>`;

    document.getElementById("ind-strategy-result").classList.remove("hidden");

    // 분석이 끝나야 거래할 수 있다 — 결과(종목·현재가·신호)를 주문 모달이 그대로 쓴다.
    _tradeCtx = { symbol, name: symbolLabel, price, signal: sig.signal || "HOLD" };
    if (tradeBtn) {
      tradeBtn.disabled = !price;
      tradeBtn.title = price ? `${symbolLabel} KIS 모의투자 주문` : "현재가를 가져오지 못해 주문할 수 없습니다";
    }
  } catch(e) {
    _tradeCtx = null;
    setToast(e.message, "error");
  } finally {
    hideLoadingModal();
    loadBtn.disabled = false;
  }
});

// ── 투자 인디케이터: 커스텀 인디케이터 개발 ──────────────────────────
function generatePineCode() {
  const name   = document.getElementById("ci-name").value || "MyIndicator";
  const base   = document.getElementById("ci-base").value || "rsi_ma";
  const intValue = (id, fallback, min, max = 10000) => {
    const raw = document.getElementById(id).value;
    const value = raw === "" ? fallback : Number(raw);
    return Number.isFinite(value) ? Math.min(max, Math.max(min, Math.trunc(value))) : fallback;
  };
  const short = intValue("ci-short", 5, 1);
  const mid = intValue("ci-mid", 20, 1);
  const rsiLen = intValue("ci-rsi", 14, 1);
  const buyTh = intValue("ci-buy-th", 35, 0, 100);
  const signalCode = base === "macd_bb"
    ? `macd_line = ta.ema(close, 12) - ta.ema(close, 26)\nmacd_sig  = ta.ema(macd_line, 9)\nbb_mid = ta.sma(close, mid_len)\nbb_upper = bb_mid + 2 * ta.stdev(close, mid_len)\nbuy_signal  = ta.crossover(macd_line, macd_sig) and close < bb_mid\nsell_signal = ta.crossunder(macd_line, macd_sig) or close > bb_upper`
    : base === "volume_rsi"
    ? `vol_ma = ta.sma(volume, mid_len)\nbuy_signal  = rsi_val < buy_th and volume > vol_ma\nsell_signal = rsi_val > sell_th`
    : base === "triple_ma"
    ? `ma_long = ta.sma(close, mid_len * 2)\nbuy_signal  = ta.crossover(ma_short, ma_mid) and ma_mid > ma_long\nsell_signal = ta.crossunder(ma_short, ma_mid) or ma_mid < ma_long`
    : `buy_signal  = ta.crossover(ma_short, ma_mid) and rsi_val < buy_th\nsell_signal = ta.crossunder(ma_short, ma_mid) or rsi_val > sell_th`;

  return `//@version=6
indicator(${JSON.stringify(name)}, overlay=true)

// 파라미터
short_len = input.int(${short}, "단기 MA 기간", minval=1)
mid_len   = input.int(${mid},   "중기 MA 기간", minval=1)
rsi_len   = input.int(${rsiLen}, "RSI 기간", minval=1)
buy_th    = input.int(${buyTh}, "매수 RSI 임계값")
sell_th   = input.int(${100 - Number(buyTh)}, "매도 RSI 임계값")

// 인디케이터 계산
ma_short = ta.sma(close, short_len)
ma_mid   = ta.sma(close, mid_len)
rsi_val  = ta.rsi(close, rsi_len)

// 매매 신호 (${base})
${signalCode}

// 시각화
plot(ma_short, color=color.blue,  title="MA Short")
plot(ma_mid,   color=color.orange, title="MA Mid")
plotshape(buy_signal,  style=shape.triangleup,   location=location.belowbar, color=color.green, size=size.small, title="Buy")
plotshape(sell_signal, style=shape.triangledown, location=location.abovebar, color=color.red,   size=size.small, title="Sell")`;
}

function generatePythonCode() {
  const name   = document.getElementById("ci-name").value || "MyIndicator";
  const base   = document.getElementById("ci-base").value || "rsi_ma";
  const short  = document.getElementById("ci-short").value || 5;
  const mid    = document.getElementById("ci-mid").value || 20;
  const rsiLen = document.getElementById("ci-rsi").value || 14;
  const buyTh  = document.getElementById("ci-buy-th").value || 35;
  const pySignal = base === "macd_bb"
    ? `    macd = ta.macd(df["Close"])["MACD_12_26_9"]\n    macd_sig = ta.macd(df["Close"])["MACDs_12_26_9"]\n    bb = ta.bbands(df["Close"], length=${mid})\n    df["buy_signal"] = (macd > macd_sig) & (macd.shift(1) <= macd_sig.shift(1)) & (df["Close"] < bb.iloc[:, 1])\n    df["sell_signal"] = (macd < macd_sig) & (macd.shift(1) >= macd_sig.shift(1))`
    : base === "volume_rsi"
    ? `    vol_ma = df["Volume"].rolling(${mid}).mean()\n    df["buy_signal"] = (df["rsi"] < ${buyTh}) & (df["Volume"] > vol_ma)\n    df["sell_signal"] = df["rsi"] > ${100 - parseInt(buyTh)}`
    : base === "triple_ma"
    ? `    ma_long = ta.sma(df["Close"], length=${parseInt(mid) * 2})\n    df["buy_signal"] = df["golden_cross"] & (df["ma_mid"] > ma_long)\n    df["sell_signal"] = df["dead_cross"] | (df["ma_mid"] < ma_long)`
    : `    df["buy_signal"]  = df["golden_cross"] & (df["rsi"] < ${buyTh})\n    df["sell_signal"] = df["dead_cross"]   | (df["rsi"] > ${100 - parseInt(buyTh)})`;

  return `import pandas as pd
import pandas_ta as ta

def ${name.replace(/[^a-zA-Z0-9_]/g,"_")}(df: pd.DataFrame) -> pd.DataFrame:
    """커스텀 인디케이터: ${name}"""
    df["ma_short"] = ta.sma(df["Close"], length=${short})
    df["ma_mid"]   = ta.sma(df["Close"], length=${mid})
    df["rsi"]      = ta.rsi(df["Close"], length=${rsiLen})

    df["golden_cross"] = (df["ma_short"] > df["ma_mid"]) & (df["ma_short"].shift(1) <= df["ma_mid"].shift(1))
    df["dead_cross"]   = (df["ma_short"] < df["ma_mid"]) & (df["ma_short"].shift(1) >= df["ma_mid"].shift(1))

    # 선택 기반: ${base}
${pySignal}
    df["signal"] = "HOLD"
    df.loc[df["buy_signal"],  "signal"] = "BUY"
    df.loc[df["sell_signal"], "signal"] = "SELL"
    return df`;
}

function renderPineCheck() {
  const editor = document.getElementById("ci-pine-code");
  const output = document.getElementById("ci-pine-check-result");
  const { diagnostics, errors } = checkPineV6(editor.value);
  output.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = errors ? `기본 문법 검사: 오류 ${errors}개` : "기본 문법 검사: 발견된 오류 없음 (컴파일 검증 전)";
  summary.style.color = errors ? "var(--red)" : "var(--green)";
  output.append(summary);
  diagnostics.forEach(d => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "block text-left text-xs mt-2";
    button.textContent = `${d.line}행 ${d.column}열: ${d.message}`;
    button.onclick = () => {
      const lines = editor.value.split("\n");
      const start = lines.slice(0, d.line - 1).reduce((n, v) => n + v.length + 1, 0) + d.column - 1;
      editor.focus(); editor.setSelectionRange(start, start + 1);
      editor.scrollTop = Math.max(0, (d.line - 3) * parseFloat(getComputedStyle(editor).lineHeight));
    };
    output.append(button);
  });
}
document.getElementById("ci-check-pine").addEventListener("click", renderPineCheck);
document.getElementById("ci-pine-code").addEventListener("input", () => {
  document.getElementById("ci-pine-check-result").textContent = "코드가 변경되었습니다. 문법 체크를 다시 실행하세요.";
});

document.getElementById("ci-generate-btn").addEventListener("click", () => {
  document.getElementById("ci-pine-code").value = generatePineCode();
  renderPineCheck();
  document.getElementById("ci-python-code").textContent = generatePythonCode();
});
document.getElementById("ci-copy-pine").addEventListener("click", () => {
  navigator.clipboard.writeText(document.getElementById("ci-pine-code").value);
  setToast("PineScript 복사됨", "ok");
});
document.getElementById("ci-copy-py").addEventListener("click", () => {
  navigator.clipboard.writeText(document.getElementById("ci-python-code").textContent);
  setToast("Python 코드 복사됨", "ok");
});
document.getElementById("ci-test-btn").addEventListener("click", async () => {
  const name = document.getElementById("ci-name").value || "MyIndicator";
  const base = document.getElementById("ci-base").value || "rsi_ma";
  const short = parseInt(document.getElementById("ci-short").value || "5");
  const mid = parseInt(document.getElementById("ci-mid").value || "20");
  const rsi = parseInt(document.getElementById("ci-rsi").value || "14");
  const buyTh = parseFloat(document.getElementById("ci-buy-th").value || "35");
  const symbol = document.getElementById("ibt-symbol")?.value || "005930.KS";
  try {
    const q = new URLSearchParams({ symbol, period: "10y", base, short, mid, rsi, buy_th: buyTh, cost_bps: document.getElementById("ci-cost")?.value || "0", slippage_bps: "0" });
    const sl = document.getElementById("ci-sl")?.value, tp = document.getElementById("ci-tp")?.value;
    if (sl) q.set("stop_loss_pct", sl); if (tp) q.set("take_profit_pct", tp);
    const r = await api(`/api/quant/pipeline?${q}`);
    const c = r.costs || {};
    const data = [
      { label:"누적 수익률", value: `${(r.return_total*100 >= 0 ? "+" : "")}${(r.return_total*100).toFixed(1)}%`, color:"var(--green)" },
      { label:tt("샤프 비율","위험 대비 수익 지표. 높을수록 좋음","sharpe"),   value: Number(r.sharpe || 0).toFixed(2), color:"var(--accent)" },
      { label:tt("최대 낙폭","전략 보유 기간 중 최악의 손실 구간(MDD)","mdd"),   value: `${(r.mdd*100).toFixed(1)}%`, color:"var(--red)" },
      { label:tt("승률","수익이 난 거래의 비율","win_rate"),        value: `${Number(r.win_rate_pct || 0).toFixed(1)}%`, color:"var(--text)" },
      { label:tt("비용 차감","수수료+슬리피지 누적 차감(%p)","cost_bps"), value: `-${Number(c.cost_pct || 0).toFixed(2)}%p`, color:"var(--text-mute)" },
      { label:"손절/익절 발동", value: `${c.stop_loss_exits ?? 0}회 / ${c.take_profit_exits ?? 0}회`, color:"var(--text-mute)" },
    ];
    document.getElementById("ci-bt-cards").innerHTML = data.map(d=>`
      <div class="card" style="padding:14px;text-align:center;">
        <div class="text-xs mb-1" style="color:var(--text-mute);">${d.label}</div>
        <div style="font-size:18px;font-weight:700;color:${d.color};">${d.value}</div>
      </div>`).join("");
    document.getElementById("ci-backtest-result").classList.remove("hidden");
    setToast(`${name} 백테스트 완료 (${symbol})`, "ok");
  } catch (e) {
    setToast("백테스트 오류: " + e.message, "error");
  }
});
// 초기 코드 생성
generatePineCode && (() => {
  const pine = generatePineCode();
  const py   = generatePythonCode();
  const pineEl = document.getElementById("ci-pine-code");
  const pyEl   = document.getElementById("ci-python-code");
  if (pineEl) { pineEl.value = pine; renderPineCheck(); }
  if (pyEl)   pyEl.textContent   = py;
})();

// ── 커스텀 인디케이터 저장/불러오기 ──────────────────────────────────
document.getElementById("ci-save-btn")?.addEventListener("click", async () => {
  const body = {
    name:          document.getElementById("ci-name").value || "MyIndicator",
    base:          document.getElementById("ci-base").value || "rsi_ma",
    short_window:  parseInt(document.getElementById("ci-short").value || "5"),
    mid_window:    parseInt(document.getElementById("ci-mid").value || "20"),
    rsi_period:    parseInt(document.getElementById("ci-rsi").value || "14"),
    buy_threshold: parseFloat(document.getElementById("ci-buy-th").value || "35"),
  };
  try {
    await api("/api/custom-indicators", { method: "POST", body });
    setToast(`'${body.name}' 저장 완료`, "ok");
    loadSavedIndicators();
  } catch (e) {
    setToast("저장 오류: " + e.message, "error");
  }
});

const CI_BASE_LABELS = { rsi_ma:"RSI+이동평균", macd_bb:"MACD+볼린저", volume_rsi:"거래량 가중 RSI", triple_ma:"삼중 이동평균" };

async function loadSavedIndicators() {
  const el = document.getElementById("ci-saved-list");
  if (!el) return;
  try {
    const { items } = await api("/api/custom-indicators");
    if (!items.length) { el.innerHTML = "<div style='color:var(--text-mute);'>저장된 인디케이터가 없습니다. 파라미터를 조정한 뒤 '저장'을 눌러보세요.</div>"; return; }
    el.innerHTML = items.map(it => `
      <div class="flex items-center gap-3 rounded-lg px-3 py-2" style="background:var(--surf2);border:1px solid var(--border);">
        <div class="flex-1">
          <span class="font-semibold">${escHtml(it.name)}</span>
          <span class="text-xs ml-2" style="color:var(--text-mute);">${escHtml(CI_BASE_LABELS[it.base] || it.base)} · 단기${it.short_window}/중기${it.mid_window} · RSI${it.rsi_period} · 매수임계값${it.buy_threshold}</span>
        </div>
        <button type="button" class="btn-secondary text-xs ci-apply-btn" data-id="${escHtml(it.id)}">불러오기</button>
        <button type="button" class="btn-secondary text-xs ci-delete-btn" data-id="${escHtml(it.id)}">삭제</button>
      </div>
    `).join("");
    const byId = Object.fromEntries(items.map(it => [it.id, it]));
    el.querySelectorAll(".ci-apply-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const it = byId[btn.dataset.id];
        if (!it) return;
        document.getElementById("ci-name").value = it.name;
        document.getElementById("ci-base").value = it.base;
        document.getElementById("ci-short").value = it.short_window;
        document.getElementById("ci-mid").value = it.mid_window;
        document.getElementById("ci-rsi").value = it.rsi_period;
        document.getElementById("ci-buy-th").value = it.buy_threshold;
        document.getElementById("ci-generate-btn")?.click();
        setToast(`'${it.name}' 불러왔습니다`, "ok");
      });
    });
    el.querySelectorAll(".ci-delete-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/custom-indicators/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
          loadSavedIndicators();
        } catch (e) { setToast(e.message, "error"); }
      });
    });
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);">${escHtml(e.message)}</div>`;
  }
}

// ── 투자 인디케이터: 성과 검증 (Python 백테스트) ───────────────────
async function loadIndicatorBacktest() {
  const sym = document.getElementById("ibt-symbol")?.value;
  const strategy = document.getElementById("ibt-strategy")?.value;
  if (!sym) return;
  try {
    const q = new URLSearchParams({ symbol: sym, period: "10y", strategy, cost_bps: document.getElementById("ibt-cost")?.value || "10", slippage_bps: document.getElementById("ibt-slip")?.value || "0" });
    const sl = document.getElementById("ibt-sl")?.value, tp = document.getElementById("ibt-tp")?.value;
    if (sl) q.set("stop_loss_pct", sl); if (tp) q.set("take_profit_pct", tp);
    const data = await api(`/api/quant/pipeline?${q}`);
    renderIndicatorBacktest(data);
  } catch {}
}

function renderIndicatorBacktest(data) {
  const el = document.getElementById("ibt-result");
  if (!el || !data) return;
  const strategy = document.getElementById("ibt-strategy")?.value || "rsi";
  const strategyLabel = { rsi:"RSI 역추세", ma:"이동평균 교차", bollinger:"볼린저밴드", composite:"복합 인디케이터" }[strategy] || "";
  el.innerHTML = `
    <div class="grid md:grid-cols-4 gap-3">
      ${[
        { l:"전략",       v: strategyLabel, c:"var(--accent)" },
        { l:"누적 수익률", v: `${data?.total_return_pct >= 0 ? "+" : ""}${Number(data?.total_return_pct || 0).toFixed(1)}%`, c:"var(--green)" },
        { l:tt("샤프 비율","위험 대비 수익 지표. 높을수록 좋음","sharpe"),  v: Number(data?.sharpe_ratio || 0).toFixed(2), c:"var(--text)" },
        { l:tt("최대 낙폭","전략 보유 기간 중 최악의 손실 구간(MDD)","mdd"),  v: `${Number(data?.mdd_pct || 0).toFixed(1)}%`, c:"var(--red)" },
      ].map(c=>`<div class="card" style="padding:14px;text-align:center;"><div class="text-xs mb-1" style="color:var(--text-mute);">${c.l}</div><div style="font-size:18px;font-weight:700;color:${c.c};">${c.v}</div></div>`).join("")}
    </div>
    <div class="card">
      <h3 class="font-semibold mb-2 text-sm">📊 Python 백테스트 상세</h3>
      <table><thead><tr><th>비교 대상</th><th style="text-align:right;">누적 수익률</th><th style="text-align:right;">거래 횟수</th><th style="text-align:right;">${tt("승률","수익이 난 거래의 비율","win_rate")}</th></tr></thead>
      <tbody><tr><td>${strategyLabel}</td><td style="text-align:right;color:${Number(data.total_return_pct)>=0?"var(--green)":"var(--red)"};">${Number(data.total_return_pct)>=0?"+":""}${Number(data.total_return_pct).toFixed(2)}%</td><td style="text-align:right;">${data.trade_count}회</td><td style="text-align:right;">${Number(data.win_rate_pct).toFixed(1)}%</td></tr><tr><td>매수 후 보유</td><td style="text-align:right;">${Number(data.buy_hold_return_pct).toFixed(2)}%</td><td style="text-align:right;">-</td><td style="text-align:right;">-</td></tr></tbody></table>
      <p class="text-xs mt-2" style="color:var(--text-mute);">비용 차감 전 수익률 ${Number(data.gross_return_pct ?? data.total_return_pct).toFixed(1)}% → 수수료 ${data.cost_bps ?? 0}bp + 슬리피지 ${data.slippage_bps ?? 0}bp 로 누적 ${Number(data.cost_pct || 0).toFixed(2)}%p 차감 · 손절 ${data.stop_loss_pct ? data.stop_loss_pct + "% (" + data.stop_loss_exits + "회 발동)" : "미적용"} · 익절 ${data.take_profit_pct ? data.take_profit_pct + "% (" + data.take_profit_exits + "회 발동)" : "미적용"} · 신호는 다음 거래일 수익률에 적용됩니다.</p>
      ${data.explanation ? renderXaiBlock(data.explanation) : ""}
      <button type="button" class="btn-secondary text-xs mt-2" onclick='compareTrayAdd(${JSON.stringify({
        source: "성과 검증 (Python)", label: `${strategyLabel}`,
        summary: `수익률 ${Number(data.total_return_pct)>=0?"+":""}${Number(data.total_return_pct).toFixed(1)}% · 샤프 ${Number(data.sharpe_ratio||0).toFixed(2)} · MDD ${Number(data.mdd_pct||0).toFixed(1)}% · 승률 ${Number(data.win_rate_pct||0).toFixed(1)}%`,
      }).replace(/'/g, "&#39;")}')">⚖️ 비교에 추가</button>
    </div>`;
  renderCompareTrayAll();
}

document.getElementById("ibt-run-btn")?.addEventListener("click", loadIndicatorBacktest);

// ── 투자 인디케이터: 증권사 API 자동화 ──────────────────────────────
async function loadIndicatorApiSettings() {
  try {
    const data = await api("/api/broker/settings");
    if (data?.broker) {
      const t = document.getElementById("iapi-broker-type");
      if (t) t.value = data.broker;
    }
    const status = document.getElementById("iapi-status");
    if (status) status.textContent = data?.connected ? "✅ 연결됨" : "연결되지 않음";
  } catch {}
}

document.getElementById("iapi-save-btn")?.addEventListener("click", async () => {
  const body = {
    broker: document.getElementById("iapi-broker-type").value,
    app_key:     document.getElementById("iapi-app-key").value,
    app_secret:  document.getElementById("iapi-app-secret").value,
    account_no:  document.getElementById("iapi-account").value,
    paper: document.getElementById("iapi-paper").checked,
  };
  try {
    await api("/api/broker/settings", { method:"POST", body });
    setToast("API 설정 저장됨", "ok");
    document.getElementById("iapi-status").textContent = "✅ 저장 완료";
  } catch(e) { setToast(e.message, "error"); }
});
document.getElementById("iapi-test-btn")?.addEventListener("click", async () => {
  try {
    const r = await api("/api/broker/test");
    setToast(r.message || "연결 테스트 성공", "ok");
  } catch(e) { setToast(e.message || "연결 실패", "error"); }
});


export { loadIndicatorApiSettings, loadIndicatorBacktest, loadSavedIndicators };
