import { escHtml } from '/js/common.js';
let charts = [];
const ids = ['co-revenue-chart', 'co-income-chart', 'co-structure-chart', 'co-margin-chart'];
const number = (value, suffix = '') => Number.isFinite(value) ? value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) + suffix : 'N/A';
export function clearCompanyWorkspace(message = '데이터 불러오는 중…') {
  charts.forEach(chart => chart.destroy()); charts = [];
  ['co-identity', 'co-snapshot', 'co-chart-source'].forEach(id => { document.getElementById(id).textContent = ''; });
  ids.forEach(id => { document.getElementById(id).innerHTML = `<div class="co-empty">${escHtml(message)}</div>`; });
  ['co-valuation', 'co-profitability', 'co-quarterly', 'co-balance'].forEach(id => { document.getElementById(id).textContent = ''; });
}
export function renderCompanyWorkspace(d) {
  charts.forEach(chart => chart.destroy()); charts = [];
  const unit = d.amountUnit || '단위 미확인';
  const selected = document.querySelector('#company-tabs .co-tab.active');
  const symbol = selected?.dataset.symbol || d.symbol || '';
  const name = selected?.querySelector('.co-tab-btn')?.textContent || d.name || symbol;
  document.getElementById('co-identity').innerHTML = `<div class="co-identity-title"><strong>${escHtml(name)}</strong><span class="co-price">${number(d.price, d.priceUnit || '')}</span><span style="font-size:13px;color:${d.chg == null ? '#64748b' : d.chg >= 0 ? '#dc5252' : '#397be8'}">${Number.isFinite(d.chg) ? `${d.chg > 0 ? '+' : ''}${number(d.chg)}%` : '등락 미확인'}</span></div><div class="co-identity-meta">${escHtml(symbol)} · ${escHtml(d.sector || '업종 미확인')} · ${escHtml(d.currency || d.priceUnit || '통화 미확인')}<br>출처 ${escHtml(d.source || '미기록')} · ${escHtml(({ complete: '수집 완료', partial: '일부 데이터 누락', stale: '이전 수집 자료' })[d.data_status] || d.data_status || '제공처 기준')}${d.cached_at ? ` · 저장 ${escHtml(d.cached_at)}` : ''}</div>`;
  const snapshot = [['PER', number(d.per, '배')], ['PBR', number(d.pbr, '배')], ['ROE', number(d.roe, '%')], ['영업이익률', number(d.opMargin, '%')], ['배당수익률', number(d.divYield, '%')], ['재무 단위', unit]];
  document.getElementById('co-snapshot').innerHTML = snapshot.map(([key, value]) => `<div class="co-snapshot-row"><span>${escHtml(key)}</span><b>${escHtml(value)}</b></div>`).join('');
  document.getElementById('co-chart-source').textContent = `실적: ${d.source || '출처 미기록'} · 재무구조: ${d.balanceSource || d.source || '출처 미기록'} · 수익성: ${d.profitabilitySource || '출처 미기록'} · 금액 단위: ${unit}. 분기는 제공처 라벨을 사용하며, DART 영업이익은 아래 표에 별도 기준으로 표시합니다. 누락값은 N/A이며 0으로 대체하지 않습니다. 사업부문별 매출 데이터는 현재 제공하지 않습니다.`;
  function draw(id, options, available) {
    const el = document.getElementById(id); el.innerHTML = '';
    if (!available || !window.ApexCharts) { el.innerHTML = `<div class="co-empty">${!window.ApexCharts ? '차트 라이브러리를 불러오지 못했습니다.' : '제공된 데이터가 없습니다. 아래 원자료를 확인하세요.'}</div>`; return; }
    const chart = new ApexCharts(el, {
      chart: { type: 'bar', height: 245, fontFamily: 'Pretendard Variable, Pretendard, sans-serif', background: 'transparent', toolbar: { show: false }, animations: { enabled: false } },
      colors: ['#7165dc', '#29b8b3', '#ee919d'],
      dataLabels: { enabled: false }, legend: { position: 'bottom', fontSize: '10px' },
      grid: { borderColor: '#e6eaf1', strokeDashArray: 3 },
      plotOptions: { bar: { borderRadius: 3, columnWidth: '46%' } },
      xaxis: { labels: { style: { fontSize: '10px', colors: '#64748b' } } },
      yaxis: { labels: { formatter: v => number(v), style: { fontSize: '10px', colors: '#64748b' } } },
      tooltip: { theme: 'light', y: { formatter: v => number(v, ` ${unit}`) } },
      noData: { text: '제공된 데이터가 없습니다.' }, ...options,
    });
    charts.push(chart);
    chart.render().catch(() => { el.innerHTML = '<div class="co-empty">차트 표시 실패 · 아래 원자료를 확인하세요.</div>'; });
  }
  const qs = d.quarters || [];
  const values = array => qs.map((_, i) => Number.isFinite(array?.[i]) ? array[i] : null);
  const revenue = values(d.revenue), op = values(d.op), net = values(d.net);
  document.querySelector('#co-revenue-chart').previousElementSibling.textContent = `매출액 추이 (${unit})`;
  document.querySelector('#co-income-chart').previousElementSibling.textContent = `영업이익·순이익 추이 (${unit})${op.some(Number.isFinite) ? '' : ' · 영업이익은 하단 DART 표 참조'}`;
  draw('co-revenue-chart', { series: [{ name: '매출액', data: revenue }], xaxis: { categories: qs, labels: { style: { fontSize: '10px' } } } }, revenue.some(Number.isFinite));
  draw('co-income-chart', { series: [{ name: '영업이익', data: op }, { name: '순이익', data: net }], xaxis: { categories: qs, labels: { style: { fontSize: '10px' } } } }, [...op, ...net].some(Number.isFinite));
  const parts = [d.equity, d.liabilities];
  const validParts = parts.every(v => Number.isFinite(v) && v >= 0) && parts.some(v => v > 0);
  draw('co-structure-chart', { chart: { type: 'donut', height: 245, fontFamily: 'Pretendard Variable, Pretendard, sans-serif', animations: { enabled: false } }, series: parts, labels: ['자기자본', '부채총계'], stroke: { width: 3, colors: ['#f7f8fc'] }, plotOptions: { pie: { donut: { size: '65%', labels: { show: true, total: { show: true, label: `자본 + 부채 (${unit})`, fontSize: '11px', formatter: () => number(parts[0] + parts[1]) }, value: { formatter: v => number(Number(v)) } } } } } }, validParts);
  const margins = [['ROE', d.roe], ['ROA', d.roa], ['영업이익률', d.opMargin], ['순이익률', d.netMargin]].filter(([, v]) => Number.isFinite(v));
  draw('co-margin-chart', { series: [{ name: '수익성', data: margins.map(([, v]) => v) }], plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '40%' } }, xaxis: { categories: margins.map(([name]) => name), labels: { formatter: v => number(Number(v), '%'), style: { fontSize: '10px' } } }, yaxis: { labels: { style: { fontSize: '11px' } } }, tooltip: { y: { formatter: v => number(v, '%') } } }, margins.length > 0);
}
document.querySelectorAll('[data-co-target]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-co-target]').forEach(other => other.classList.toggle('active', other === button));
  document.getElementById(button.dataset.coTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}));
