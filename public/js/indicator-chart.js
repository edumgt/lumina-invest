let chart, payload, label;
const el = id => document.getElementById(id);
const selected = key => el(`ind-${key}`)?.checked;
export function clearIndicatorChart(message) {
  chart?.remove(); chart = null; payload = null;
  el('ind-chart-status').textContent = message;
  el('ind-chart-values').textContent = '';
}
function ema(values, period) {
  let value = values[0];
  return values.map(x => (value += (x - value) * 2 / (period + 1)));
}
export function renderIndicatorChart(data, name) {
  clearIndicatorChart('차트를 그리는 중…');
  payload = data; label = name;
  const L = window.LightweightCharts;
  if (!L) { el('ind-chart-status').textContent = '차트 라이브러리를 불러오지 못했습니다. 새로고침하세요.'; return; }
  const valid = new Map();
  data.times.forEach((time, i) => {
    const stamp = typeof time === "number" ? new Date(time * 1000) : new Date(time);
    const date = Number.isFinite(stamp.getTime()) ? stamp.toISOString().slice(0, 10) : "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(data.closes[i])) valid.set(date, i);
  });
  const rows = [...valid].sort(([a], [b]) => a.localeCompare(b));
  if (!rows.length) { clearIndicatorChart('표시할 일봉 데이터가 없습니다.'); return; }
  const css = getComputedStyle(document.documentElement);
  chart = L.createChart(el('ind-tv-chart'), {
    autoSize: true, height: 560,
    layout: { background: { color: css.getPropertyValue('--surf').trim() || '#111827' }, textColor: css.getPropertyValue('--text-dim').trim() || '#b9c5d5', panes: { separatorColor: '#334155' } },
    grid: { vertLines: { color: '#33415540' }, horzLines: { color: '#33415540' } },
    localization: { locale: 'ko-KR' }, timeScale: { borderColor: '#334155' },
  });
  const series = [];
  function line(title, values, color, pane = 0) {
    const s = chart.addSeries(L.LineSeries, { title, color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true }, pane);
    s.setData(rows.map(([time, i]) => values[i] != null && Number.isFinite(values[i]) ? { time, value: values[i] } : { time }));
    series.push([title, s]); return s;
  }
  const price = line('종가', data.closes, css.getPropertyValue('--text-dim').trim() || '#334155');
  for (const [key, title, color] of [['ma5','MA5','#38bdf8'],['ma20','MA20','#f59e0b'],['ma60','MA60','#a78bfa']]) {
    if (selected(key)) line(title, data[key], color);
  }
  if (selected('bb')) {
    line('BB 상단', data.bb_upper, '#2dd4bf'); line('BB 중심', data.bb_mid, '#94a3b8'); line('BB 하단', data.bb_lower, '#2dd4bf');
  }
  let pane = 1;
  if (selected('rsi')) {
    const rsi = line('RSI14', data.rsi, '#a78bfa', pane++);
    for (const value of [30,70]) rsi.createPriceLine({ price: value, color: '#94a3b8', lineWidth: 1, lineStyle: L.LineStyle.Dashed, axisLabelVisible: true, title: value === 30 ? '과매도' : '과매수' });
  }
  if (selected('macd')) {
    // Warm up from the available API window; show its limitation alongside the chart.
    const fast = ema(data.closes, 12), slow = ema(data.closes, 26);
    const macd = fast.map((v,i) => v - slow[i]), signal = ema(macd,9);
    const warmed = values => values.map((v,i) => i < 34 ? null : v);
    line('MACD12/26', warmed(macd), '#38bdf8', pane);
    line('Signal9', warmed(signal), '#fb923c', pane);
  }
  if (selected('ma5') && selected('ma20')) {
    const markers = [];
    rows.forEach(([time,i]) => {
      if (!i || [data.ma5[i],data.ma20[i],data.ma5[i-1],data.ma20[i-1]].some(v => v == null)) return;
      const before = data.ma5[i-1] - data.ma20[i-1], after = data.ma5[i] - data.ma20[i];
      if (before <= 0 && after > 0) markers.push({ time, position: 'belowBar', color: '#22c55e', shape: 'arrowUp', text: '골든' });
      if (before >= 0 && after < 0) markers.push({ time, position: 'aboveBar', color: '#f87171', shape: 'arrowDown', text: '데드' });
    });
    L.createSeriesMarkers(price, markers);
  }
  chart.subscribeCrosshairMove(event => {
    if (!event.time) return;
    const time = typeof event.time === 'object' ? `${event.time.year}-${String(event.time.month).padStart(2,'0')}-${String(event.time.day).padStart(2,'0')}` : event.time;
    el('ind-chart-values').textContent = [time, ...series.map(([title,s]) => { const value = event.seriesData.get(s)?.value; return value == null ? null : `${title} ${value.toLocaleString('ko-KR',{maximumFractionDigits:2})}`; }).filter(Boolean)].join(' · ');
  });
  chart.timeScale().fitContent();
  el('ind-chart-status').textContent = `${name} · 일봉 ${rows.length}개 (${rows[0][0]} ~ ${rows.at(-1)[0]}) · 출처 ${data.source || 'API 미제공'} · 조회 ${new Date().toLocaleString('ko-KR')} · 요청 기간 중 API 제공 최근 최대 100봉${selected('macd') ? ' · MACD는 표시 구간에서 계산, 초기 34봉 제외' : ''}`;
}
for (const key of ['ma5','ma20','ma60','rsi','macd','bb']) el(`ind-${key}`)?.addEventListener('change', () => { if (payload) renderIndicatorChart(payload, label); });
for (const key of ['symbol','period']) el(`ind-${key}`)?.addEventListener('change', () => {
  clearIndicatorChart('선택이 바뀌었습니다. 전략 분석을 실행해 새 데이터를 적용하세요.');
  el('ind-strategy-result').classList.add('hidden');
  el('ind-trade-btn').disabled = true;
});
