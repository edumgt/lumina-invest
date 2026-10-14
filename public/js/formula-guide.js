const $ = id => document.getElementById(id);
export const functionHelp = {
  sma: ['일정 기간의 평균 가격', '최근 종가가 90·100·110원이면 3일 평균은 100원입니다.'],
  ema: ['최근 가격에 더 큰 비중을 둔 평균', '기간을 짧게 잡으면 최근 가격 변화에 더 빠르게 반응합니다.'],
  rsi: ['최근 상승과 하락의 강도 (0~100)', 'RSI가 28이고 매수 조건이 RSI < 30이면 조건을 만족합니다.'],
  macd: ['단기와 장기 평균의 차이', 'MACD가 신호선보다 크면 상승 방향이 상대적으로 강한 상태로 봅니다.'],
  bb_upper: ['평균 가격 위·아래의 변동 범위', '종가 95원, 하단 밴드 96원이면 close < bb_lower(...) 조건을 만족합니다.'],
  atr: ['하루 가격 변동 폭의 평균', 'ATR 2원 ÷ 종가 100원 = 0.02, 가격 대비 평균 변동 폭 2%입니다.'],
  std: ['가격이 평균에서 얼마나 흩어져 있는지', '값이 클수록 해당 기간의 가격 차이가 큽니다.'],
  highest: ['기간 중 가장 높은 값·낮은 값', '오늘 105원, 전일까지 20일 최고가 100원이면 신고가 돌파 조건을 만족합니다.'],
  shift: ['이전 거래일의 값', '오늘 종가 105원, 어제 100원이면 shift(close, 1)은 100원입니다.'],
  change: ['이전 가격과의 차이·변화율', '100원에서 105원으로 오르면 차이는 5원, 변화율은 0.05(5%)입니다.'],
  zscore: ['평균에서 얼마나 떨어져 있는지', '평균 100원, 표준편차 2원, 오늘 95원이면 (95−100)÷2 = −2.5입니다.'],
  normalize: ['최근 최솟값·최댓값 사이의 위치 (0~1)', '최소 100·최대 200·현재 150이면 위치는 0.5입니다.'],
  rank: ['최근 값들과 비교한 상대적인 순위 (0~1)', '1에 가까울수록 최근 구간에서 높은 값에 해당합니다.'],
  crossover: ['두 선의 순서가 바뀌는 날', '어제 단기선이 장기선 아래, 오늘 위로 올라오면 상향 교차가 참입니다.'],
  where: ['조건에 따라 다른 값 사용', 'RSI < 30이면 1, 아니면 0을 표시할 수 있습니다.'],
  abs: ['숫자의 크기·범위를 다듬기', 'clip(4, −3, 3)은 3입니다. 지표가 정한 범위를 넘지 않게 제한합니다.'],
  typical: ['고가·저가·종가의 평균', '고가 110·저가 90·종가 100원이면 typical()은 100원입니다.'],
  vwap: ['거래량을 가중치로 둔 평균 가격', '거래가 많이 된 날의 가격이 평균에 더 크게 반영됩니다.'],
  obv: ['가격 방향에 따라 거래량 누적', '종가가 오르면 거래량을 더하고, 내리면 뺍니다. 누적 흐름을 관찰합니다.'],
  barssince: ['조건이 마지막으로 충족된 뒤 지난 거래일 수', '오늘 교차하면 0, 그 후 다음 거래일에는 1입니다.'],
};
export function initFormulaGuide(apply) {
  function definition() {
    const kind = $('fx-easy-kind').value, n = Number($('fx-easy-n').value);
    const a = Number($('fx-easy-a').value), b = Number($('fx-easy-b').value);
    if (!Number.isInteger(n) || n < 2 || n > 250) throw Error('계산 기간은 2~250 사이의 정수로 입력하세요.');
    if (!$('fx-easy-a').value || (kind !== 'cross' && !$('fx-easy-b').value)) throw Error('기준값을 입력하세요.');
    if (kind === 'cross') {
      if (!Number.isInteger(a) || a <= n || a > 500) throw Error('장기 기간은 단기 기간보다 큰 정수로, 최대 500일까지 입력하세요.');
      return { name: `${n}일·${a}일 평균선 교차`, description: `단기 평균선이 장기 평균선을 위로 통과하면 매수, 아래로 통과하면 매도`, indicator_expr: `sma(close, ${n}) - sma(close, ${a})`, buy_expr: `crossover(sma(close, ${n}), sma(close, ${a}))`, sell_expr: `crossunder(sma(close, ${n}), sma(close, ${a}))`, params: {}, example: '가정: 어제 단기 평균 99원·장기 평균 100원, 오늘 단기 102원·장기 101원이면 매수 조건을 만족합니다. 단순히 위에 있는 모든 날이 아니라 교차한 날을 찾습니다.' };
    }
    if (!Number.isFinite(a) || !Number.isFinite(b) || a >= b) throw Error('매수 기준은 매도 기준보다 작은 숫자로 입력하세요.');
    if (kind === 'rsi') {
      if (a < 0 || b > 100) throw Error('RSI 기준은 0~100 사이로 입력하세요.');
      const expr = `rsi(close, ${n})`;
      return { name: `RSI ${n}일 조건`, description: `${n}일 RSI가 ${a} 미만이면 매수 조건, ${b} 초과이면 매도 조건을 만족합니다.`, indicator_expr: expr, buy_expr: `${expr} < ${a}`, sell_expr: `${expr} > ${b}`, params: {}, example: `가정: RSI ${a}는 매수 기준과 같으므로 미충족, ${a - 1}은 매수 조건 충족입니다. 낮은 RSI는 최근 하락 강도가 컸다는 뜻이며 반등을 보장하지 않습니다.` };
    }
    const expr = `(close / sma(close, ${n}) - 1) * 100`;
    return { name: `${n}일 평균 대비 가격 차이`, description: `종가와 ${n}일 평균의 차이가 ${a}% 미만이면 매수 조건, ${b}% 초과이면 매도 조건을 만족합니다.`, indicator_expr: expr, buy_expr: `${expr} < ${a}`, sell_expr: `${expr} > ${b}`, params: {}, example: '가정: 종가 95원·평균 100원이면 (95÷100−1)×100 = −5%입니다. 매수 기준이 −4%이면 충족, −5%이면 미충족입니다.' };
  }
  function render() {
    try { const d = definition(); $('fx-easy-summary').textContent = d.description; $('fx-easy-example').textContent = d.example; $('fx-easy-apply').disabled = false; }
    catch(e) { $('fx-easy-summary').textContent = e.message; $('fx-easy-example').textContent = ''; $('fx-easy-apply').disabled = true; }
  }
  $('fx-easy-kind').addEventListener('change', () => {
    const kind = $('fx-easy-kind').value;
    $('fx-easy-n').value = kind === 'rsi' ? 14 : kind === 'cross' ? 5 : 20;
    $('fx-easy-a').value = kind === 'rsi' ? 30 : kind === 'cross' ? 20 : -5;
    $('fx-easy-b').value = kind === 'rsi' ? 70 : kind === 'cross' ? '' : 5;
    $('fx-easy-b').disabled = kind === 'cross';
    $('fx-easy-n-label').textContent = kind === 'cross' ? '단기 기간 (거래일)' : '계산 기간 (거래일)';
    $('fx-easy-a-label').textContent = kind === 'cross' ? '장기 기간 (거래일)' : kind === 'gap' ? '매수 기준 (% 미만)' : '매수 기준 (미만)';
    $('fx-easy-b-label').textContent = kind === 'cross' ? '매도는 하향 교차로 자동 설정' : kind === 'gap' ? '매도 기준 (% 초과)' : '매도 기준 (초과)';
    render();
  });
  for (const id of ['fx-easy-n','fx-easy-a','fx-easy-b']) $(id).addEventListener('input', render);
  $('fx-easy-apply').addEventListener('click', () => { try { apply(definition()); } catch(e) { $('fx-easy-summary').textContent = e.message; } });
  render();
}
