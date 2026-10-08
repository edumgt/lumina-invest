import { escHtml } from '/js/common.js';
const value = v => Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 4 });
const valid = v => typeof v === 'number' && Number.isFinite(v);
export function renderFormulaChartHelp(result) {
  let box = document.getElementById('fx-chart-help');
  if (!box) {
    box = document.createElement('section'); box.id = 'fx-chart-help';
    box.className = 'text-xs mt-4'; box.style.cssText = 'padding:16px;border:1px solid var(--border);border-radius:10px;background:var(--surf2);line-height:1.8;overflow-wrap:anywhere;';
    box.setAttribute('aria-label', '계산 결과 그래프 설명');
    document.getElementById('fx-chart').after(box);
  }
  box.hidden = false;
  const p = result.payload || result, s = p.series;
  if (!s?.times?.length) { box.textContent = '표시할 시계열 데이터가 없어 그래프를 해석할 수 없습니다.'; return; }
  const points = s.indicator.map((v,i) => ({v,date:s.times[i]})).filter(x => valid(x.v));
  const first = points[0], last = points.at(-1);
  const delta = first && last ? last.v - first.v : null;
  const missing = s.indicator.filter(v => !valid(v)).length;
  const signal = result.latest_signal || p.latest_signal || 'HOLD';
  const signals = { BUY: 'BUY: 마지막 봉에서 매수 조건이 참입니다. 신규 진입 여부는 기존 보유 상태에 따라 달라집니다.', SELL: 'SELL: 마지막 봉에서 매도 조건이 참입니다.', HOLD_LONG: 'HOLD_LONG: 새 매수·매도 조건 없이 산식 기준 보유 상태가 이어집니다.', HOLD: 'HOLD: 현재 매수·매도 조건이 없거나 매수 조건을 설정하지 않은 상태입니다.' };
  const hasPosition = Array.isArray(s.position);
  const summary = last ? `표시 구간의 마지막 유효 지표값은 ${escHtml(last.date)}의 <b>${value(last.v)}</b>입니다. ${points.length > 1 ? `첫 유효값 ${value(first.v)} 대비 ${delta > 0 ? '상승' : delta < 0 ? '하락' : '동일'}${delta ? ` (${delta > 0 ? '+' : ''}${value(delta)})` : ''}했습니다.` : '유효값이 하나라 변화 방향을 비교할 수 없습니다.'}` : '표시 구간에 유효한 지표값이 없습니다.';
  box.innerHTML = `<h3 class="text-sm font-semibold mb-2">그래프 읽는 법</h3>
    <p>${summary} 값이 높거나 상승한다는 사실만으로 매수 신호를 뜻하지 않으며, 의미와 단위는 입력한 산식에 따라 달라집니다.</p>
    <ul class="list-disc ml-5 mt-2" style="color:var(--text-dim);">
      <li><b>가로축:</b> 봉의 날짜입니다. 차트는 ${escHtml(s.times[0])}부터 ${escHtml(s.times.at(-1))}까지 최근 ${s.times.length}개 봉을 표시합니다. 요약 성과는 전체 계산 ${result.rows ?? p.rows ?? '-'}개 봉 기준이므로 차트 구간과 다를 수 있습니다.</li>
      <li><b style="color:#2962ff;">파란 선 · 왼쪽 축:</b> 종가입니다. <b style="color:#f59e0b;">주황 선 · 오른쪽 축:</b> 사용자 산식의 지표값입니다. 서로 다른 눈금을 쓰므로 선 높이와 교차 자체를 매매 신호로 해석하지 않습니다.</li>
      ${hasPosition ? `<li><b style="color:#089981;">초록 영역:</b> 산식 기준 포지션입니다. 1은 보유, 0은 미보유입니다. 실제 주문·체결 기록이 아니며, 백테스트는 신호를 한 봉 뒤에 적용하고 손절·익절도 반영하므로 최종 보유 상태와 다를 수 있습니다.</li>
      <li><b>B / S:</b> 산식 기준 포지션이 0→1 / 1→0으로 전환된 날짜입니다. 조건이 참인 모든 날을 표시하지 않으며, 각각 최근 40개 전환만 표시합니다. 현재 표시 구간의 전환은 B ${s.buy_dates?.length || 0}개, S ${s.sell_dates?.length || 0}개입니다.</li>` : '<li><b>지표만 계산:</b> 매수 조건이 없어 포지션 영역과 B/S 전환 표시, 전략 백테스트가 없습니다.</li>'}
      <li><b>최신 신호:</b> ${escHtml(signals[signal] || signal)} 지표값의 높낮이와는 별도로 조건식으로 결정됩니다.</li>
      <li><b>빈 구간:</b> 이동평균 등의 초기 계산 기간 또는 유효하지 않은 계산값은 비워 둡니다. 표시 구간에 ${missing}개가 있으며 0을 뜻하지 않습니다.</li>
      <li><b>조작:</b> 날짜 위에 마우스를 올리면 종가·지표값을 함께 확인할 수 있고, 범례를 누르면 해당 시리즈를 숨기거나 표시합니다.</li>
    </ul>
    <p class="mt-2" style="color:var(--text-mute);">${p.backtest?.total_return_pct != null ? '누적 수익률은 설정한 수수료·슬리피지와 손절·익절을 반영한 백테스트 결과입니다. 매수후보유는 종목을 계속 보유한 비교 기준이며, MDD는 누적 자산의 고점 대비 최대 하락률입니다.' : '평균·표준편차·최소·최대 카드는 최근 최대 252개의 유효 지표값 기준입니다.'}</p>`;
}
